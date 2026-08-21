"""
Stage machine.

Two things this does that the original spec did not:

1. GATES ARE FIRST-CLASS OBJECTS. Each stage's entry conditions are a list of
   named, individually-evaluated gates carrying (passed, actual, target). That
   is what powers "距离 Stage 3 还差什么" on the phone — the checklist is the
   rule engine's own internals, not a hand-written list that can drift.

2. HYSTERESIS. A regime label that flips every three days is noise wearing a
   regime's clothes. Entering a stage needs ENTRY_PERSISTENCE consecutive
   qualifying days; leaving needs the score to fall EXIT_BUFFER points below
   the entry threshold. We also report flip counts so you can tell at a glance
   whether the classifier is being decisive or just twitchy.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date

from . import config


@dataclass
class Gate:
    key: str
    label_cn: str
    passed: bool | None          # None == cannot evaluate (missing data)
    actual: float | None
    target: str
    detail: str = ""
    # Numeric threshold + comparison direction, so the UI can answer
    # "how far away is this?" instead of only "pass/fail".
    threshold: float | None = None
    direction: str = ""          # ">=" | "<=" | ">" | "<"
    unit: str = ""               # "bp" | "%" | "pt"

    def gap(self) -> float | None:
        if self.actual is None or self.threshold is None:
            return None
        return self.actual - self.threshold

    def to_dict(self) -> dict:
        d = asdict(self)
        d["gap"] = self.gap()
        return d


# ------------------------------------------------------------ gate defs ----
def stage3_gates(f: dict, sc: dict) -> list[Gate]:
    """Directional hard gates for Financial Repression. ALL must pass."""
    g: list[Gate] = []

    ct = f.get("cpi_yoy_trend_3m")
    g.append(Gate("inflation_not_falling", "通胀未在下行",
                  None if ct is None else ct >= -0.15, ct,
                  "CPI YoY 3个月变化 ≥ -0.15pp",
                  "" if ct is None else f"实际 {ct:+.2f}pp", -0.15, ">=", "pp"))

    rt = f.get("DFII10_chg_60d")
    g.append(Gate("real_yield_falling", "实际利率下行",
                  None if rt is None else rt < -0.10, rt,
                  "10Y 实际利率 60日变化 < -10bp",
                  "" if rt is None else f"实际 {rt*100:+.0f}bp", -0.10, "<", "bp"))

    dx = f.get("dxy_chg_60d")
    g.append(Gate("dollar_not_rising", "美元未走强",
                  None if dx is None else dx <= 1.0, dx,
                  "美元 60日 ≤ +1.0%",
                  "" if dx is None else f"实际 {dx:+.1f}%", 1.0, "<=", "%"))

    gr = f.get("GOLD_ret_60d")
    g.append(Gate("gold_rising", "黄金上行",
                  None if gr is None else gr > 0, gr,
                  "黄金 60日 > 0%",
                  "" if gr is None else f"实际 {gr:+.1f}%", 0.0, ">", "%"))

    c30 = f.get("DGS30_chg_60d")
    g.append(Gate("long_end_not_spiraling", "长端未失控上行",
                  None if c30 is None else c30 < 0.60, c30,
                  "30Y 60日变化 < +60bp",
                  "" if c30 is None else f"实际 {c30*100:+.0f}bp", 0.60, "<", "bp"))

    fr = sc["financial_repression"]["score"]
    g.append(Gate("repression_score", "金融压抑分 ≥ 65", fr >= 65, fr,
                  "Financial Repression ≥ 65", f"实际 {fr:.1f}", 65.0, ">=", "pt"))
    return g


def stage2_conditions(f: dict, sc: dict, policy: dict) -> list[Gate]:
    """Supporting conditions for Fiscal Dominance Watch. Need >= 2 of these."""
    g: list[Gate] = []

    pc, lv = f.get("DGS30_pctile"), f.get("DGS30_level")
    ok = None if (pc is None and lv is None) else ((pc or 0) >= 70 or (lv or 0) >= 5.0)
    g.append(Gate("long_end_elevated", "长端处于高位", ok, lv,
                  "30Y ≥ 5.0% 或 3年百分位 ≥ 70",
                  f"30Y {lv:.2f}%" + (f" / {pc:.0f}th" if pc is not None else "")
                  if lv is not None else "", 5.0, ">=", "%"))

    pi = policy.get("fiscal_intervention_score", 0.0)
    g.append(Gate("treasury_intervention", "财政部/联储有干预动作", pi > 0, pi,
                  "政策台账干预分 > 0", policy.get("fiscal_intervention_note", "无记录"),
                  0.0, ">", "pt"))

    cy, ctr = f.get("cpi_yoy"), f.get("cpi_yoy_trend_3m")
    ok = None if cy is None else (cy >= 2.5 and (ctr is None or ctr >= -0.4))
    g.append(Gate("inflation_sticky", "通胀顽固", ok, cy,
                  "CPI YoY ≥ 2.5% 且未快速回落",
                  f"CPI {cy:.1f}%" + (f", 3м趋势 {ctr:+.2f}pp" if ctr is not None else "")
                  if cy is not None else "", 2.5, ">=", "%"))

    tp = f.get("THREEFYTP10_level")
    g.append(Gate("term_premium_elevated", "期限溢价抬升",
                  None if tp is None else tp >= 0.50, tp,
                  "10Y 期限溢价 ≥ 0.50%",
                  f"实际 {tp:+.2f}%" if tp is not None else "", 0.50, ">=", "%"))

    dx = f.get("dxy_chg_60d")
    g.append(Gate("dollar_weakening", "美元走弱",
                  None if dx is None else dx < 0, dx,
                  "美元 60日 < 0%",
                  f"实际 {dx:+.1f}%" if dx is not None else "", 0.0, "<", "%"))
    return g


def stage4_gate(policy: dict, f: dict) -> Gate:
    """
    Stage 4 needs BOTH halves and can never be entered on inference:
      (a) a recorded policy FACT (QE/YCC/confirmed expansion) inside the
          trigger window, with a source URL; and
      (b) corroboration in the data — the Fed balance sheet is actually
          expanding right now.
    Either half alone is not a monetary regime shift.
    """
    hits = policy.get("stage4_facts", [])
    bs = f.get("walcl_chg_60d_pct")
    bs_ok = bs is not None and bs >= config.STAGE4_BALANCE_SHEET_MIN_60D_PCT
    passed = bool(hits) and bool(bs_ok)
    titles = "; ".join(h.get("title", "")[:40] for h in hits) if hits else "无近期政策事实"
    bs_txt = f"扩表 60日 {bs:+.1f}%" if bs is not None else "扩表数据缺失"
    return Gate("policy_fact_plus_expansion", "政策事实 + 扩表同时成立",
                passed, float(len(hits)),
                f"台账内 {config.STAGE4_FACT_WINDOW_DAYS} 天内 QE/YCC 事实 "
                f"且 WALCL 60日 ≥ +{config.STAGE4_BALANCE_SHEET_MIN_60D_PCT}%",
                f"{titles} | {bs_txt}")


# ------------------------------------------------------- raw resolution ----
def raw_stage(f: dict, sc: dict, policy: dict) -> tuple[int, dict]:
    fs = sc["fiscal_stress"]["score"]
    fr = sc["financial_repression"]["score"]

    g4 = stage4_gate(policy, f)
    g3 = stage3_gates(f, sc)
    g2 = stage2_conditions(f, sc, policy)

    n2 = sum(1 for x in g2 if x.passed is True)
    all3 = all(x.passed is True for x in g3)

    if g4.passed:
        stage = 4
    elif all3:
        stage = 3
    elif fs >= config.STAGE_THRESHOLDS[2]["fiscal_stress_min"] and \
            n2 >= config.STAGE_THRESHOLDS[2]["extra_conditions_required"]:
        stage = 2
    elif fs >= config.STAGE_THRESHOLDS[1]["fiscal_stress_min"]:
        stage = 1
    else:
        stage = 0

    detail = {
        "stage4_gate": g4.to_dict(),
        "stage3_gates": [x.to_dict() for x in g3],
        "stage3_passed": sum(1 for x in g3 if x.passed is True),
        "stage3_total": len(g3),
        "stage2_conditions": [x.to_dict() for x in g2],
        "stage2_passed": n2,
        "stage2_required": config.STAGE_THRESHOLDS[2]["extra_conditions_required"],
        "fiscal_stress": fs,
        "financial_repression": fr,
    }
    return stage, detail


# ----------------------------------------------------------- hysteresis ----
def apply_hysteresis(raw: list[int], fs_series: list[float],
                     fr_series: list[float]) -> list[int]:
    """
    Whole-sequence pass. Deterministic and stateless from the caller's point of
    view: the daily job replays the full history every run, so there is no
    state file that can silently rot.
    """
    if not raw:
        return []
    P, BUF = config.ENTRY_PERSISTENCE, config.EXIT_BUFFER
    out = [raw[0]]
    cur = raw[0]
    streak_val, streak_len = raw[0], 1

    for i in range(1, len(raw)):
        r = raw[i]
        streak_len = streak_len + 1 if r == streak_val else 1
        streak_val = r

        if r > cur:
            # upgrade: needs P consecutive days at the higher label
            if streak_len >= P:
                cur = r
        elif r < cur:
            # downgrade: needs P consecutive days AND the driving score must
            # have fallen clearly below the entry threshold, not just grazed it
            if streak_len >= P:
                if cur == 3:
                    thr = 65 - BUF
                    if fr_series[i] < thr:
                        cur = r
                elif cur == 2:
                    thr = config.STAGE_THRESHOLDS[2]["fiscal_stress_min"] - BUF
                    if fs_series[i] < thr:
                        cur = r
                elif cur == 1:
                    thr = config.STAGE_THRESHOLDS[1]["fiscal_stress_min"] - BUF
                    if fs_series[i] < thr:
                        cur = r
                else:
                    cur = r
        out.append(cur)
    return out


def stage_stability(stages: list[int], window: int = 90) -> dict:
    """Days in current stage + flip count. High flip count == distrust the label."""
    if not stages:
        return {"days_in_stage": 0, "flips_90d": 0, "trustworthy": False}
    cur, days = stages[-1], 1
    for i in range(len(stages) - 2, -1, -1):
        if stages[i] == cur:
            days += 1
        else:
            break
    tail = stages[-window:]
    flips = sum(1 for i in range(1, len(tail)) if tail[i] != tail[i - 1])
    return {"days_in_stage": days, "flips_90d": flips,
            "trustworthy": flips <= 4}


def next_stage_checklist(stage: int, detail: dict) -> dict:
    """What the phone shows under 距离下一阶段还差什么."""
    if stage >= 4:
        return {"target": None, "items": [],
                "note": "已处于最高阶段（货币体制转换）"}
    if stage == 3:
        g = detail["stage4_gate"]
        return {"target": 4, "target_name": config.STAGE_DEFS[4]["name_cn"],
                "items": [g],
                "note": "Stage 4 只能由政策事实触发，不接受推测"}
    if stage == 2:
        items = detail["stage3_gates"]
        done = sum(1 for x in items if x["passed"] is True)
        return {"target": 3, "target_name": config.STAGE_DEFS[3]["name_cn"],
                "items": items, "done": done, "total": len(items),
                "note": f"需全部满足，当前 {done}/{len(items)}"}
    # stage 0 or 1 -> next is 2
    items = detail["stage2_conditions"]
    done = detail["stage2_passed"]
    req = detail["stage2_required"]
    fs_gate = {"key": "fiscal_stress_min", "label_cn": "财政压力分 ≥ 55",
               "passed": detail["fiscal_stress"] >= 55, "actual": detail["fiscal_stress"],
               "target": "Fiscal Stress ≥ 55", "detail": f"实际 {detail['fiscal_stress']:.1f}",
               "threshold": 55.0, "direction": ">=", "unit": "pt",
               "gap": detail["fiscal_stress"] - 55.0}
    fs_ok = detail["fiscal_stress"] >= 55
    blocking = []
    if not fs_ok:
        blocking.append(f"财政压力分 {detail['fiscal_stress']:.0f} 未达 55")
    if done < req:
        blocking.append(f"辅助条件 {done}/{req}")
    note = ("两项门槛均已满足" if not blocking
            else "尚缺：" + "、".join(blocking)
                 + (f"（辅助条件 {done} 项已达标，需 {req} 项）" if done >= req else ""))
    return {"target": 2, "target_name": config.STAGE_DEFS[2]["name_cn"],
            "items": [fs_gate] + items, "done": done, "total": req,
            "note": note}


def nearest_triggers(checklist: dict, limit: int = 3) -> list[dict]:
    """
    The unmet conditions closest to flipping, with the numeric distance.

    This is the "还差多少" answer: not "you need 4 more things", but
    "30Y needs another 31bp and the dollar needs to turn".

    Careful with compound gates. "通胀顽固" needs CPI >= 2.5% AND inflation not
    collapsing; if CPI is already 3.5% the gate can still fail on its second
    half, and naively reporting |actual - threshold| would print a confident
    "还差 1.04%" that is simply false. When the primary threshold is already
    satisfied we say so instead of inventing a number.
    """
    SCALE = {"bp": 0.10, "pp": 0.30, "%": 1.0, "pt": 10.0}
    CMP = {">=": lambda a, t: a >= t, ">": lambda a, t: a > t,
           "<=": lambda a, t: a <= t, "<": lambda a, t: a < t}

    out = []
    for it in checklist.get("items", []):
        if it.get("passed") is True:
            continue
        thr, act = it.get("threshold"), it.get("actual")
        direction = it.get("direction", "")
        if thr is None or act is None or direction not in CMP:
            continue
        unit = it.get("unit", "")
        row = {"key": it.get("key"), "label_cn": it.get("label_cn"),
               "unit": unit, "direction": direction,
               "actual": act, "threshold": thr}

        if CMP[direction](act, thr):
            # primary threshold already met — the gate fails on another clause
            row.update(need=None, distance=1e6, primary_met=True,
                       need_cn="主阈值已满足，卡在该条件的另一半")
        else:
            need = abs(act - thr)
            if need < 1e-9:
                row.update(need=0.0, distance=0.0, primary_met=False,
                           need_cn="就差一点（需严格超过阈值）")
            else:
                row.update(
                    need=need, distance=need / SCALE.get(unit, 1.0),
                    primary_met=False,
                    need_cn=(f"还差 {need*100:.0f}bp" if unit == "bp"
                             else f"还差 {need:.2f}pp" if unit == "pp"
                             else f"还差 {need:.1f} 分" if unit == "pt"
                             else f"还差 {need:.2f}%"))
        out.append(row)

    out.sort(key=lambda x: x["distance"])
    return out[:limit]
