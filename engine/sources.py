"""
Data source adapters. Every value that enters the engine arrives through here
and carries (value, date, source, source_url, fetched_at).

Network layer is subprocess-curl first, urllib second. This is deliberate:
inside macOS cron's sandbox Python's urllib SSL handshake reliably times out,
while curl works. In GitHub Actions both work, so curl-first costs nothing.
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import config

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) fiscal-dominance-monitor/1.0"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_API = "https://api.stlouisfed.org/fred/series/observations"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
COINGECKO = "https://api.coingecko.com/api/v3"


class FetchError(RuntimeError):
    pass


def _http_get(url: str, timeout: int = 25, retries: int = 3,
              send_ua: bool = False) -> str:
    """
    curl first, urllib fallback, exponential backoff.

    send_ua matters and the two sources want OPPOSITE things — measured, not guessed:
      * FRED  : a browser User-Agent makes it hang forever (20s timeout, 0 bytes).
                Send curl's default UA.  send_ua=False
      * Yahoo : no browser User-Agent gets you "Edge: Too Many Requests".
                send_ua=True
    Also --http1.1 everywhere: FRED intermittently kills HTTP/2 streams
    (curl rc=92 INTERNAL_ERROR), and --compressed makes that worse.
    Do not "clean up" any of this without re-measuring.
    """
    last = None
    proxy = os.environ.get("FDM_PROXY", "").strip()
    for attempt in range(retries):
        try:
            cmd = ["curl", "-sSL", "--http1.1", "--max-time", str(timeout)]
            if send_ua:
                cmd += ["-H", f"User-Agent: {UA}"]
            if proxy:
                cmd += ["-x", proxy]
            cmd.append(url)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
            last = f"curl rc={r.returncode} stderr={r.stderr[:200]}"
        except Exception as exc:                       # noqa: BLE001
            last = f"curl exc: {exc}"
        try:
            hdrs = {"User-Agent": UA} if send_ua else {}
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:                       # noqa: BLE001
            last = f"urllib exc: {exc}"
        if attempt < retries - 1:
            time.sleep(1.0 * (2 ** attempt))
    raise FetchError(f"GET failed after {retries} tries: {url} :: {last}")


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.csv"


def _write_cache(key: str, rows: list[tuple[str, float]]) -> None:
    with _cache_path(key).open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "value"])
        w.writerows(rows)


def _read_cache(key: str) -> list[tuple[str, float]]:
    p = _cache_path(key)
    if not p.exists():
        return []
    out = []
    with p.open() as fh:
        for row in csv.DictReader(fh):
            try:
                out.append((row["date"], float(row["value"])))
            except (ValueError, KeyError):
                continue
    return out


# ---------------------------------------------------------------- FRED ----
def fetch_fred(series_id: str, start: str = config.HISTORY_START,
               use_cache_on_fail: bool = True) -> dict[str, Any]:
    """
    Keyless fredgraph.csv is the default path — it means the user needs zero
    API keys. If FRED_API_KEY is set we use the JSON API instead (higher rate
    limits, better error messages).
    """
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if api_key:
        url = (f"{FRED_API}?series_id={series_id}&api_key={api_key}"
               f"&file_type=json&observation_start={start}")
        public_url = f"https://fred.stlouisfed.org/series/{series_id}"
        try:
            payload = json.loads(_http_get(url, send_ua=False))
            rows = [(o["date"], float(o["value"]))
                    for o in payload.get("observations", [])
                    if o.get("value") not in (".", "", None)]
        except Exception as exc:                        # noqa: BLE001
            rows = []
            if not use_cache_on_fail:
                raise FetchError(f"FRED api {series_id}: {exc}") from exc
    else:
        url = f"{FRED_CSV}?id={series_id}&cosd={start}"
        public_url = f"https://fred.stlouisfed.org/series/{series_id}"
        try:
            text = _http_get(url, send_ua=False)
            rows = []
            for row in csv.DictReader(io.StringIO(text)):
                dt = row.get("observation_date") or row.get("DATE") or row.get("date")
                raw = row.get(series_id) or row.get(series_id.upper())
                if dt is None or raw in (".", "", None):
                    continue
                try:
                    rows.append((dt, float(raw)))
                except ValueError:
                    continue
        except Exception as exc:                        # noqa: BLE001
            rows = []
            if not use_cache_on_fail:
                raise FetchError(f"FRED csv {series_id}: {exc}") from exc

    stale = False
    if not rows and use_cache_on_fail:
        rows = _read_cache(f"fred_{series_id}")
        stale = bool(rows)
    if rows and not stale:
        _write_cache(f"fred_{series_id}", rows)

    meta = config.FRED_SERIES.get(series_id, {})
    return {
        "series_id": series_id,
        "label": meta.get("label", series_id),
        "freq": meta.get("freq", "d"),
        "release_lag_days": meta.get("release_lag_days", 1),
        "observations": rows,
        "source": "FRED (St. Louis Fed)",
        "source_url": public_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "from_cache": stale,
    }


# --------------------------------------------------------------- Yahoo ----
def fetch_yahoo(symbol: str, range_: str | None = None,
                start: str = config.HISTORY_START,
                use_cache_on_fail: bool = True) -> dict[str, Any]:
    """
    Use period1/period2 unix timestamps, NOT range=max.  range=max silently
    downgrades the interval to monthly (26 years came back as 267 points);
    period1/period2 with interval=1d returns the full ~6000 daily bars.
    """
    key = config.YAHOO_SERIES.get(symbol, {}).get("key", symbol)
    if range_:
        url = f"{YAHOO_CHART}/{symbol}?range={range_}&interval=1d"
    else:
        p1 = int(datetime.strptime(start, "%Y-%m-%d")
                 .replace(tzinfo=timezone.utc).timestamp())
        p2 = int(datetime.now(timezone.utc).timestamp())
        url = f"{YAHOO_CHART}/{symbol}?period1={p1}&period2={p2}&interval=1d"
    rows: list[tuple[str, float]] = []
    stale = False
    try:
        payload = json.loads(_http_get(url, send_ua=True))
        res = payload["chart"]["result"][0]
        stamps = res.get("timestamp") or []
        closes = res["indicators"]["quote"][0].get("close") or []
        adj = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
        series = adj if adj and len(adj) == len(stamps) else closes
        for ts, val in zip(stamps, series):
            if val is None:
                continue
            rows.append((datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"),
                         float(val)))
    except Exception as exc:                            # noqa: BLE001
        if not use_cache_on_fail:
            raise FetchError(f"yahoo {symbol}: {exc}") from exc

    if not rows and use_cache_on_fail:
        rows = _read_cache(f"yahoo_{key}")
        stale = bool(rows)
    if rows and not stale:
        # de-dup by date, keep last
        dedup: dict[str, float] = {}
        for d, v in rows:
            dedup[d] = v
        rows = sorted(dedup.items())
        _write_cache(f"yahoo_{key}", rows)

    return {
        "series_id": key,
        "label": config.YAHOO_SERIES.get(symbol, {}).get("label", symbol),
        "freq": "d",
        # Market prices carry NO publication lag: the tape prints them live.
        # FRED statistical releases keep their real lag (DGS30 for date T lands
        # that evening or T+1; CPI for month M lands mid-M+1). Mixing the two
        # correctly is what stops the dashboard showing a two-day-old gold price.
        "release_lag_days": 0,
        "observations": rows,
        "source": "Yahoo Finance",
        "source_url": f"https://finance.yahoo.com/quote/{symbol}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "from_cache": stale,
    }


# ----------------------------------------------------------- CoinGecko ----
def fetch_btc_spot() -> dict[str, Any] | None:
    url = (f"{COINGECKO}/simple/price?ids=bitcoin&vs_currencies=usd"
           f"&include_24hr_change=true&include_market_cap=true&include_last_updated_at=true")
    try:
        p = json.loads(_http_get(url, timeout=25, retries=2, send_ua=False))["bitcoin"]
        return {
            "price": float(p["usd"]),
            "change_24h_pct": float(p.get("usd_24h_change") or 0.0),
            "market_cap": float(p.get("usd_market_cap") or 0.0),
            "last_updated": datetime.fromtimestamp(
                p.get("last_updated_at", 0), timezone.utc).isoformat(),
            "source": "CoinGecko",
            "source_url": "https://www.coingecko.com/en/coins/bitcoin",
        }
    except Exception:                                   # noqa: BLE001
        return None


def fetch_btc_history(days: int = 3650) -> dict[str, Any]:
    """CoinGecko daily history; falls back to Yahoo BTC-USD which goes to 2014."""
    url = f"{COINGECKO}/coins/bitcoin/market_chart?vs_currency=usd&days={days}&interval=daily"
    rows: list[tuple[str, float]] = []
    try:
        payload = json.loads(_http_get(url, timeout=45, retries=2, send_ua=False))
        for ms, price in payload.get("prices", []):
            rows.append((datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d"),
                         float(price)))
    except Exception:                                   # noqa: BLE001
        rows = []
    if len(rows) < 400:                                 # free tier caps history at 365d
        y = fetch_yahoo("BTC-USD", range_="10y")
        if len(y["observations"]) > len(rows):
            return y
    dedup: dict[str, float] = {}
    for d, v in rows:
        dedup[d] = v
    rows = sorted(dedup.items())
    if rows:
        _write_cache("yahoo_BTC", rows)
    return {
        "series_id": "BTC", "label": "Bitcoin", "freq": "d", "release_lag_days": 0,
        "observations": rows, "source": "CoinGecko",
        "source_url": "https://www.coingecko.com/en/coins/bitcoin",
        "fetched_at": datetime.now(timezone.utc).isoformat(), "from_cache": False,
    }


# ------------------------------------------------------------ orchestr ----
def collect_all(start: str = config.HISTORY_START, verbose: bool = True) -> dict[str, dict]:
    out: dict[str, dict] = {}
    failures: list[str] = []
    for sid in config.FRED_SERIES:
        try:
            d = fetch_fred(sid, start=start)
            out[sid] = d
            if verbose:
                n, tag = len(d["observations"]), " [CACHE]" if d["from_cache"] else ""
                last = d["observations"][-1] if d["observations"] else ("-", "-")
                print(f"  FRED {sid:14s} n={n:6d} last={last[0]} {last[1]}{tag}")
            if not d["observations"]:
                failures.append(sid)
        except Exception as exc:                        # noqa: BLE001
            failures.append(sid)
            if verbose:
                print(f"  FRED {sid:14s} FAILED: {exc}")
    for sym, meta in config.YAHOO_SERIES.items():
        if meta["key"] == "BTC":
            continue
        try:
            d = fetch_yahoo(sym)
            out[meta["key"]] = d
            if verbose:
                n, tag = len(d["observations"]), " [CACHE]" if d["from_cache"] else ""
                last = d["observations"][-1] if d["observations"] else ("-", "-")
                print(f"  YHOO {meta['key']:14s} n={n:6d} last={last[0]} {last[1]}{tag}")
            if not d["observations"]:
                failures.append(meta["key"])
        except Exception as exc:                        # noqa: BLE001
            failures.append(meta["key"])
            if verbose:
                print(f"  YHOO {meta['key']:14s} FAILED: {exc}")
    try:
        btc = fetch_btc_history()
        out["BTC"] = btc
        if verbose:
            last = btc["observations"][-1] if btc["observations"] else ("-", "-")
            print(f"  BTC  {'BTC':14s} n={len(btc['observations']):6d} last={last[0]} {last[1]}")
    except Exception as exc:                            # noqa: BLE001
        failures.append("BTC")
        if verbose:
            print(f"  BTC FAILED: {exc}")
    out["_failures"] = {"series_id": "_failures", "observations": [], "list": failures}
    return out
