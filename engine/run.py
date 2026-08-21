"""
Orchestrator. Produces docs/data/*.json for the PWA.

Design note: the daily job REPLAYS THE WHOLE HISTORY every run rather than
incrementally updating a state file. Hysteresis therefore has no persistent
state that can silently corrupt, and any given day's stage is reproducible from
the cached series alone. It costs a few seconds. Worth it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import (breakers, config, features, narrative, policy, scenarios,
               scores, signals, sources)
from .features import Panel

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data"
OUT.mkdir(parents=True, exist_ok=True)


def _load_panel(refresh: bool) -> Panel:
    if refresh:
        print("[collect] fetching all series ...")
        raw = sources.collect_all(verbose=True)
        fails = raw.get("_failures", {}).get("list", [])
        if fails:
            print(f"[collect] WARNING failed: {fails}")
    else:
        print("[collect] using disk cache ...")
        raw = {}
        for sid, meta in config.FRED_SERIES.items():
            rows = sources._read_cache(f"fred_{sid}")
            if rows:
                raw[sid] = {"observations": rows, "label": meta["label"],
                            "freq": meta["freq"],
                            "release_lag_days": meta["release_lag_days"],
                            "source": "FRED (cache)",
                            "source_url": f"https://fred.stlouisfed.org/series/{sid}",
                            "from_cache": True}
        for _sym, meta in config.YAHOO_SERIES.items():
            key = meta["key"]
            rows = sources._read_cache(f"yahoo_{key}")
            if rows and key not in raw:
                raw[key] = {"observations": rows, "label": meta["label"], "freq": "d",
                            "release_lag_days": 0, "source": "Yahoo (cache)",
                            "source_url": "", "from_cache": True}
    return Panel(raw)


def _eval_day(p: Panel, when: date, events: list[dict]) -> dict:
    f = features.compute_features(p, when)
    pol = policy.evaluate(when, events)
    sc = scores.compute_all(f, pol)
    rs, detail = stages_raw(f, sc, pol)
    return {"date": when, "features": f, "policy": pol, "scores": sc,
            "raw_stage": rs, "detail": detail}


def stages_raw(f, sc, pol):
    from .stages import raw_stage
    return raw_stage(f, sc, pol)


def _confidence(f: dict, p: Panel, when: date) -> dict:
    """100 minus staleness penalties. A Stage call on stale inputs earns an asterisk."""
    pen, notes = 0.0, []
    for sid, days in (f.get("_staleness") or {}).items():
        if days is None:
            continue
        meta = p.meta.get(sid, {})
        freq = meta.get("freq", "d")
        rule = config.STALENESS_PENALTY.get(freq, config.STALENESS_PENALTY["d"])
        grace = (config.FREQ_PERIOD_DAYS.get(freq, 1)
                 + int(meta.get("release_lag_days", 1))
                 + rule["buffer"])
        over = days - grace
        if over > 0:
            add = min(rule["max_penalty"], over * rule["penalty_per_day"])
            if sid not in config.CORE_SERIES:
                add *= config.NONCORE_PENALTY_SCALE
            pen += add
            if add >= 2:
                notes.append(f"{sid} 滞后 {days} 天（正常上限 {grace} 天）−{add:.0f}")
    pen = min(config.MAX_TOTAL_STALENESS_PENALTY, pen)
    return {"confidence": round(100.0 - pen, 1), "penalty": round(pen, 1),
            "notes": notes[:6]}


def run(refresh: bool = True, backtest_days: int | None = None,
        out_dir: Path = OUT) -> dict:
    t0 = time.time()
    p = _load_panel(refresh)
    events = policy.load_events()

    cal = p.calendar
    if not cal:
        raise SystemExit("no calendar dates — data collection failed entirely")

    span = backtest_days if backtest_days else 400
    eval_dates = [d for d in cal if d >= cal[-1] - timedelta(days=int(span * 1.5))]
    print(f"[eval] {len(eval_dates)} dates {eval_dates[0]} -> {eval_dates[-1]}")

    days = []
    for i, d in enumerate(eval_dates):
        days.append(_eval_day(p, d, events))
        if i % 250 == 0 and i:
            print(f"  ... {i}/{len(eval_dates)}")

    from .stages import (apply_hysteresis, nearest_triggers,
                         next_stage_checklist, stage_stability)
    raw_seq = [d["raw_stage"] for d in days]
    fs_seq = [d["scores"]["fiscal_stress"]["score"] for d in days]
    fr_seq = [d["scores"]["financial_repression"]["score"] for d in days]
    conf_seq = apply_hysteresis(raw_seq, fs_seq, fr_seq)
    for d, s in zip(days, conf_seq):
        d["stage"] = s

    # Second pass. Signals depend on the POST-hysteresis stage, so they cannot
    # be produced inside the first loop. Doing it here gives us a per-day signal
    # history, which is what the trajectory ("is the macro case strengthening
    # or weakening?") is computed from.
    sig_history: list[dict | None] = []
    for d in days:
        try:
            b = breakers.evaluate(d["features"], d["scores"])
            sig_history.append(signals.compute(d["stage"], d["scores"],
                                               d["features"], b))
        except Exception:                                   # noqa: BLE001
            sig_history.append(None)

    # Damp each asset's signal the same way the stage label is damped. Do this
    # BEFORE anything reads sig_history, so the trajectory, the posture and the
    # final signal all describe the same damped series.
    for asset in ("gold", "btc", "ust30", "usd"):
        raw_idx = [h[asset]["index"] if h else None for h in sig_history]
        for h, idx in zip(sig_history, signals.apply_hysteresis(raw_idx)):
            if h is None or idx is None:
                continue
            lvl = config.SIGNAL_LEVELS[idx]
            if lvl != h[asset]["signal"]:
                h[asset]["raw_signal"] = h[asset]["signal"]
                h[asset]["reason"] += f"（原始 {signals.CN[h[asset]['signal']]}，迟滞未确认）"
            h[asset]["signal"] = lvl
            h[asset]["signal_cn"] = signals.CN[lvl]
            h[asset]["emoji"] = signals.EMOJI[lvl]
            h[asset]["index"] = idx

    last = days[-1]
    prev_sc = days[-2]["scores"] if len(days) > 1 else None
    stab = stage_stability(conf_seq)
    checklist = next_stage_checklist(last["stage"], last["detail"])
    brk = breakers.evaluate(last["features"], last["scores"])
    sig = sig_history[-1] or signals.compute(last["stage"], last["scores"],
                                             last["features"], brk)
    btc_check = signals.btc_upgrade_checklist(last["features"], last["scores"],
                                              sig["btc"]["signal"])
    for k in sig:
        sig[k]["posture"] = signals.posture(sig[k]["index"])
        sig[k]["trajectory"] = signals.trajectory(sig_history, k)
    scen = scenarios.compute(last["features"], last["scores"], last["policy"])
    triggers = nearest_triggers(checklist)
    conf = _confidence(last["features"], p, last["date"])
    narr = narrative.build(last["date"], last["stage"], last["scores"],
                           last["features"], brk, stab, checklist, sig,
                           btc_check, prev_sc)

    spot = sources.fetch_btc_spot() if refresh else None

    latest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": last["date"].isoformat(),
        "stage": last["stage"],
        "stage_raw": last["raw_stage"],
        "stage_name": config.STAGE_DEFS[last["stage"]]["name"],
        "stage_name_cn": config.STAGE_DEFS[last["stage"]]["name_cn"],
        "stage_color": config.STAGE_DEFS[last["stage"]]["color"],
        "stage_defs": config.STAGE_DEFS,
        "composite": last["scores"]["composite"],
        "driver_composite": last["scores"]["driver_composite"],
        "confirmation_composite": last["scores"]["confirmation_composite"],
        "scores": {k: {"score": v["score"],
                       "driver_score": v["driver_score"],
                       "confirmation_score": v["confirmation_score"],
                       "coverage_pct": v["coverage_pct"],
                       "components": v["components"]}
                   for k, v in last["scores"].items()
                   if isinstance(v, dict)},
        "signals": sig,
        "btc_checklist": btc_check,
        "next_stage": checklist,
        "nearest_triggers": triggers,
        "scenarios": scen,
        "breakers": brk,
        "stability": stab,
        "confidence": conf,
        "narrative": narr,
        "policy": {"recent_facts": last["policy"]["recent_facts"],
                   "recent_inferences": last["policy"]["recent_inferences"],
                   "ledger_size": last["policy"]["ledger_size"],
                   "fiscal_intervention_score": last["policy"]["fiscal_intervention_score"],
                   "repression_score": last["policy"]["repression_score"]},
        "yield_decomposition": {
            "d10y_60d": last["features"].get("decomp_d10y_60d"),
            "real_60d": last["features"].get("decomp_real_60d"),
            "breakeven_60d": last["features"].get("decomp_breakeven_60d"),
            "termprem_60d": last["features"].get("decomp_termprem_60d"),
            "driver": last["features"].get("decomp_driver"),
            "driver_cn": narrative.DRIVER_CN.get(
                last["features"].get("decomp_driver") or "", ""),
        },
        "key_metrics": _key_metrics(last["features"], p, last["date"], spot),
        "sources": _source_table(p),
    }

    hist = {
        "dates": [d["date"].isoformat() for d in days],
        "stage": [d["stage"] for d in days],
        "stage_raw": [d["raw_stage"] for d in days],
        "composite": [d["scores"]["composite"] for d in days],
        "fiscal_stress": [d["scores"]["fiscal_stress"]["score"] for d in days],
        "financial_repression": [d["scores"]["financial_repression"]["score"] for d in days],
        "debasement": [d["scores"]["debasement"]["score"] for d in days],
        "btc_liquidity": [d["scores"]["btc_liquidity"]["score"] for d in days],
        "driver": [d["scores"]["driver_composite"] for d in days],
        "confirmation": [d["scores"]["confirmation_composite"] for d in days],
        "DGS30": [d["features"].get("DGS30_level") for d in days],
        "DFII10": [d["features"].get("DFII10_level") for d in days],
        "THREEFYTP10": [d["features"].get("THREEFYTP10_level") for d in days],
        "DXY": [d["features"].get("DXY_level") for d in days],
        "GOLD": [d["features"].get("GOLD_level") for d in days],
        "BTC": [d["features"].get("BTC_level") for d in days],
        "BTC_GOLD": [d["features"].get("btc_gold_ratio") for d in days],
        "sig_gold": [h["gold"]["index"] if h else None for h in sig_history],
        "sig_btc": [h["btc"]["index"] if h else None for h in sig_history],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(
        json.dumps(latest, ensure_ascii=False, indent=1, default=str))
    (out_dir / "history.json").write_text(
        json.dumps(hist, ensure_ascii=False, separators=(",", ":"), default=str))
    print(f"[done] stage={last['stage']} composite={last['scores']['composite']} "
          f"in {time.time()-t0:.1f}s -> {out_dir}")
    return latest


def _key_metrics(f: dict, p: Panel, when: date, spot: dict | None) -> list[dict]:
    def row(key, label, sid, val, unit, chg, chg_unit, url):
        return {"key": key, "label": label, "series": sid, "value": val,
                "unit": unit, "change": chg, "change_unit": chg_unit,
                "as_of": (f.get("_asof_dates") or {}).get(sid),
                "stale_days": (f.get("_staleness") or {}).get(sid),
                "source_url": url}
    F = "https://fred.stlouisfed.org/series/"
    out = [
        row("dgs30", "30Y 美债", "DGS30", f.get("DGS30_level"), "%",
            f.get("DGS30_chg_5d"), "bp", F + "DGS30"),
        row("dfii10", "10Y 实际利率", "DFII10", f.get("DFII10_level"), "%",
            f.get("DFII10_chg_5d"), "bp", F + "DFII10"),
        row("tp10", "10Y 期限溢价", "THREEFYTP10", f.get("THREEFYTP10_level"), "%",
            f.get("THREEFYTP10_chg_5d"), "bp", F + "THREEFYTP10"),
        row("t5yifr", "5Y5Y 通胀预期", "T5YIFR", f.get("T5YIFR_level"), "%",
            f.get("T5YIFR_chg_5d"), "bp", F + "T5YIFR"),
        row("cpi", "CPI 同比", "CPIAUCSL", f.get("cpi_yoy"), "%",
            f.get("cpi_yoy_trend_3m"), "pp", F + "CPIAUCSL"),
        row("dxy", "美元 DXY", "DXY", f.get("DXY_level"), "",
            f.get("DXY_ret_5d"), "%", "https://finance.yahoo.com/quote/DX-Y.NYB"),
        row("gold", "黄金", "GOLD", f.get("GOLD_level"), "$",
            f.get("GOLD_ret_5d"), "%", "https://finance.yahoo.com/quote/GC=F"),
        row("btc", "BTC", "BTC", f.get("BTC_level"), "$",
            f.get("BTC_ret_5d"), "%", "https://www.coingecko.com/en/coins/bitcoin"),
        row("btcgold", "BTC/黄金 比价", "BTC", f.get("btc_gold_ratio"), "",
            f.get("btc_gold_ratio_chg_20d_pct"), "%", ""),
        row("netliq", "净流动性", "WALCL", 
            (f.get("net_liquidity_musd") or 0) / 1e6 if f.get("net_liquidity_musd") else None,
            "T$", f.get("net_liquidity_chg_60d_pct"), "%", F + "WALCL"),
    ]
    if spot:
        out.append({"key": "btc_spot", "label": "BTC 实时", "series": "BTC",
                    "value": spot["price"], "unit": "$",
                    "change": spot["change_24h_pct"], "change_unit": "%",
                    "as_of": spot["last_updated"], "stale_days": 0,
                    "source_url": spot["source_url"]})
    return out


def _source_table(p: Panel) -> list[dict]:
    return [{"series": sid, "label": m.get("label", sid), "source": m.get("source", ""),
             "url": m.get("source_url", ""), "freq": m.get("freq", ""),
             "release_lag_days": m.get("release_lag_days", 1),
             "from_cache": m.get("from_cache", False)}
            for sid, m in sorted(p.meta.items())]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-refresh", action="store_true", help="use disk cache only")
    ap.add_argument("--days", type=int, default=400, help="history window to evaluate")
    args = ap.parse_args()
    run(refresh=not args.no_refresh, backtest_days=args.days)


if __name__ == "__main__":
    main()
