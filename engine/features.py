"""
Panel construction + derived metrics.

Two rules that matter more than anything else in this file:

1. RELEASE LAG IS ALWAYS APPLIED. An observation dated M is only visible once
   M + release_lag_days has passed. This is what stops the backtest from
   "knowing" July CPI in early July. It is applied in live mode too, because
   in live mode it is simply true.

2. NOTHING IS INTERPOLATED. Lower-frequency series are forward-filled from the
   last actually-published value, and we always keep the observation date so
   staleness is visible rather than hidden.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date, datetime, timedelta

from . import config


def _d(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


class Panel:
    """Aligned view over all series with as-of-date semantics."""

    def __init__(self, raw: dict[str, dict]):
        self.meta: dict[str, dict] = {}
        self.dates: dict[str, list[date]] = {}
        self.values: dict[str, list[float]] = {}
        self.avail: dict[str, list[date]] = {}   # obs_date + release_lag

        for sid, blob in raw.items():
            if sid.startswith("_"):
                continue
            obs = blob.get("observations") or []
            if not obs:
                continue
            obs = sorted({o[0]: o[1] for o in obs}.items())
            lag = int(blob.get("release_lag_days", 1))
            self.meta[sid] = {k: v for k, v in blob.items() if k != "observations"}
            self.dates[sid] = [_d(o[0]) for o in obs]
            self.values[sid] = [float(o[1]) for o in obs]
            self.avail[sid] = [dd + timedelta(days=lag) for dd in self.dates[sid]]

        all_d: set[date] = set()
        for sid in self.dates:
            if self.meta[sid].get("freq") == "d":
                all_d.update(self.dates[sid])
        self.calendar = sorted(all_d)

    # ---------------------------------------------------------------- core --
    def _idx_asof(self, sid: str, when: date) -> int | None:
        """Index of newest observation PUBLISHED on or before `when`."""
        av = self.avail.get(sid)
        if not av:
            return None
        i = bisect_right(av, when) - 1
        return i if i >= 0 else None

    def asof(self, sid: str, when: date) -> float | None:
        i = self._idx_asof(sid, when)
        return self.values[sid][i] if i is not None else None

    def asof_date(self, sid: str, when: date) -> date | None:
        i = self._idx_asof(sid, when)
        return self.dates[sid][i] if i is not None else None

    def staleness_days(self, sid: str, when: date) -> int | None:
        od = self.asof_date(sid, when)
        return (when - od).days if od else None

    def _idx_back(self, sid: str, when: date, n_obs: int) -> int | None:
        i = self._idx_asof(sid, when)
        if i is None:
            return None
        j = i - n_obs
        return j if j >= 0 else None

    def chg(self, sid: str, when: date, n_obs: int) -> float | None:
        """Absolute change over n observations (for rates: change in pct points)."""
        i, j = self._idx_asof(sid, when), self._idx_back(sid, when, n_obs)
        if i is None or j is None:
            return None
        return self.values[sid][i] - self.values[sid][j]

    def pct_chg(self, sid: str, when: date, n_obs: int) -> float | None:
        """Percent change over n observations (for prices)."""
        i, j = self._idx_asof(sid, when), self._idx_back(sid, when, n_obs)
        if i is None or j is None:
            return None
        base = self.values[sid][j]
        if base == 0:
            return None
        return (self.values[sid][i] / base - 1.0) * 100.0

    def window(self, sid: str, when: date, n_obs: int) -> list[float]:
        i = self._idx_asof(sid, when)
        if i is None:
            return []
        return self.values[sid][max(0, i - n_obs + 1): i + 1]

    def percentile(self, sid: str, when: date,
                   lookback: int = config.PERCENTILE_LOOKBACK) -> float | None:
        w = self.window(sid, when, lookback)
        if len(w) < max(30, lookback // 10):
            return None
        cur = w[-1]
        return 100.0 * sum(1 for v in w if v <= cur) / len(w)

    def zscore(self, sid: str, when: date,
               lookback: int = config.ZSCORE_LOOKBACK) -> float | None:
        w = self.window(sid, when, lookback)
        if len(w) < max(30, lookback // 10):
            return None
        mu = sum(w) / len(w)
        var = sum((v - mu) ** 2 for v in w) / max(1, len(w) - 1)
        sd = math.sqrt(var)
        return (w[-1] - mu) / sd if sd > 1e-12 else None

    def max_to_date(self, sid: str, when: date) -> float | None:
        i = self._idx_asof(sid, when)
        if i is None:
            return None
        return max(self.values[sid][: i + 1])

    def yoy(self, sid: str, when: date, periods: int = 12) -> float | None:
        """Year-over-year percent change for monthly index levels (CPI etc)."""
        i, j = self._idx_asof(sid, when), self._idx_back(sid, when, periods)
        if i is None or j is None or self.values[sid][j] == 0:
            return None
        return (self.values[sid][i] / self.values[sid][j] - 1.0) * 100.0


# ---------------------------------------------------------------- derived --
def compute_features(p: Panel, when: date) -> dict:
    """
    Everything the scorers are allowed to look at. One flat dict so the whole
    input surface of the rule engine is inspectable and dumpable to JSON.
    """
    W = config.WINDOWS
    f: dict[str, float | None] = {}

    def lvl(sid, name=None):
        f[name or f"{sid}_level"] = p.asof(sid, when)

    # --- levels -----------------------------------------------------------
    for sid in ("DGS30", "DGS10", "DGS5", "DGS2", "DGS3MO", "DFII10", "DFII30",
                "DFII5", "T10YIE", "T5YIE", "T5YIFR", "THREEFYTP10", "DTWEXBGS",
                "BAMLH0A0HYM2", "VIXCLS", "DFEDTARU", "GFDEGDQ188S",
                "DCOILWTICO", "DCOILBRENTEU"):
        lvl(sid)
    for sid in ("GOLD", "BTC", "DXY", "NDX", "SPX"):
        lvl(sid)

    # --- rate changes (percentage points) ---------------------------------
    for sid in ("DGS30", "DGS10", "DFII10", "DFII30", "T10YIE", "T5YIFR",
                "THREEFYTP10", "BAMLH0A0HYM2"):
        for tag, n in (("5d", W["w1"]), ("20d", W["w4"]), ("60d", W["w12"]), ("252d", W["w52"])):
            f[f"{sid}_chg_{tag}"] = p.chg(sid, when, n)

    # --- price changes (percent) ------------------------------------------
    for sid in ("GOLD", "BTC", "DXY", "NDX", "SPX", "DTWEXBGS",
                "DCOILWTICO", "DCOILBRENTEU"):
        for tag, n in (("5d", W["w1"]), ("20d", W["w4"]), ("60d", W["w12"]), ("252d", W["w52"])):
            f[f"{sid}_ret_{tag}"] = p.pct_chg(sid, when, n)

    # DTWEXBGS is a rate-like index but we want its % move for the dollar block
    f["dxy_chg_60d"] = f.get("DXY_ret_60d") if f.get("DXY_ret_60d") is not None else f.get("DTWEXBGS_ret_60d")
    f["dxy_chg_20d"] = f.get("DXY_ret_20d") if f.get("DXY_ret_20d") is not None else f.get("DTWEXBGS_ret_20d")

    # --- percentiles / z-scores -------------------------------------------
    for sid in ("DGS30", "DFII10", "THREEFYTP10", "DTWEXBGS", "BAMLH0A0HYM2"):
        f[f"{sid}_pctile"] = p.percentile(sid, when)
        f[f"{sid}_z"] = p.zscore(sid, when)

    # --- realised inflation -----------------------------------------------
    f["cpi_yoy"] = p.yoy("CPIAUCSL", when)
    f["core_cpi_yoy"] = p.yoy("CPILFESL", when)
    f["pce_yoy"] = p.yoy("PCEPI", when)
    f["core_pce_yoy"] = p.yoy("PCEPILFE", when)

    # 3-month trend in CPI YoY: is realised inflation accelerating?
    cpi_now = f["cpi_yoy"]
    i = p._idx_asof("CPIAUCSL", when)
    if cpi_now is not None and i is not None and i >= 15:
        v = p.values["CPIAUCSL"]
        prev_yoy = (v[i - 3] / v[i - 15] - 1.0) * 100.0 if v[i - 15] else None
        f["cpi_yoy_trend_3m"] = (cpi_now - prev_yoy) if prev_yoy is not None else None
    else:
        f["cpi_yoy_trend_3m"] = None

    # --- curve ------------------------------------------------------------
    if f["DGS30_level"] is not None and f["DGS3MO_level"] is not None:
        f["curve_30y_3m"] = f["DGS30_level"] - f["DGS3MO_level"]
    if f["DGS10_level"] is not None and f["DGS2_level"] is not None:
        f["curve_10y_2y"] = f["DGS10_level"] - f["DGS2_level"]
    c30 = p.chg("DGS30", when, W["w12"])
    c3m = p.chg("DGS3MO", when, W["w12"])
    # bear steepening with the front end pinned == fiscal, not growth
    f["bear_steepen_60d"] = (c30 - c3m) if (c30 is not None and c3m is not None) else None

    # --- YIELD DECOMPOSITION ---------------------------------------------
    # The question the whole product exists to answer: is the long end rising
    # because of REAL RATES (ordinary repricing) or because of TERM PREMIUM
    # (the market charging the Treasury a fiscal risk premium)?
    d10 = p.chg("DGS10", when, W["w12"])
    dre = p.chg("DFII10", when, W["w12"])
    dbe = p.chg("T10YIE", when, W["w12"])
    dtp = p.chg("THREEFYTP10", when, W["w12"])
    f["decomp_d10y_60d"] = d10
    f["decomp_real_60d"] = dre
    f["decomp_breakeven_60d"] = dbe
    f["decomp_termprem_60d"] = dtp
    if d10 is not None and abs(d10) > 1e-9:
        f["decomp_share_real"] = (dre / d10) if dre is not None else None
        f["decomp_share_breakeven"] = (dbe / d10) if dbe is not None else None
        f["decomp_share_termprem"] = (dtp / d10) if dtp is not None else None
    else:
        f["decomp_share_real"] = f["decomp_share_breakeven"] = f["decomp_share_termprem"] = None

    f["decomp_driver"] = _classify_yield_driver(d10, dre, dbe, dtp)

    # --- net liquidity ----------------------------------------------------
    # WALCL / WTREGEN are $ millions; RRPONTSYD is $ billions.
    walcl, tga, rrp = p.asof("WALCL", when), p.asof("WTREGEN", when), p.asof("RRPONTSYD", when)
    if walcl is not None:
        nl = walcl - (tga or 0.0) - (rrp or 0.0) * 1000.0
        f["net_liquidity_musd"] = nl
        prev = _net_liquidity_at(p, when - timedelta(days=90))
        f["net_liquidity_chg_60d_pct"] = ((nl / prev - 1.0) * 100.0
                                          if prev and prev > 0 else None)
    else:
        f["net_liquidity_musd"] = f["net_liquidity_chg_60d_pct"] = None
    f["walcl_chg_60d_pct"] = p.pct_chg("WALCL", when, 12)   # weekly series -> 12 obs ~ 60d

    # --- cross-asset ------------------------------------------------------
    g, b = f["GOLD_level"], f["BTC_level"]
    f["btc_gold_ratio"] = (b / g) if (g and b) else None
    prev_g = p.asof("GOLD", when - timedelta(days=30))
    prev_b = p.asof("BTC", when - timedelta(days=30))
    if all(x for x in (g, b, prev_g, prev_b)):
        f["btc_gold_ratio_chg_20d_pct"] = ((b / g) / (prev_b / prev_g) - 1.0) * 100.0
    else:
        f["btc_gold_ratio_chg_20d_pct"] = None
    if f.get("BTC_ret_60d") is not None and f.get("NDX_ret_60d") is not None:
        f["btc_minus_ndx_60d"] = f["BTC_ret_60d"] - f["NDX_ret_60d"]
    if f.get("BTC_ret_60d") is not None and f.get("GOLD_ret_60d") is not None:
        f["btc_minus_gold_60d"] = f["BTC_ret_60d"] - f["GOLD_ret_60d"]

    # --- drawdown from all-time high --------------------------------------
    for sid in ("GOLD", "BTC"):
        ath = p.max_to_date(sid, when)
        cur = f[f"{sid}_level"]
        f[f"{sid.lower()}_ath"] = ath
        f[f"{sid.lower()}_from_ath_pct"] = ((cur / ath - 1.0) * 100.0
                                            if (ath and cur) else None)

    # --- market-implied policy direction ----------------------------------
    # The front end versus the current target is a free, transparent read on
    # whether the market expects the Fed to hike or cut. It matters here for a
    # specific reason: financial repression requires a Fed that is CONSTRAINED
    # and keeping real rates down. A market pricing HIKES is direct evidence
    # the Fed is not being run by the fiscal problem — which is exactly the
    # opposite of the Stage 3 story, however high the debt gets.
    tgt = f.get("DFEDTARU_level")
    if tgt is not None:
        for sid, tag in (("DGS3MO", "3m"), ("DGS2", "2y")):
            lv = f.get(f"{sid}_level")
            f[f"policy_spread_{tag}"] = (lv - tgt) if lv is not None else None
        sp = f.get("policy_spread_2y")
        f["policy_expectation"] = (
            None if sp is None else
            ("hiking" if sp > 0.15 else ("cutting" if sp < -0.15 else "on_hold")))
    else:
        f["policy_spread_3m"] = f["policy_spread_2y"] = None
        f["policy_expectation"] = None

    # --- oil / rates co-movement -----------------------------------------
    # "Oil and long yields rising together" is a specific, checkable pattern
    # (oil rebuilds inflation pressure -> long end sells off). Expose it as a
    # single feature so a narrative can be tested against it directly.
    oil5, y5 = f.get("DCOILWTICO_ret_5d"), f.get("DGS30_chg_5d")
    oil20, y20 = f.get("DCOILWTICO_ret_20d"), f.get("DGS30_chg_20d")
    f["oil_yield_comove_5d"] = (1.0 if (oil5 > 0 and y5 > 0) else
                                (-1.0 if (oil5 < 0 and y5 < 0) else 0.0)) \
        if (oil5 is not None and y5 is not None) else None
    f["oil_yield_comove_20d"] = (1.0 if (oil20 > 0 and y20 > 0) else
                                 (-1.0 if (oil20 < 0 and y20 < 0) else 0.0)) \
        if (oil20 is not None and y20 is not None) else None

    # --- aliases used by the Stage-3 gate expressions ----------------------
    f["dfii10_chg_60d"] = f.get("DFII10_chg_60d")
    f["dgs30_chg_60d"] = f.get("DGS30_chg_60d")
    f["gold_chg_60d"] = f.get("GOLD_ret_60d")

    # --- freshness --------------------------------------------------------
    f["_staleness"] = {sid: p.staleness_days(sid, when) for sid in p.meta}
    f["_asof_dates"] = {sid: (p.asof_date(sid, when).isoformat()
                              if p.asof_date(sid, when) else None) for sid in p.meta}
    return f


def _net_liquidity_at(p: Panel, when: date) -> float | None:
    walcl = p.asof("WALCL", when)
    if walcl is None:
        return None
    return walcl - (p.asof("WTREGEN", when) or 0.0) - (p.asof("RRPONTSYD", when) or 0.0) * 1000.0


def _classify_yield_driver(d10, dre, dbe, dtp) -> str | None:
    """
    Attribution label for the 60d move in the 10Y. Deliberately conservative:
    returns 'mixed' unless one component clearly dominates.
    """
    if d10 is None:
        return None
    if abs(d10) < 0.10:
        # Net move is small, but the COMPOSITION may still have rotated hard —
        # real yields up while breakevens fall nets to ~zero yet is a completely
        # different world from "nothing happened". Report the rotation.
        if dre is not None and dbe is not None and abs(dre) >= 0.15 and dre * dbe < 0:
            return "rotation_real_up" if dre > 0 else "rotation_real_down"
        return "flat"
    parts = {"real": dre, "breakeven": dbe, "term_premium": dtp}
    parts = {k: v for k, v in parts.items() if v is not None}
    if not parts:
        return None
    # dominance = same sign as the total move AND >= 55% of it
    ranked = sorted(parts.items(), key=lambda kv: abs(kv[1]), reverse=True)
    top, val = ranked[0]
    if val * d10 > 0 and abs(val) >= 0.55 * abs(d10):
        direction = "rising" if d10 > 0 else "falling"
        return f"{top}_{direction}"
    return "mixed"
