#!/usr/bin/env python3
"""
Threshold sensitivity analysis.

Every threshold in this system is a human prior — 30Y > 5.0%, repression >= 65,
term premium >= 0.50. Round numbers chosen by judgement, never fitted. That is
defensible ONLY if the output is not delicately balanced on them. If nudging a
threshold by 10% rewrites the stage history, the model is false precision and
its confident-looking labels are noise.

This perturbs the load-bearing thresholds and measures how much of the stage
history survives. Run: python3 sensitivity.py
"""
from __future__ import annotations

import copy
import random
import statistics
from datetime import datetime

from engine import config, features, policy, scores
from engine.stages import apply_hysteresis, raw_stage
from backtest import load_cached_panel

START = "2010-01-01"


def replay_stages(p, events) -> list[int]:
    d0 = datetime.strptime(START, "%Y-%m-%d").date()
    cal = [d for d in p.calendar if d >= d0]
    raws, fs, fr = [], [], []
    for d in cal:
        f = features.compute_features(p, d)
        pol = policy.evaluate(d, events)
        sc = scores.compute_all(f, pol)
        rs, _ = raw_stage(f, sc, pol)
        raws.append(rs)
        fs.append(sc["fiscal_stress"]["score"])
        fr.append(sc["financial_repression"]["score"])
    return apply_hysteresis(raws, fs, fr)


def agreement(a: list[int], b: list[int]) -> float:
    n = min(len(a), len(b))
    return 100.0 * sum(1 for i in range(n) if a[i] == b[i]) / n


def perturb(scale: float, seed: int) -> None:
    """Scale every load-bearing threshold by ~(1 +/- scale)."""
    rng = random.Random(seed)
    j = lambda: 1.0 + rng.uniform(-scale, scale)

    config.STAGE_THRESHOLDS[1]["fiscal_stress_min"] = 35 * j()
    config.STAGE_THRESHOLDS[2]["fiscal_stress_min"] = 55 * j()
    config.BANDS["DGS30"] = [(4.50 * j(), 0), (5.00 * j(), 4), (5.30 * j(), 8),
                             (5.70 * j(), 12), (99.0, 15)]
    config.BANDS["THREEFYTP10"] = [(0.00, 0), (0.50 * j(), 3), (1.00 * j(), 6),
                                   (1.50 * j(), 9), (99.0, 12)]
    config.BANDS["DFII10_low"] = [(0.50 * j(), 12), (1.00 * j(), 9), (1.50 * j(), 6),
                                  (2.00 * j(), 3), (99.0, 0)]
    config.ENTRY_PERSISTENCE = max(1, round(3 * j()))
    config.EXIT_BUFFER = 6.0 * j()


def main() -> None:
    print("[sens] loading panel ...")
    p = load_cached_panel()
    events = policy.load_events()

    pristine = copy.deepcopy({
        "ST": config.STAGE_THRESHOLDS, "B": config.BANDS,
        "EP": config.ENTRY_PERSISTENCE, "EB": config.EXIT_BUFFER,
    })

    print("[sens] baseline ...")
    base = replay_stages(p, events)
    print(f"[sens] baseline n={len(base)}")

    for scale in (0.05, 0.10, 0.20):
        aggs, s3, s2 = [], [], []
        for seed in range(12):
            config.STAGE_THRESHOLDS = copy.deepcopy(pristine["ST"])
            config.BANDS = copy.deepcopy(pristine["B"])
            config.ENTRY_PERSISTENCE = pristine["EP"]
            config.EXIT_BUFFER = pristine["EB"]
            perturb(scale, seed)
            st = replay_stages(p, events)
            aggs.append(agreement(base, st))
            s3.append(100.0 * sum(1 for x in st if x == 3) / len(st))
            s2.append(100.0 * sum(1 for x in st if x == 2) / len(st))
        base3 = 100.0 * sum(1 for x in base if x == 3) / len(base)
        base2 = 100.0 * sum(1 for x in base if x == 2) / len(base)
        print(f"\n±{scale*100:.0f}% 扰动 (12 次随机):")
        print(f"  与基线逐日一致率  中位 {statistics.median(aggs):5.1f}%  "
              f"最差 {min(aggs):5.1f}%")
        print(f"  Stage 3 占比      基线 {base3:.2f}%  扰动后 "
              f"{min(s3):.2f}%–{max(s3):.2f}%")
        print(f"  Stage 2 占比      基线 {base2:.2f}%  扰动后 "
              f"{min(s2):.2f}%–{max(s2):.2f}%")

    config.STAGE_THRESHOLDS = copy.deepcopy(pristine["ST"])
    config.BANDS = copy.deepcopy(pristine["B"])
    config.ENTRY_PERSISTENCE = pristine["EP"]
    config.EXIT_BUFFER = pristine["EB"]


if __name__ == "__main__":
    main()
