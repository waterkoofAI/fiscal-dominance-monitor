"""
Thesis breakers — the part of this system that tries to prove the thesis WRONG.

The core hypothesis is:
    Fiscal Dominance -> Financial Repression -> Gold/BTC bull

A monitor that only ever looks for evidence supporting its own hypothesis is a
confirmation-bias machine with a nice UI. Each breaker below is a specific,
pre-registered pattern that, if observed, means the thesis is weakening. They
are evaluated every day with the same rigour as the supporting scores and are
shown at the same prominence on the phone.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Breaker:
    key: str
    name_cn: str
    active: bool | None
    severity: float          # 0..1 when active
    evidence: str
    implication_cn: str

    def to_dict(self) -> dict:
        return asdict(self)


# A breaker that has just tripped carries a floor severity. Without it a
# breaker firing exactly at its threshold contributes ~0 penalty, which makes
# "2 breakers active" cosmetic. Firing at all has to cost something.
SEVERITY_FLOOR = 0.35


def _sev(x: float, lo: float, hi: float) -> float:
    if hi == lo:
        return SEVERITY_FLOOR
    scaled = max(0.0, min(1.0, (x - lo) / (hi - lo)))
    return SEVERITY_FLOOR + (1.0 - SEVERITY_FLOOR) * scaled


def evaluate(f: dict, sc: dict) -> dict:
    out: list[Breaker] = []

    # A: long end AND real yields both rising == real-rate repricing, not
    #    fiscal risk premium. The bull case for gold/BTC weakens.
    c30, rt = f.get("DGS30_chg_60d"), f.get("DFII10_chg_60d")
    if c30 is None or rt is None:
        out.append(Breaker("A_real_rate_repricing", "真实利率重定价（非财政溢价）",
                           None, 0.0, "数据缺失", ""))
    else:
        active = c30 > 0.25 and rt > 0.20
        out.append(Breaker(
            "A_real_rate_repricing", "真实利率重定价（非财政溢价）", active,
            min(_sev(c30, 0.25, 0.80), _sev(rt, 0.20, 0.70)) if active else 0.0,
            f"30Y {c30*100:+.0f}bp / 实际利率 {rt*100:+.0f}bp（60日）",
            "市场在重新定价真实利率，而不是在给财政风险要溢价。这直接削弱金融压抑论。"))

    # B: inflation falling materially while real yields rise == disinflation,
    #    which is the ordinary (non-repression) resolution.
    ct = f.get("cpi_yoy_trend_3m")
    if ct is None or rt is None:
        out.append(Breaker("B_disinflation", "通胀实质回落 + 实际利率上行",
                           None, 0.0, "数据缺失", ""))
    else:
        active = ct < -0.40 and rt > 0.15
        out.append(Breaker(
            "B_disinflation", "通胀实质回落 + 实际利率上行", active,
            min(_sev(-ct, 0.40, 1.20), _sev(rt, 0.15, 0.60)) if active else 0.0,
            f"CPI YoY 3м {ct:+.2f}pp / 实际利率 {rt*100:+.0f}bp",
            "反通胀正常化路径，财政主导逻辑减弱。"))

    # C: dollar ripping while gold and BTC fall == debasement trade unwinding.
    dx, gr, br = f.get("dxy_chg_60d"), f.get("GOLD_ret_60d"), f.get("BTC_ret_60d")
    if dx is None or gr is None:
        out.append(Breaker("C_dollar_squeeze", "美元大涨 + 金/BTC 双杀",
                           None, 0.0, "数据缺失", ""))
    else:
        active = dx > 3.0 and gr < -2.0 and (br is None or br < -8.0)
        out.append(Breaker(
            "C_dollar_squeeze", "美元大涨 + 金/BTC 双杀", active,
            _sev(dx, 3.0, 8.0) if active else 0.0,
            f"美元 {dx:+.1f}% / 黄金 {gr:+.1f}%"
            + (f" / BTC {br:+.1f}%" if br is not None else ""),
            "贬值交易在平仓，可能是美元流动性收紧。此时 BTC/黄金的宏观理由不成立。"))

    # D: BTC up while gold down and real yields + dollar both up. BTC is then
    #    running on crypto-specific flows, NOT on the macro thesis. This is the
    #    breaker that stops you crediting the model for a rally it did not call.
    if br is None or gr is None or rt is None or dx is None:
        out.append(Breaker("D_crypto_idiosyncratic", "BTC 独涨（非宏观驱动）",
                           None, 0.0, "数据缺失", ""))
    else:
        active = br > 10.0 and gr < 0 and rt > 0.10 and dx > 0
        out.append(Breaker(
            "D_crypto_idiosyncratic", "BTC 独涨（非宏观驱动）", active,
            _sev(br, 10.0, 40.0) if active else 0.0,
            f"BTC {br:+.1f}% / 黄金 {gr:+.1f}% / 实际利率 {rt*100:+.0f}bp / 美元 {dx:+.1f}%",
            "BTC 上涨来自加密圈内部流动性而非宏观贬值。别把这波涨幅记在宏观假设头上。"))

    # E: driver/confirmation divergence — the model's causal story and the
    #    market's verdict disagree. Not fatal, but it is the honest warning that
    #    one of the two halves is wrong.
    dv, cv = sc.get("driver_composite"), sc.get("confirmation_composite")
    if dv is None or cv is None:
        out.append(Breaker("E_driver_confirm_gap", "驱动与市场确认背离",
                           None, 0.0, "数据缺失", ""))
    else:
        gap = dv - cv
        active = abs(gap) > 25
        out.append(Breaker(
            "E_driver_confirm_gap", "驱动与市场确认背离", active,
            _sev(abs(gap), 25, 60) if active else 0.0,
            f"驱动分 {dv:.0f} vs 市场确认分 {cv:.0f}（差 {gap:+.0f}）",
            ("宏观驱动已具备但市场未确认：要么你早了，要么驱动读错了。"
             if gap > 0 else
             "市场在涨但宏观驱动不支持：涨幅的原因不在这套框架里。")))

    active = [b for b in out if b.active is True]
    total = sum(b.severity for b in active)
    return {
        "breakers": [b.to_dict() for b in out],
        "active_count": len(active),
        "severity_total": round(total, 2),
        # A blunt haircut on how much the supporting scores deserve to be trusted
        "thesis_penalty": round(min(35.0, total * 18.0), 1),
    }
