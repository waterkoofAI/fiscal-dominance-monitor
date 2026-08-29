"""
Deterministic Chinese narrative.

No LLM. The daily text is rendered from the rule engine's own outputs, which
means: no API key required, no cost, no drift, and the words on the phone can
never disagree with the numbers next to them. An LLM polish layer can be bolted
on later, but it must never be able to change a score, a stage, or a signal.
"""
from __future__ import annotations

from datetime import date

from . import config
from .signals import CN

ARROW = {1: "↑", -1: "↓", 0: "→"}


def _arrow(x: float | None, eps: float = 1e-9) -> str:
    if x is None:
        return "·"
    return ARROW[1] if x > eps else (ARROW[-1] if x < -eps else ARROW[0])


def _bp(x: float | None) -> str:
    return "n/a" if x is None else f"{x*100:+.0f}bp"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.1f}%"


DRIVER_CN = {
    "term_premium_rising": "期限溢价上行主导——市场在向财政部索要风险溢价",
    "term_premium_falling": "期限溢价回落主导——财政风险溢价在收敛",
    "real_rising": "真实利率上行主导——普通的实际利率重定价，非财政溢价",
    "real_falling": "真实利率下行主导——金融压抑方向",
    "breakeven_rising": "通胀预期上行主导——贬值预期在定价",
    "breakeven_falling": "通胀预期回落主导——反通胀方向",
    "mixed": "无单一主导因素（混合）",
    "flat": "长端基本持平",
    "rotation_real_up": "长端名义净变动很小，但内部发生轮动：实际利率上行、通胀预期回落。"
                        "这是偏鹰的实际利率重定价，不是财政主导。",
    "rotation_real_down": "长端名义净变动很小，但内部发生轮动：实际利率下行、通胀预期上行。"
                          "这正是金融压抑的内部特征。",
}


def today_lines(f: dict) -> list[dict]:
    """The 今天发生了什么 block."""
    rows = [
        ("30Y 美债", f.get("DGS30_level"), "%", f.get("DGS30_chg_5d"), "bp"),
        ("10Y 实际利率", f.get("DFII10_level"), "%", f.get("DFII10_chg_5d"), "bp"),
        ("10Y 期限溢价", f.get("THREEFYTP10_level"), "%", f.get("THREEFYTP10_chg_5d"), "bp"),
        ("美元 DXY", f.get("DXY_level"), "", f.get("DXY_ret_5d"), "%"),
        ("黄金", f.get("GOLD_level"), "$", f.get("GOLD_ret_5d"), "%"),
        ("BTC", f.get("BTC_level"), "$", f.get("BTC_ret_5d"), "%"),
    ]
    out = []
    for label, lvl, unit, chg, chg_unit in rows:
        if lvl is None:
            continue
        if unit == "$":
            lvl_s = f"${lvl:,.0f}"
        elif unit == "%":
            lvl_s = f"{lvl:.2f}%"
        else:
            lvl_s = f"{lvl:.2f}"
        chg_s = ("n/a" if chg is None else
                 (_bp(chg) if chg_unit == "bp" else f"{chg:+.1f}%"))
        out.append({"label": label, "value": lvl_s, "change": chg_s,
                    "arrow": _arrow(chg), "raw_change": chg})
    return out


def biggest_movers(f: dict, prev_sc: dict | None, sc: dict) -> list[str]:
    """Explain the 3 components that moved the composite most since yesterday."""
    if not prev_sc:
        return []
    deltas = []
    for block in ("fiscal_stress", "financial_repression", "debasement", "btc_liquidity"):
        cur = sc[block]["components"]
        old = (prev_sc.get(block) or {}).get("components", {})
        for k, v in cur.items():
            if v["missing"] or k not in old or old[k]["missing"]:
                continue
            d = v["points"] - old[k]["points"]
            if abs(d) >= 0.4:
                deltas.append((abs(d), d, block, k, v["note"]))
    deltas.sort(reverse=True)
    out = []
    for _, d, block, k, note in deltas[:3]:
        out.append(f"{block}·{k} {d:+.1f}分（{note}）")
    return out


def verdict(stage: int, sc: dict, f: dict, brk: dict, stab: dict,
            checklist: dict) -> str:
    """The one-paragraph 今日判断."""
    name = config.STAGE_DEFS[stage]["name_cn"]
    parts = [f"当前处于 Stage {stage}（{name}），综合 {sc['composite']:.0f}/100。"]

    dv, cv = sc.get("driver_composite"), sc.get("confirmation_composite")
    if dv is not None and cv is not None:
        gap = dv - cv
        if gap > 25:
            parts.append(f"宏观驱动（{dv:.0f}）明显强于市场确认（{cv:.0f}）："
                         "论点具备但价格尚未跟随，属于「早」或「读错」二选一。")
        elif gap < -25:
            parts.append(f"市场确认（{cv:.0f}）明显强于宏观驱动（{dv:.0f}）："
                         "价格在走但这套框架解释不了，涨幅别记在宏观假设头上。")
        else:
            parts.append(f"驱动 {dv:.0f} 与市场确认 {cv:.0f} 大体一致。")

    pe = f.get("policy_expectation")
    sp = f.get("policy_spread_2y")
    if pe and sp is not None:
        PE_CN = {
            "hiking": f"市场在定价加息（2年期高出政策利率上限 {sp*100:.0f}bp）。"
                      "这对金融压抑论是结构性不利：压抑要求联储被财政绑住并压低实际利率，"
                      "而定价加息说明联储仍在主张独立性。",
            "cutting": f"市场在定价降息（2年期低于政策利率上限 {abs(sp)*100:.0f}bp）。",
            "on_hold": f"市场定价按兵不动（2年期与政策利率上限相差 {sp*100:+.0f}bp）。",
        }
        parts.append(PE_CN[pe])

    drv = f.get("decomp_driver")
    if drv and drv in DRIVER_CN:
        # some labels already end in a full stop; do not double it up
        parts.append("长端归因：" + DRIVER_CN[drv].rstrip("。") + "。")

    if checklist.get("target"):
        parts.append(f"距离 Stage {checklist['target']}（{checklist['target_name']}）："
                     f"{checklist.get('note','')}。")

    if brk["active_count"] > 0:
        names = "、".join(b["name_cn"] for b in brk["breakers"] if b["active"])
        parts.append(f"⚠️ 反向信号触发 {brk['active_count']} 项（{names}），"
                     f"支持性评分已扣减 {brk['thesis_penalty']:.0f}。")
    else:
        parts.append("当前无反向信号触发。")

    if not stab["trustworthy"]:
        parts.append(f"⚠️ 过去90天阶段翻转 {stab['flips_90d']} 次，"
                     "分类器处于不稳定状态，这个标签本身别太当真。")
    else:
        parts.append(f"该阶段已持续 {stab['days_in_stage']} 天。")

    return "".join(parts)


def strategy_block(sig: dict, stage: int) -> list[dict]:
    stage_note = {
        0: "常态：无需为财政主导叙事额外承担风险。",
        1: "财政压力显现：黄金开始有配置理由，长久期美债降低。",
        2: "财政主导观察：黄金偏多，BTC 谨慎偏多，长端保持警惕，现金留一部分。",
        3: "金融压抑确认：黄金/BTC 高配的宏观条件成立，但仍是风险姿态而非指令。",
        4: "货币体制转换：政策事实已发生，贬值资产强偏多；同时警惕后段追涨风险。",
    }[stage]
    rows = []
    for key, label in (("gold", "黄金"), ("btc", "BTC"),
                       ("ust30", "长久期美债"), ("usd", "美元")):
        s = sig[key]
        rows.append({"asset_cn": label, "signal_cn": s["signal_cn"],
                     "emoji": s["emoji"], "reason": s["reason"]})
    return [{"stage_note": stage_note, "rows": rows}]


def build(when: date, stage: int, sc: dict, f: dict, brk: dict, stab: dict,
          checklist: dict, sig: dict, btc_check: dict,
          prev_sc: dict | None = None) -> dict:
    return {
        "date": when.isoformat(),
        "headline": f"Stage {stage} — {config.STAGE_DEFS[stage]['name_cn']}"
                    f"（{config.STAGE_DEFS[stage]['name']}）",
        "today": today_lines(f),
        "movers": biggest_movers(f, prev_sc, sc),
        "verdict": verdict(stage, sc, f, brk, stab, checklist),
        "strategy": strategy_block(sig, stage),
        "btc_checklist_note": btc_check["note"],
        "disclaimer": "本工具输出的是规则化风险姿态标签，不是交易指令，"
                      "不构成投资建议。所有分数与阶段由确定性规则计算，"
                      "文字由模板渲染，不经过语言模型。",
    }
