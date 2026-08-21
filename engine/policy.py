"""
Policy ledger.

Deliberate design decision, and the one most likely to be argued with:
POLICY EVENTS ARE NOT SCRAPED FROM NEWS. They are read from a human-maintained
JSON ledger where every entry carries a source_url and an explicit
fact_or_inference flag.

Why: Stage 4 (QE/YCC) is the single highest-consequence output of this system,
and the original spec had it triggered by an LLM reading headlines every
morning. That is the exact shape of failure where a model reads
"Fed officials discussed balance sheet policy" and prints MONETARY REGIME SHIFT.
Only `fact` entries score. Only `fact` entries can trigger Stage 4.
Inference entries are displayed, clearly labelled, and never touch a number.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path

from . import config

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "policy_events.json"

FISCAL_INTERVENTION_TYPES = {
    "buyback_routine", "buyback_expanded", "issuance_shift_to_bills",
    "long_yield_discussion",
}
REPRESSION_TYPES = {
    "buyback_expanded", "long_yield_discussion", "qt_taper", "qt_end",
    "emergency_facility_small", "emergency_facility_large",
    "balance_sheet_expansion_confirmed", "qe_announced",
    "ycc_announced", "yield_target_announced",
}


def load_events() -> list[dict]:
    if not LEDGER.exists():
        return []
    try:
        raw = json.loads(LEDGER.read_text())
    except json.JSONDecodeError:
        return []
    out = []
    for e in raw if isinstance(raw, list) else raw.get("events", []):
        if not e.get("date") or not e.get("event_type"):
            continue
        e.setdefault("fact_or_inference", "inference")
        e.setdefault("source_url", "")
        e.setdefault("institution", "unknown")
        out.append(e)
    return sorted(out, key=lambda x: x["date"])


def _decayed(points: float, age_days: int) -> float:
    if age_days < 0:
        return 0.0
    return points * math.pow(0.5, age_days / config.POLICY_EVENT_HALFLIFE_DAYS)


def evaluate(when: date, events: list[dict] | None = None) -> dict:
    """Aggregate ledger state as of `when`. Never looks into the future."""
    events = load_events() if events is None else events
    facts, infers, s4 = [], [], []
    fiscal_pts = repress_pts = 0.0

    for e in events:
        try:
            ed = datetime.strptime(e["date"][:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if ed > when:
            continue
        age = (when - ed).days
        if e["fact_or_inference"] != "fact":
            if age <= 180:
                infers.append(e)
            continue
        base = config.POLICY_EVENT_SCORES.get(e["event_type"], 0)
        pts = _decayed(base, age)
        if e["event_type"] in FISCAL_INTERVENTION_TYPES:
            fiscal_pts += max(0.0, pts)
        if e["event_type"] in REPRESSION_TYPES:
            repress_pts += max(0.0, pts)
        if age <= 365:
            facts.append({**e, "age_days": age, "decayed_points": round(pts, 1)})
        # Stage 4 trigger facts are only "live" for a bounded window. Staying
        # live for a year is what let a tapered-away programme pin the label.
        if e["event_type"] in config.STAGE4_TRIGGER_EVENTS \
                and age <= config.STAGE4_FACT_WINDOW_DAYS:
            s4.append({**e, "age_days": age})

    def _note(items, kind):
        live = [x for x in items if x.get("age_days", 999) <= 120]
        if not live:
            return "近期无记录"
        top = max(live, key=lambda x: x["decayed_points"])
        return f"{top['title'][:44]} ({top['age_days']}天前)"

    return {
        "fiscal_intervention_score": round(fiscal_pts, 1),
        "fiscal_intervention_note": _note(facts, "fiscal"),
        "repression_score": round(repress_pts, 1),
        "repression_note": _note(facts, "repression"),
        "stage4_facts": s4,
        "recent_facts": facts[-12:],
        "recent_inferences": infers[-8:],
        "ledger_size": len(events),
    }
