"""
Asset signals + the BTC upgrade checklist.

These are RISK POSTURE LABELS, not trade instructions. Deliberately so: a
single bad data print or a stale API should be able to change a label, and
must never be able to change a position. There is no sizing, no entry price,
no order anywhere in this codebase, and that is a feature.
"""
from __future__ import annotations

from . import config

LEVELS = config.SIGNAL_LEVELS          # Strong Bearish .. Strong Bullish
CN = {
    "Strong Bearish": "强烈偏空", "Bearish": "偏空", "Caution": "谨慎",
    "Neutral": "中性", "Bullish": "偏多", "Strong Bullish": "强烈偏多",
}
EMOJI = {
    "Strong Bearish": "🔴", "Bearish": "🔴", "Caution": "🟡",
    "Neutral": "⚪", "Bullish": "🟢", "Strong Bullish": "🟢",
}


def _clamp(i: int) -> str:
    return LEVELS[max(0, min(len(LEVELS) - 1, i))]


def _shift(level: str, n: int) -> str:
    return _clamp(LEVELS.index(level) + n)


def compute(stage: int, sc: dict, f: dict, brk: dict) -> dict:
    """Stage sets the baseline; scores and breakers nudge it by one notch."""
    deb = sc["debasement"]["score"]
    liq = sc["btc_liquidity"]["score"]
    fs = sc["fiscal_stress"]["score"]
    penalty = brk["thesis_penalty"]

    base_gold = {0: "Neutral", 1: "Bullish", 2: "Bullish",
                 3: "Strong Bullish", 4: "Strong Bullish"}[stage]
    base_btc = {0: "Neutral", 1: "Neutral", 2: "Bullish",
                3: "Strong Bullish", 4: "Strong Bullish"}[stage]
    base_ust = {0: "Neutral", 1: "Caution", 2: "Bearish",
                3: "Caution", 4: "Neutral"}[stage]
    base_usd = {0: "Neutral", 1: "Neutral", 2: "Caution",
                3: "Bearish", 4: "Bearish"}[stage]

    gold = _shift(base_gold, (1 if deb >= 70 else 0) - (1 if deb < 40 else 0))
    btc = _shift(base_btc, (1 if liq >= 65 else 0) - (1 if liq < 40 else 0))
    if penalty >= 20:
        gold, btc = _shift(gold, -1), _shift(btc, -1)
    ust = _shift(base_ust, -1 if fs >= 75 else 0)
    usd = _shift(base_usd, -1 if deb >= 70 else 0)

    def pack(name, cn, level, reason):
        return {"asset": name, "asset_cn": cn, "signal": level,
                "signal_cn": CN[level], "emoji": EMOJI[level],
                "index": LEVELS.index(level), "reason": reason}

    return {
        "gold": pack("Gold", "黄金", gold,
                     f"贬值分 {deb:.0f}，阶段基线 {CN[base_gold]}"
                     + (f"，破论扣减 {penalty:.0f}" if penalty >= 20 else "")),
        "btc": pack("BTC", "比特币", btc,
                    f"BTC流动性分 {liq:.0f}，阶段基线 {CN[base_btc]}"
                    + (f"，破论扣减 {penalty:.0f}" if penalty >= 20 else "")),
        "ust30": pack("UST 30Y", "30年美债", ust, f"财政压力分 {fs:.0f}"),
        "usd": pack("USD", "美元", usd, f"贬值分 {deb:.0f}"),
    }


# ------------------------------------------- BTC upgrade confirmation ------
def btc_upgrade_checklist(f: dict, sc: dict, current: str) -> dict:
    """
    "距离 BTC 加仓确认还差什么" — the feature that turns a daily anxiety
    ("can I buy yet?") into a falsifiable checklist ("my thesis still needs
    these three things to be true").
    """
    def item(key, label, ok, detail, target):
        return {"key": key, "label_cn": label, "passed": ok,
                "detail": detail, "target": target}

    items = []

    c30 = f.get("DGS30_chg_60d")
    items.append(item("long_end_stable", "30Y 不再持续上升",
                      None if c30 is None else c30 < 0.20,
                      f"30Y 60日 {c30*100:+.0f}bp" if c30 is not None else "数据缺失",
                      "< +20bp"))

    dx = f.get("dxy_chg_60d")
    items.append(item("dollar_weak", "美元转弱",
                      None if dx is None else dx < 0,
                      f"美元 60日 {dx:+.1f}%" if dx is not None else "数据缺失",
                      "< 0%"))

    rt = f.get("DFII10_chg_60d")
    items.append(item("real_yield_down", "实际利率下降",
                      None if rt is None else rt < -0.10,
                      f"10Y 实际利率 60日 {rt*100:+.0f}bp" if rt is not None else "数据缺失",
                      "< -10bp"))

    cy = f.get("cpi_yoy")
    items.append(item("inflation_high", "通胀保持高位",
                      None if cy is None else cy >= 2.5,
                      f"CPI YoY {cy:.1f}%" if cy is not None else "数据缺失",
                      "≥ 2.5%"))

    bg = f.get("btc_gold_ratio_chg_20d_pct")
    items.append(item("btc_gold_breakout", "BTC/黄金比价 20日走强",
                      None if bg is None else bg > 0,
                      f"BTC/黄金比价 20日 {bg:+.1f}%" if bg is not None else "数据缺失",
                      "> 0%"))

    nl = f.get("net_liquidity_chg_60d_pct")
    items.append(item("net_liquidity_up", "净流动性回升",
                      None if nl is None else nl > 0,
                      f"净流动性 60日 {nl:+.1f}%" if nl is not None else "数据缺失",
                      "> 0%"))

    done = sum(1 for x in items if x["passed"] is True)
    return {
        "current_signal": current,
        "current_signal_cn": CN.get(current, current),
        "target_signal": "Strong Bullish",
        "target_signal_cn": CN["Strong Bullish"],
        "items": items,
        "done": done,
        "total": len(items),
        "note": f"{done}/{len(items)} 项已满足"
                + ("（全部满足 ≠ 自动加仓，仍由你自己拍板）" if done == len(items) else ""),
    }


# ------------------------------------------------------ posture & trend ----
# Posture is about EXPOSURE to the named asset, derived from the signal level.
# It is still a risk-posture label, not an order: there is no size, no price and
# no timing here, and there never will be in this codebase.
POSTURE = {
    5: ("增持",     "Increase",   "↑↑"),
    4: ("逐步增持", "Gradual add", "↑"),
    3: ("维持",     "Hold",       "→"),
    2: ("降低风险", "De-risk",    "↓"),
    1: ("减持",     "Reduce",     "↓"),
    0: ("大幅减持", "Cut",        "↓↓"),
}


def posture(idx: int) -> dict:
    cn, en, arrow = POSTURE[max(0, min(5, idx))]
    return {"action_cn": cn, "action_en": en, "arrow": arrow}


def trajectory(history: list[dict | None], asset: str) -> dict:
    """
    Signal now vs 5 / 20 / 60 trading days ago.

    This is the honest substitute for a forecast. The engine cannot tell you
    where BTC is going, but it can tell you whether the macro case for BTC has
    been strengthening or weakening, and for how long — which is the question
    you can actually act on.
    """
    def at(n: int) -> str | None:
        if len(history) <= n or history[-1 - n] is None:
            return None
        return history[-1 - n][asset]["signal"]

    now = at(0)
    out = {"now": now, "now_cn": CN.get(now or "", "—")}
    for tag, n in (("d5", 5), ("d20", 20), ("d60", 60)):
        prev = at(n)
        if prev is None or now is None:
            out[tag] = None
            continue
        di = LEVELS.index(now) - LEVELS.index(prev)
        out[tag] = {"prev": prev, "prev_cn": CN[prev], "delta": di,
                    "arrow": "↑" if di > 0 else ("↓" if di < 0 else "→")}

    # how long has the current label held
    streak = 0
    for h in reversed(history):
        if h is None or h[asset]["signal"] != now:
            break
        streak += 1
    out["days_held"] = streak

    # Summarise on the 20-day view, but never say "no change" while a shorter
    # window disagrees — a flat 20d with a +1 five-day move is a recent turn,
    # not a quiet stretch, and printing "无变化" next to a "5日 ↑+1" chip is
    # simply wrong.
    d5, d20 = out.get("d5"), out.get("d20")
    if d20 is None and d5 is None:
        out["summary_cn"] = "历史不足，无法判断趋势"
    elif d20 and d20["delta"] > 0:
        out["summary_cn"] = (f"过去20个交易日从「{d20['prev_cn']}」上调至"
                             f"「{out['now_cn']}」，宏观理由在增强")
    elif d20 and d20["delta"] < 0:
        out["summary_cn"] = (f"过去20个交易日从「{d20['prev_cn']}」下调至"
                             f"「{out['now_cn']}」，宏观理由在减弱")
    elif d5 and d5["delta"] != 0:
        word = "上调" if d5["delta"] > 0 else "下调"
        out["summary_cn"] = (f"近5个交易日从「{d5['prev_cn']}」{word}至"
                             f"「{out['now_cn']}」（20日净持平），刚出现转向")
    else:
        out["summary_cn"] = f"已在「{out['now_cn']}」维持 {streak} 个交易日，无变化"
    return out
