"""
Narrative tracker.

Somebody posts a macro thesis. Parts of it are checkable, parts are not, and
the persuasive move is almost always to establish the checkable parts and then
let your confidence carry over to the unfalsifiable ones. This module makes
that separation permanent: each narrative is stored with pre-registered
CONFIRM and REFUTE conditions, plus an explicit list of the claims that CANNOT
be tested at all. Every day both sides are scored against live data.

Conditions are structured data, never expressions — there is no eval() here,
by design. A narrative file is untrusted input: it is written by whoever pasted
the thesis, and it must not be able to execute anything.

Registering the refute conditions AT THE SAME TIME as the confirm conditions is
the whole point. Deciding what would prove you wrong after you already know
which way the data went is not a test.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "narratives.json"

OPS = {
    ">":  lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<":  lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}


def load() -> list[dict]:
    if not LEDGER.exists():
        return []
    try:
        raw = json.loads(LEDGER.read_text())
    except json.JSONDecodeError:
        return []
    return raw if isinstance(raw, list) else raw.get("narratives", [])


def _fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "bp":
        return f"{value * 100:+.0f}bp"
    if unit == "pp":
        return f"{value:+.2f}pp"
    if unit == "%":
        return f"{value:+.1f}%"
    if unit == "pt":
        return f"{value:.1f}"
    return f"{value:+.2f}"


def _eval_cond(cond: dict, f: dict) -> dict:
    """Evaluate one condition. Returns passed=None when inputs are missing."""
    label = cond.get("label_cn", cond.get("id", "?"))

    for combiner in ("all", "any"):
        if combiner in cond:
            subs = [_eval_cond(c, f) for c in cond[combiner]]
            vals = [s["passed"] for s in subs]
            if any(v is None for v in vals):
                passed = None
            else:
                passed = all(vals) if combiner == "all" else any(vals)
            return {"id": cond.get("id"), "label_cn": label, "passed": passed,
                    "detail": " / ".join(s["detail"] for s in subs),
                    "sub": subs, "combiner": combiner}

    feat, op, target = cond.get("feature"), cond.get("op"), cond.get("value")
    unit = cond.get("unit", "")
    actual = f.get(feat)
    if actual is None or op not in OPS or target is None:
        return {"id": cond.get("id"), "label_cn": label, "passed": None,
                "detail": f"{feat} 数据缺失", "actual": None,
                "target": f"{op} {target}", "unit": unit}
    return {"id": cond.get("id"), "label_cn": label,
            "passed": bool(OPS[op](actual, target)),
            "detail": f"{_fmt(actual, unit)}（需 {op} {_fmt(target, unit)}）",
            "actual": actual, "target": f"{op} {target}", "unit": unit}


def _status(confirm_frac: float | None, refute_frac: float | None) -> tuple[str, str]:
    if confirm_frac is None or refute_frac is None:
        return "unknown", "数据不足"
    if refute_frac >= 0.50 and refute_frac > confirm_frac:
        return "refuted", "正在被证伪"
    if confirm_frac >= 0.60 and refute_frac <= 0.34:
        return "confirmed", "正在被证实"
    if confirm_frac >= 0.50 and refute_frac >= 0.50:
        return "mixed", "证据互相矛盾"
    return "neutral", "尚未定论"


def evaluate_one(n: dict, f: dict) -> dict:
    conf = [_eval_cond(c, f) for c in n.get("confirm", [])]
    refu = [_eval_cond(c, f) for c in n.get("refute", [])]

    def frac(rows):
        live = [r for r in rows if r["passed"] is not None]
        if not live:
            return None, 0, 0
        hit = sum(1 for r in live if r["passed"])
        return hit / len(live), hit, len(live)

    cf, ch, ct = frac(conf)
    rf, rh, rt = frac(refu)
    code, label = _status(cf, rf)

    return {
        "id": n.get("id"),
        "title": n.get("title", ""),
        "source": n.get("source", ""),
        "source_url": n.get("source_url", ""),
        "recorded_at": n.get("recorded_at", ""),
        "summary_cn": n.get("summary_cn", ""),
        "confirm": conf, "refute": refu,
        "confirm_hit": ch, "confirm_total": ct,
        "refute_hit": rh, "refute_total": rt,
        "confirm_frac": round(cf * 100, 0) if cf is not None else None,
        "refute_frac": round(rf * 100, 0) if rf is not None else None,
        "status": code, "status_cn": label,
        "untestable": n.get("untestable", []),
        "scale_checks": n.get("scale_checks", []),
    }


def evaluate(f: dict, narratives: list[dict] | None = None) -> dict:
    items = [evaluate_one(n, f) for n in (load() if narratives is None else narratives)]
    return {
        "narratives": items,
        "count": len(items),
        "note": "确认与证伪条件是在记录当天一并预注册的。事后再决定「什么算错」不是检验。"
                "每条叙事里被标为「无法检验」的部分，本工具不会给它任何分数——"
                "那不是它没通过，是它根本不进入检验。",
    }
