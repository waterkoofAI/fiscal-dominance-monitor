"""
The four scores. Every component is a pure function of the feature dict and
returns (points, max_points, human_note). Nothing here reads the network, the
clock, or an LLM. Given the same features you get the same score, forever.
"""
from __future__ import annotations

from . import config


# ------------------------------------------------------------- helpers ----
def band(value: float | None, bands: list[tuple[float, float]]) -> float | None:
    """Step function. bands = [(upper_bound, points), ...] ascending."""
    if value is None:
        return None
    for upper, pts in bands:
        if value <= upper:
            return float(pts)
    return float(bands[-1][1])


def ramp(value: float | None, lo: float, hi: float, max_pts: float,
         invert: bool = False) -> float | None:
    """Linear ramp: lo -> 0 points, hi -> max_pts (clamped). invert flips it."""
    if value is None:
        return None
    if hi == lo:
        return None
    t = (value - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    if invert:
        t = 1.0 - t
    return t * max_pts


class ScoreBuilder:
    def __init__(self, weights: dict[str, float], drivers: list[str]):
        self.weights = weights
        self.drivers = set(drivers)
        self.parts: dict[str, dict] = {}

    def add(self, name: str, pts: float | None, note: str) -> None:
        mx = float(self.weights[name])
        if pts is None:
            self.parts[name] = {"points": None, "max": mx, "note": note,
                                "missing": True,
                                "kind": "driver" if name in self.drivers else "confirmation"}
        else:
            self.parts[name] = {"points": round(max(0.0, min(mx, pts)), 2), "max": mx,
                                "note": note, "missing": False,
                                "kind": "driver" if name in self.drivers else "confirmation"}

    def finalise(self, collinear_caps: dict[str, tuple[list[str], float]] | None = None) -> dict:
        if collinear_caps:
            for _grp, (members, cap) in collinear_caps.items():
                live = [m for m in members if m in self.parts and not self.parts[m]["missing"]]
                total = sum(self.parts[m]["points"] for m in live)
                if total > cap and total > 0:
                    scale = cap / total
                    for m in live:
                        self.parts[m]["points"] = round(self.parts[m]["points"] * scale, 2)
                        self.parts[m]["note"] += f" [collinearity-capped ×{scale:.2f}]"

        avail = {k: v for k, v in self.parts.items() if not v["missing"]}
        avail_max = sum(v["max"] for v in avail.values())
        got = sum(v["points"] for v in avail.values())
        # Renormalise to 0..100 over the components we could actually compute,
        # so a missing input dilutes confidence rather than silently scoring 0.
        score = (got / avail_max * 100.0) if avail_max > 0 else 0.0
        driver_max = sum(v["max"] for v in avail.values() if v["kind"] == "driver")
        driver_got = sum(v["points"] for v in avail.values() if v["kind"] == "driver")
        conf_max = sum(v["max"] for v in avail.values() if v["kind"] == "confirmation")
        conf_got = sum(v["points"] for v in avail.values() if v["kind"] == "confirmation")
        return {
            "score": round(score, 1),
            "driver_score": round(driver_got / driver_max * 100.0, 1) if driver_max else None,
            "confirmation_score": round(conf_got / conf_max * 100.0, 1) if conf_max else None,
            "coverage_pct": round(avail_max / sum(v["max"] for v in self.parts.values()) * 100.0, 1),
            "components": self.parts,
        }


# ------------------------------------------------------ 1. FISCAL STRESS --
def fiscal_stress(f: dict, policy: dict) -> dict:
    W = config.FISCAL_STRESS
    b = ScoreBuilder(W, config.DRIVER_COMPONENTS["fiscal_stress"])

    lv = f.get("DGS30_level")
    b.add("level_30y", ramp(band(lv, config.BANDS["DGS30"]), 0, 15, W["level_30y"]),
          f"30Y at {lv:.2f}%" if lv is not None else "30Y unavailable")

    pc = f.get("DGS30_pctile")
    b.add("pctile_30y", ramp(pc, 40, 95, W["pctile_30y"]),
          f"30Y in {pc:.0f}th pctile of last 3y" if pc is not None else "no percentile")

    ch = f.get("DGS30_chg_20d")
    b.add("chg_30y_20d", ramp(ch, -0.10, 0.45, W["chg_30y_20d"]),
          f"30Y {ch*100:+.0f}bp over 20d" if ch is not None else "no 20d change")

    tp = f.get("THREEFYTP10_level")
    b.add("term_premium_level", ramp(band(tp, config.BANDS["THREEFYTP10"]), 0, 12,
                                     W["term_premium_level"]),
          f"10Y term premium {tp:+.2f}%" if tp is not None else "no term premium")

    tpc = f.get("THREEFYTP10_chg_60d")
    b.add("term_premium_chg_60d", ramp(tpc, -0.15, 0.55, W["term_premium_chg_60d"]),
          f"term premium {tpc*100:+.0f}bp over 60d" if tpc is not None else "n/a")

    bs = f.get("bear_steepen_60d")
    b.add("curve_stress", ramp(bs, -0.20, 0.70, W["curve_stress"]),
          f"30Y-3M steepened {bs*100:+.0f}bp in 60d" if bs is not None else "n/a")

    dg = f.get("GFDEGDQ188S_level")
    b.add("debt_gdp", ramp(dg, 95, 135, W["debt_gdp"]),
          f"federal debt/GDP {dg:.0f}%" if dg is not None else "n/a")

    pi = policy.get("fiscal_intervention_score", 0.0)
    b.add("policy_intervention", ramp(pi, 0, 40, W["policy_intervention"]),
          policy.get("fiscal_intervention_note", "no recorded intervention"))

    return b.finalise({"long_end": (["level_30y"], config.COLLINEAR_GROUPS["long_end"]["cap"])})


# ----------------------------------------------- 2. FINANCIAL REPRESSION --
def financial_repression(f: dict, policy: dict, fiscal_score: float = 50.0) -> dict:
    W = config.FINANCIAL_REPRESSION
    b = ScoreBuilder(W, config.DRIVER_COMPONENTS["financial_repression"])

    ry = f.get("DFII10_level")
    b.add("real_yield_level", ramp(band(ry, config.BANDS["DFII10_low"]), 0, 12,
                                   W["real_yield_level"]),
          f"10Y real yield {ry:+.2f}%" if ry is not None else "n/a")

    rt = f.get("DFII10_chg_60d")
    b.add("real_yield_trend_60d", ramp(rt, 0.20, -0.60, W["real_yield_trend_60d"]),
          f"real yield {rt*100:+.0f}bp over 60d" if rt is not None else "n/a")

    # THE smoking gun: realised inflation not falling WHILE real yields fall.
    ct, rt2 = f.get("cpi_yoy_trend_3m"), f.get("DFII10_chg_60d")
    if ct is None or rt2 is None:
        b.add("infl_realyield_divergence", None, "needs CPI trend + real yield trend")
    else:
        infl_part = ramp(ct, -0.40, 0.60, 1.0)      # 0..1, inflation accelerating
        real_part = ramp(rt2, 0.10, -0.50, 1.0)     # 0..1, real yields falling
        div = (infl_part * real_part) ** 0.5        # geometric mean: BOTH required
        b.add("infl_realyield_divergence", div * W["infl_realyield_divergence"],
              f"CPI YoY trend {ct:+.2f}pp & real yield {rt2*100:+.0f}bp/60d")

    # Long end capped: deficits large but 30Y refusing to rise = someone is
    # leaning on it. Only meaningful when fiscal stress is already elevated.
    # "The long end has stopped rising" is only evidence of repression if
    # there is fiscal pressure for something to be repressing. In a calm market
    # a flat 30Y is just a flat 30Y, and scoring it as repression manufactures
    # a signal out of nothing. Gate it on fiscal stress.
    c30 = f.get("DGS30_chg_60d")
    if c30 is None:
        b.add("long_end_capped", None, "n/a")
    else:
        raw = ramp(c30, 0.35, -0.35, W["long_end_capped"])
        gate = ramp(fiscal_score, 30.0, 60.0, 1.0)     # 0 below FS=30, full at FS>=60
        b.add("long_end_capped", raw * gate,
              f"30Y {c30*100:+.0f}bp over 60d"
              + (f" [财政压力门 ×{gate:.2f}]" if gate < 0.99 else ""))

    pr = policy.get("repression_score", 0.0)
    b.add("policy_repression", ramp(pr, 0, 50, W["policy_repression"]),
          policy.get("repression_note", "no recorded repression action"))

    dx = f.get("dxy_chg_60d")
    b.add("dollar_debase", ramp(dx, 2.0, -6.0, W["dollar_debase"]),
          f"dollar {dx:+.1f}% over 60d" if dx is not None else "n/a")

    return b.finalise()


# ----------------------------------------------------------- 3. DEBASEMENT --
def debasement(f: dict, policy: dict) -> dict:
    W = config.DEBASEMENT
    b = ScoreBuilder(W, config.DRIVER_COMPONENTS["debasement"])

    dx = f.get("dxy_chg_60d")
    b.add("dxy_trend", ramp(dx, 2.0, -7.0, W["dxy_trend"]),
          f"dollar {dx:+.1f}% / 60d" if dx is not None else "n/a")

    gr = f.get("GOLD_ret_60d")
    b.add("gold_trend", ramp(gr, -3.0, 18.0, W["gold_trend"]),
          f"gold {gr:+.1f}% / 60d" if gr is not None else "n/a")

    ga = f.get("gold_from_ath_pct")
    b.add("gold_ath_proximity", ramp(ga, -18.0, 0.0, W["gold_ath_proximity"]),
          f"gold {ga:+.1f}% from ATH" if ga is not None else "n/a")

    br = f.get("BTC_ret_60d")
    b.add("btc_trend", ramp(br, -12.0, 40.0, W["btc_trend"]),
          f"BTC {br:+.1f}% / 60d" if br is not None else "n/a")

    ie = f.get("T5YIFR_level")
    iec = f.get("T5YIFR_chg_60d")
    if ie is None:
        b.add("infl_expectations", None, "n/a")
    else:
        lvl_pts = ramp(ie, 2.0, 3.2, W["infl_expectations"] * 0.6)
        trd_pts = ramp(iec, -0.15, 0.30, W["infl_expectations"] * 0.4) if iec is not None else 0.0
        b.add("infl_expectations", lvl_pts + trd_pts,
              f"5y5y forward {ie:.2f}%" + (f", {iec*100:+.0f}bp/60d" if iec is not None else ""))

    rt = f.get("DFII10_chg_60d")
    b.add("real_yield_support", ramp(rt, 0.15, -0.50, W["real_yield_support"]),
          f"real yield {rt*100:+.0f}bp / 60d" if rt is not None else "n/a")

    # Coherence: in a genuine debasement trade gold rises AS the dollar falls.
    # Gold rising while the dollar ALSO rises is a different (weaker) story.
    if gr is None or dx is None:
        b.add("gold_vs_dxy_coherence", None, "n/a")
    else:
        coherent = (gr > 0 and dx < 0)
        partial = (gr > 0 and dx >= 0) or (gr <= 0 and dx < 0)
        pts = W["gold_vs_dxy_coherence"] * (1.0 if coherent else (0.4 if partial else 0.0))
        b.add("gold_vs_dxy_coherence", pts,
              "gold up / dollar down (coherent)" if coherent
              else ("partially coherent" if partial else "incoherent: gold down & dollar up"))

    return b.finalise({"infl_exp": (["infl_expectations"],
                                    config.COLLINEAR_GROUPS["infl_exp"]["cap"])})


# -------------------------------------------------------- 4. BTC LIQUIDITY --
def btc_liquidity(f: dict, policy: dict) -> dict:
    W = config.BTC_LIQUIDITY
    b = ScoreBuilder(W, config.DRIVER_COMPONENTS["btc_liquidity"])

    nl = f.get("net_liquidity_chg_60d_pct")
    b.add("net_liquidity_trend", ramp(nl, -3.0, 4.0, W["net_liquidity_trend"]),
          f"net liquidity (WALCL-TGA-RRP) {nl:+.1f}% / 60d" if nl is not None else "n/a")

    rt = f.get("DFII10_chg_60d")
    b.add("real_yield_direction", ramp(rt, 0.20, -0.50, W["real_yield_direction"]),
          f"real yield {rt*100:+.0f}bp / 60d" if rt is not None else "n/a")

    dx = f.get("dxy_chg_60d")
    b.add("dxy_direction", ramp(dx, 3.0, -4.0, W["dxy_direction"]),
          f"dollar {dx:+.1f}% / 60d" if dx is not None else "n/a")

    br = f.get("BTC_ret_20d")
    b.add("btc_trend_20d", ramp(br, -15.0, 25.0, W["btc_trend_20d"]),
          f"BTC {br:+.1f}% / 20d" if br is not None else "n/a")

    bg = f.get("btc_minus_gold_60d")
    b.add("btc_vs_gold", ramp(bg, -15.0, 20.0, W["btc_vs_gold"]),
          f"BTC {bg:+.1f}pp vs gold / 60d" if bg is not None else "n/a")

    bn = f.get("btc_minus_ndx_60d")
    b.add("btc_vs_ndx", ramp(bn, -15.0, 20.0, W["btc_vs_ndx"]),
          f"BTC {bn:+.1f}pp vs Nasdaq / 60d" if bn is not None else "n/a")

    # Credit gate: widening HY spreads kill BTC regardless of the macro story.
    oas = f.get("BAMLH0A0HYM2_level")
    b.add("credit_risk_gate", ramp(band(oas, config.BANDS["HY_OAS"]), 10, 0,
                                   W["credit_risk_gate"]),
          f"HY OAS {oas:.2f}%" if oas is not None else "n/a")

    return b.finalise()


# ------------------------------------------------------------- composite --
def composite(scores: dict[str, dict]) -> float:
    tot = 0.0
    for k, w in config.COMPOSITE_WEIGHTS.items():
        tot += scores[k]["score"] * w
    return round(tot, 1)


def compute_all(f: dict, policy: dict) -> dict:
    fs = fiscal_stress(f, policy)
    out = {
        "fiscal_stress": fs,
        "financial_repression": financial_repression(f, policy, fs["score"]),
        "debasement": debasement(f, policy),
        "btc_liquidity": btc_liquidity(f, policy),
    }
    out["composite"] = composite(out)
    drivers = [out[k]["driver_score"] for k in config.COMPOSITE_WEIGHTS
               if out[k]["driver_score"] is not None]
    confs = [out[k]["confirmation_score"] for k in config.COMPOSITE_WEIGHTS
             if out[k]["confirmation_score"] is not None]
    out["driver_composite"] = round(sum(drivers) / len(drivers), 1) if drivers else None
    out["confirmation_composite"] = round(sum(confs) / len(confs), 1) if confs else None
    return out
