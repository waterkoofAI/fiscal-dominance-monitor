"""
Scenario weights.

The spec was explicit that scenario probabilities must come from transparent
rules, never from a model's feel. So each scenario here is scored from a small
set of NAMED sub-conditions, each of which is a 0..1 satisfaction value you can
read off the screen. The scenario score is their mean; the five scores are then
normalised linearly to sum to 100.

What these numbers ARE: "given today's data, how much of each scenario's
defining pattern is currently present".

What they are NOT: calibrated forecast probabilities. Nobody has 500 independent
financial-repression episodes to calibrate against — see ASSESSMENT.md §3. Do
not read "金融压抑 22%" as "22% chance of financial repression next year".
It means "22% of the total pattern-match weight currently sits on that path".
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Cond:
    label_cn: str
    value: float          # 0..1 satisfaction
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def _sat(x: float | None, lo: float, hi: float) -> float:
    """Linear satisfaction: lo -> 0, hi -> 1, clamped. hi<lo inverts."""
    if x is None:
        return 0.5                      # unknown == neutral, never 0
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


SCENARIOS = {
    "normalisation":         {"name_cn": "正常化",      "color": "#2ea043"},
    "fiscal_dominance":      {"name_cn": "财政主导",    "color": "#e8873a"},
    "financial_repression":  {"name_cn": "金融压抑",    "color": "#e5484d"},
    "qe_ycc":                {"name_cn": "QE / YCC",   "color": "#a457e8"},
    "debt_crisis":           {"name_cn": "债务危机",    "color": "#f0f0f0"},
}


def compute(f: dict, sc: dict, policy: dict) -> dict:
    cy   = f.get("cpi_yoy")
    ctr  = f.get("cpi_yoy_trend_3m")
    ry   = f.get("DFII10_level")
    rt   = f.get("DFII10_chg_60d")
    c30  = f.get("DGS30_chg_60d")
    l30  = f.get("DGS30_level")
    tp   = f.get("THREEFYTP10_level")
    tpc  = f.get("THREEFYTP10_chg_60d")
    dx   = f.get("dxy_chg_60d")
    gr   = f.get("GOLD_ret_60d")
    oas  = f.get("BAMLH0A0HYM2_level")
    oasc = f.get("BAMLH0A0HYM2_chg_60d")
    walcl = f.get("walcl_chg_60d_pct")
    fs   = sc["fiscal_stress"]["score"]
    fr   = sc["financial_repression"]["score"]

    conds: dict[str, list[Cond]] = {

        "normalisation": [
            Cond("通胀在回落", _sat(ctr, 0.20, -0.60),
                 f"CPI YoY 3м趋势 {ctr:+.2f}pp" if ctr is not None else "n/a"),
            Cond("实际利率回到正常区间", _sat(ry, 0.50, 2.20),
                 f"10Y 实际利率 {ry:+.2f}%" if ry is not None else "n/a"),
            Cond("长端未持续上行", _sat(c30, 0.40, -0.20),
                 f"30Y 60日 {c30*100:+.0f}bp" if c30 is not None else "n/a"),
            Cond("期限溢价不高", _sat(tp, 1.20, 0.10),
                 f"期限溢价 {tp:+.2f}%" if tp is not None else "n/a"),
            Cond("财政压力分低", _sat(fs, 60, 15), f"财政压力 {fs:.0f}"),
        ],

        "fiscal_dominance": [
            Cond("长端处于高位", _sat(l30, 4.20, 5.60),
                 f"30Y {l30:.2f}%" if l30 is not None else "n/a"),
            Cond("期限溢价抬升", _sat(tp, 0.00, 1.20),
                 f"期限溢价 {tp:+.2f}%" if tp is not None else "n/a"),
            Cond("期限溢价仍在走高", _sat(tpc, -0.15, 0.45),
                 f"期限溢价 60日 {tpc*100:+.0f}bp" if tpc is not None else "n/a"),
            Cond("通胀顽固", _sat(cy, 1.80, 3.60),
                 f"CPI YoY {cy:.1f}%" if cy is not None else "n/a"),
            Cond("财政部/联储已有干预", _sat(policy.get("fiscal_intervention_score", 0.0), 0, 25),
                 policy.get("fiscal_intervention_note", "无记录")),
            Cond("财政压力分高", _sat(fs, 25, 70), f"财政压力 {fs:.0f}"),
        ],

        "financial_repression": [
            Cond("实际利率在下行", _sat(rt, 0.15, -0.55),
                 f"实际利率 60日 {rt*100:+.0f}bp" if rt is not None else "n/a"),
            Cond("实际利率水平被压低", _sat(ry, 2.40, 0.30),
                 f"10Y 实际利率 {ry:+.2f}%" if ry is not None else "n/a"),
            Cond("通胀未下行", _sat(ctr, -0.60, 0.30),
                 f"CPI YoY 3м趋势 {ctr:+.2f}pp" if ctr is not None else "n/a"),
            Cond("长端被压住（未失控）", _sat(c30, 0.55, -0.30),
                 f"30Y 60日 {c30*100:+.0f}bp" if c30 is not None else "n/a"),
            Cond("美元走弱", _sat(dx, 2.5, -5.0),
                 f"美元 60日 {dx:+.1f}%" if dx is not None else "n/a"),
            Cond("已有压抑性政策动作", _sat(policy.get("repression_score", 0.0), 0, 30),
                 policy.get("repression_note", "无记录")),
        ],

        "qe_ycc": [
            Cond("已有 QE/YCC 政策事实", 1.0 if policy.get("stage4_facts") else 0.0,
                 "台账内有 fact" if policy.get("stage4_facts") else "台账内无 fact"),
            Cond("联储资产负债表在扩", _sat(walcl, -1.0, 3.0),
                 f"WALCL 60日 {walcl:+.1f}%" if walcl is not None else "n/a"),
            Cond("实际利率下行配合", _sat(rt, 0.10, -0.50),
                 f"实际利率 60日 {rt*100:+.0f}bp" if rt is not None else "n/a"),
            Cond("金融压抑分已高", _sat(fr, 30, 75), f"金融压抑 {fr:.0f}"),
        ],

        "debt_crisis": [
            Cond("长端失控上行", _sat(c30, 0.20, 0.90),
                 f"30Y 60日 {c30*100:+.0f}bp" if c30 is not None else "n/a"),
            Cond("期限溢价飙升", _sat(tpc, 0.10, 0.60),
                 f"期限溢价 60日 {tpc*100:+.0f}bp" if tpc is not None else "n/a"),
            Cond("信用利差走阔", _sat(oasc, 0.20, 1.80),
                 f"HY OAS 60日 {oasc*100:+.0f}bp" if oasc is not None else "n/a"),
            Cond("信用利差绝对水平高", _sat(oas, 3.20, 7.00),
                 f"HY OAS {oas:.2f}%" if oas is not None else "n/a"),
            Cond("避险失灵（美元与黄金同向异动）",
                 _sat(dx, -1.0, 5.0) * _sat(gr, 2.0, -8.0) if (dx is not None and gr is not None) else 0.5,
                 f"美元 {dx:+.1f}% / 黄金 {gr:+.1f}%" if (dx is not None and gr is not None) else "n/a"),
        ],
    }

    raw = {k: sum(c.value for c in v) / len(v) for k, v in conds.items()}
    total = sum(raw.values()) or 1.0
    pct = {k: round(v / total * 100.0, 1) for k, v in raw.items()}

    ranked = sorted(pct.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "weights": pct,
        "raw": {k: round(v, 3) for k, v in raw.items()},
        "ranked": [{"key": k, "name_cn": SCENARIOS[k]["name_cn"],
                    "color": SCENARIOS[k]["color"], "pct": p,
                    "conditions": [c.to_dict() for c in conds[k]]}
                   for k, p in ranked],
        "leader": ranked[0][0],
        "leader_cn": SCENARIOS[ranked[0][0]]["name_cn"],
        "note": "以上是「今天的数据有多符合各条路径的特征」的规则映射并归一化，"
                "不是校准过的预测概率。金融压抑这类体制几十年一遇，"
                "没有任何数据集能校准出它的真实发生概率。",
    }
