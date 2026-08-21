"""
Engine tests. These guard the invariants that make the output trustworthy —
weights summing correctly, no look-ahead, determinism, hysteresis actually
damping, and Stage 4 being unreachable without a policy fact.

Run:  python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import (breakers, config, features, policy, scenarios,
                    scores, signals, stages)
from engine.features import Panel


def mk_panel(**series) -> Panel:
    raw = {}
    for sid, obs in series.items():
        meta = config.FRED_SERIES.get(sid, {})
        raw[sid] = {"observations": obs, "label": sid,
                    "freq": meta.get("freq", "d"),
                    "release_lag_days": meta.get("release_lag_days", 1),
                    "source": "test", "source_url": "", "from_cache": False}
    return Panel(raw)


def daily(start: str, values: list[float]) -> list[tuple[str, float]]:
    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(days=i)).isoformat(), v) for i, v in enumerate(values)]


class TestConfig(unittest.TestCase):
    def test_weights_sum_to_100(self):
        for name in ("FISCAL_STRESS", "FINANCIAL_REPRESSION", "DEBASEMENT", "BTC_LIQUIDITY"):
            self.assertAlmostEqual(sum(getattr(config, name).values()), 100.0, places=6,
                                   msg=f"{name} must sum to 100")

    def test_composite_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(config.COMPOSITE_WEIGHTS.values()), 1.0, places=9)

    def test_driver_components_exist(self):
        for block, comps in config.DRIVER_COMPONENTS.items():
            weights = {"fiscal_stress": config.FISCAL_STRESS,
                       "financial_repression": config.FINANCIAL_REPRESSION,
                       "debasement": config.DEBASEMENT,
                       "btc_liquidity": config.BTC_LIQUIDITY}[block]
            for c in comps:
                self.assertIn(c, weights, f"{block}.{c} is not a real component")

    def test_stage4_triggers_are_scored_events(self):
        for t in config.STAGE4_TRIGGER_EVENTS:
            self.assertIn(t, config.POLICY_EVENT_SCORES)


class TestReleaseLag(unittest.TestCase):
    """The single most important correctness property: no look-ahead."""

    def test_observation_invisible_before_release(self):
        p = mk_panel(CPIAUCSL=[("2026-07-01", 332.8)])
        lag = config.FRED_SERIES["CPIAUCSL"]["release_lag_days"]
        self.assertIsNone(p.asof("CPIAUCSL", date(2026, 7, 1)),
                          "July CPI must not be visible on July 1")
        self.assertIsNone(p.asof("CPIAUCSL", date(2026, 7, 1) + timedelta(days=lag - 1)))
        self.assertEqual(p.asof("CPIAUCSL", date(2026, 7, 1) + timedelta(days=lag)), 332.8)

    def test_asof_never_returns_future_value(self):
        p = mk_panel(DGS30=daily("2026-01-01", [4.0, 4.5, 5.0, 5.5, 6.0]))
        for i in range(5):
            when = date(2026, 1, 1) + timedelta(days=i)
            v = p.asof("DGS30", when)
            if v is not None:
                self.assertLessEqual(v, [4.0, 4.5, 5.0, 5.5, 6.0][i],
                                     "asof leaked a future observation")


class TestScoreHelpers(unittest.TestCase):
    def test_band_monotonic(self):
        b = config.BANDS["DGS30"]
        self.assertEqual(scores.band(4.0, b), 0)
        self.assertEqual(scores.band(4.9, b), 4)
        self.assertEqual(scores.band(5.2, b), 8)
        self.assertEqual(scores.band(6.0, b), 15)
        self.assertIsNone(scores.band(None, b))

    def test_ramp_clamps(self):
        self.assertEqual(scores.ramp(-99, 0, 10, 20), 0.0)
        self.assertEqual(scores.ramp(999, 0, 10, 20), 20.0)
        self.assertAlmostEqual(scores.ramp(5, 0, 10, 20), 10.0)
        self.assertAlmostEqual(scores.ramp(5, 0, 10, 20, invert=True), 10.0)
        self.assertAlmostEqual(scores.ramp(0, 0, 10, 20, invert=True), 20.0)

    def test_missing_inputs_renormalise_not_zero(self):
        """A missing input must dilute coverage, never silently score zero."""
        b = scores.ScoreBuilder({"a": 50.0, "b": 50.0}, ["a"])
        b.add("a", 50.0, "full marks")
        b.add("b", None, "missing")
        out = b.finalise()
        self.assertEqual(out["score"], 100.0, "score must renormalise over available components")
        self.assertEqual(out["coverage_pct"], 50.0)

    def test_collinearity_cap_applies(self):
        b = scores.ScoreBuilder({"x": 20.0, "y": 20.0}, [])
        b.add("x", 20.0, "")
        b.add("y", 20.0, "")
        out = b.finalise({"g": (["x", "y"], 20.0)})
        total = out["components"]["x"]["points"] + out["components"]["y"]["points"]
        self.assertAlmostEqual(total, 20.0, places=1, msg="collinear group must be capped")


class TestStageMachine(unittest.TestCase):
    def test_hysteresis_damps_single_day_spikes(self):
        raw = [0, 0, 2, 0, 0, 0, 0, 0]
        fs = [10] * 8
        fr = [10] * 8
        out = stages.apply_hysteresis(raw, fs, fr)
        self.assertNotIn(2, out, "a one-day spike must not create a stage change")

    def test_hysteresis_accepts_persistent_change(self):
        raw = [0, 0, 2, 2, 2, 2]
        out = stages.apply_hysteresis(raw, [60] * 6, [10] * 6)
        self.assertEqual(out[-1], 2, "a persistent signal must be accepted")

    def test_exit_requires_buffer(self):
        """Grazing back under the threshold must not immediately downgrade."""
        raw = [2, 2, 2, 1, 1, 1]
        fs = [60, 60, 60, 54, 54, 54]        # 54 is under 55 but inside the 6pt buffer
        out = stages.apply_hysteresis(raw, fs, [10] * 6)
        self.assertEqual(out[-1], 2, "exit inside the buffer must be rejected")

        fs2 = [60, 60, 60, 40, 40, 40]       # clearly below 55 - 6
        out2 = stages.apply_hysteresis(raw, fs2, [10] * 6)
        self.assertEqual(out2[-1], 1, "a clear break must be accepted")

    def test_stage4_impossible_without_policy_fact(self):
        f = {"walcl_chg_60d_pct": 25.0}          # balance sheet exploding
        gate = stages.stage4_gate({"stage4_facts": []}, f)
        self.assertFalse(gate.passed, "Stage 4 must never fire on data alone")

    def test_stage4_impossible_without_expansion(self):
        facts = [{"title": "QE announced", "event_type": "qe_announced"}]
        gate = stages.stage4_gate({"stage4_facts": facts}, {"walcl_chg_60d_pct": -3.0})
        self.assertFalse(gate.passed, "Stage 4 must require corroborating expansion")

    def test_stage4_fires_with_both(self):
        facts = [{"title": "QE announced", "event_type": "qe_announced"}]
        gate = stages.stage4_gate({"stage4_facts": facts}, {"walcl_chg_60d_pct": 5.0})
        self.assertTrue(gate.passed)

    def test_stage_stability_flags_twitchy_classifier(self):
        twitchy = [0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
        self.assertFalse(stages.stage_stability(twitchy)["trustworthy"])
        calm = [2] * 60
        self.assertTrue(stages.stage_stability(calm)["trustworthy"])
        self.assertEqual(stages.stage_stability(calm)["days_in_stage"], 60)


class TestPolicyLedger(unittest.TestCase):
    def test_inference_never_scores(self):
        ev = [{"date": "2026-08-01", "institution": "Fed", "event_type": "qe_announced",
               "title": "speculation", "fact_or_inference": "inference"}]
        out = policy.evaluate(date(2026, 8, 15), ev)
        self.assertEqual(out["repression_score"], 0.0)
        self.assertEqual(out["stage4_facts"], [])

    def test_future_events_invisible(self):
        ev = [{"date": "2027-01-01", "institution": "Fed", "event_type": "qe_announced",
               "title": "future", "fact_or_inference": "fact"}]
        out = policy.evaluate(date(2026, 8, 15), ev)
        self.assertEqual(out["stage4_facts"], [], "ledger must not see the future")

    def test_events_decay(self):
        ev = [{"date": "2026-08-01", "institution": "Fed", "event_type": "buyback_expanded",
               "title": "buyback", "fact_or_inference": "fact"}]
        near = policy.evaluate(date(2026, 8, 5), ev)["fiscal_intervention_score"]
        far = policy.evaluate(date(2026, 12, 1), ev)["fiscal_intervention_score"]
        self.assertGreater(near, far, "policy events must decay with age")

    def test_stage4_fact_window_expires(self):
        ev = [{"date": "2026-01-01", "institution": "Fed", "event_type": "qe_announced",
               "title": "old QE", "fact_or_inference": "fact"}]
        inside = policy.evaluate(date(2026, 2, 1), ev)["stage4_facts"]
        outside = policy.evaluate(date(2026, 8, 1), ev)["stage4_facts"]
        self.assertTrue(inside)
        self.assertFalse(outside, "a stale announcement must not pin Stage 4")


class TestBreakers(unittest.TestCase):
    def test_active_breaker_carries_real_penalty(self):
        f = {"DGS30_chg_60d": 0.30, "DFII10_chg_60d": 0.25, "cpi_yoy_trend_3m": 0.0,
             "dxy_chg_60d": 0.0, "GOLD_ret_60d": 1.0, "BTC_ret_60d": 1.0}
        sc = {"driver_composite": 50.0, "confirmation_composite": 50.0}
        out = breakers.evaluate(f, sc)
        self.assertGreaterEqual(out["active_count"], 1)
        self.assertGreater(out["thesis_penalty"], 5.0,
                           "a tripped breaker must not be cosmetic")

    def test_no_breakers_when_quiet(self):
        f = {"DGS30_chg_60d": 0.0, "DFII10_chg_60d": 0.0, "cpi_yoy_trend_3m": 0.0,
             "dxy_chg_60d": 0.0, "GOLD_ret_60d": 1.0, "BTC_ret_60d": 1.0}
        sc = {"driver_composite": 50.0, "confirmation_composite": 50.0}
        self.assertEqual(breakers.evaluate(f, sc)["active_count"], 0)


class TestSignals(unittest.TestCase):
    def test_signals_are_labels_not_orders(self):
        sc = {"debasement": {"score": 80}, "btc_liquidity": {"score": 80},
              "fiscal_stress": {"score": 50}}
        brk = {"thesis_penalty": 0.0}
        out = signals.compute(3, sc, {}, brk)
        for v in out.values():
            self.assertIn(v["signal"], config.SIGNAL_LEVELS)
            self.assertNotIn("buy", str(v).lower())
            self.assertNotIn("sell", str(v).lower())

    def test_breaker_penalty_downgrades(self):
        sc = {"debasement": {"score": 80}, "btc_liquidity": {"score": 80},
              "fiscal_stress": {"score": 50}}
        clean = signals.compute(3, sc, {}, {"thesis_penalty": 0.0})
        hit = signals.compute(3, sc, {}, {"thesis_penalty": 30.0})
        self.assertLess(hit["btc"]["index"], clean["btc"]["index"],
                        "an active breaker must be able to downgrade a signal")


class TestDeterminism(unittest.TestCase):
    def test_same_inputs_same_output(self):
        p = mk_panel(
            DGS30=daily("2026-01-01", [5.0 + i * 0.01 for i in range(120)]),
            DFII10=daily("2026-01-01", [2.0 - i * 0.004 for i in range(120)]),
        )
        when = date(2026, 4, 1)
        a = features.compute_features(p, when)
        b = features.compute_features(p, when)
        self.assertEqual(a["DGS30_level"], b["DGS30_level"])
        self.assertEqual(a["decomp_driver"], b["decomp_driver"])
        pol = {"fiscal_intervention_score": 0.0, "repression_score": 0.0}
        self.assertEqual(scores.compute_all(a, pol)["composite"],
                         scores.compute_all(b, pol)["composite"])


class TestNetLiquidity(unittest.TestCase):
    def test_unit_alignment(self):
        """WALCL/WTREGEN are $mn, RRPONTSYD is $bn. Getting this wrong is a 1000x error."""
        raw = {
            "WALCL":     {"observations": [("2026-08-19", 6_745_699.0)], "freq": "w",
                          "release_lag_days": 2, "label": "", "source": "", "source_url": ""},
            "WTREGEN":   {"observations": [("2026-08-19", 953_612.0)], "freq": "w",
                          "release_lag_days": 2, "label": "", "source": "", "source_url": ""},
            "RRPONTSYD": {"observations": [("2026-08-19", 0.225)], "freq": "d",
                          "release_lag_days": 1, "label": "", "source": "", "source_url": ""},
            "DGS30":     {"observations": [("2026-08-19", 5.19)], "freq": "d",
                          "release_lag_days": 1, "label": "", "source": "", "source_url": ""},
        }
        p = Panel(raw)
        f = features.compute_features(p, date(2026, 8, 25))
        nl = f["net_liquidity_musd"]
        self.assertIsNotNone(nl)
        self.assertAlmostEqual(nl / 1e6, 5.79, places=1,
                               msg="net liquidity should land near $5.8T, not $5.8bn or $5,800T")


class TestScenarios(unittest.TestCase):
    def test_weights_sum_to_100(self):
        f = {"cpi_yoy": 3.0, "cpi_yoy_trend_3m": 0.1, "DFII10_level": 1.5,
             "DFII10_chg_60d": -0.2, "DGS30_chg_60d": 0.1, "DGS30_level": 5.0,
             "THREEFYTP10_level": 0.8, "THREEFYTP10_chg_60d": 0.1,
             "dxy_chg_60d": -2.0, "GOLD_ret_60d": 5.0,
             "BAMLH0A0HYM2_level": 3.0, "BAMLH0A0HYM2_chg_60d": 0.0,
             "walcl_chg_60d_pct": 0.5}
        sc = {"fiscal_stress": {"score": 50}, "financial_repression": {"score": 50}}
        out = scenarios.compute(f, sc, {"fiscal_intervention_score": 0.0,
                                        "repression_score": 0.0, "stage4_facts": []})
        self.assertAlmostEqual(sum(out["weights"].values()), 100.0, places=0)
        self.assertEqual(len(out["ranked"]), 5)

    def test_missing_data_is_neutral_not_zero(self):
        """Absent inputs must read as 0.5, never silently as 0."""
        self.assertEqual(scenarios._sat(None, 0, 1), 0.5)

    def test_qe_scenario_needs_a_policy_fact(self):
        f = {"walcl_chg_60d_pct": 20.0, "DFII10_chg_60d": -0.5}
        sc = {"fiscal_stress": {"score": 50}, "financial_repression": {"score": 90}}
        no_fact = scenarios.compute(f, sc, {"fiscal_intervention_score": 0.0,
                                            "repression_score": 0.0, "stage4_facts": []})
        with_fact = scenarios.compute(f, sc, {"fiscal_intervention_score": 0.0,
                                              "repression_score": 0.0,
                                              "stage4_facts": [{"title": "QE"}]})
        self.assertGreater(with_fact["weights"]["qe_ycc"], no_fact["weights"]["qe_ycc"])


class TestPostureAndTrajectory(unittest.TestCase):
    def test_posture_covers_every_signal_level(self):
        for i in range(len(config.SIGNAL_LEVELS)):
            self.assertIn("action_cn", signals.posture(i))

    def test_posture_direction_matches_level(self):
        self.assertEqual(signals.posture(5)["arrow"], "↑↑")
        self.assertEqual(signals.posture(3)["arrow"], "→")
        self.assertEqual(signals.posture(0)["arrow"], "↓↓")

    def _hist(self, levels):
        return [{"btc": {"signal": lv, "index": config.SIGNAL_LEVELS.index(lv)}}
                for lv in levels]

    def test_trajectory_reports_upgrade(self):
        h = self._hist(["Caution"] * 25 + ["Bullish"] * 1)
        t = signals.trajectory(h, "btc")
        self.assertEqual(t["now"], "Bullish")
        self.assertGreater(t["d20"]["delta"], 0)
        self.assertIn("增强", t["summary_cn"])

    def test_trajectory_never_says_flat_while_5d_moved(self):
        """A flat 20d with a live 5d move must not print 无变化."""
        h = self._hist(["Neutral"] * 20 + ["Caution"] * 3 + ["Neutral"] * 3)
        t = signals.trajectory(h, "btc")
        self.assertEqual(t["d20"]["delta"], 0)
        self.assertNotEqual(t["d5"]["delta"], 0)
        self.assertNotIn("无变化", t["summary_cn"])


class TestSignalHysteresis(unittest.TestCase):
    """Regression: a 0.15-point score wobble must not rewrite the recommendation."""

    def _gold_idx(self, deb_scores):
        out = []
        for deb in deb_scores:
            sc = {"debasement": {"score": deb}, "btc_liquidity": {"score": 50},
                  "fiscal_stress": {"score": 42}}
            out.append(signals.compute(1, sc, {}, {"thesis_penalty": 0.0})["gold"]["index"])
        return out

    def test_knife_edge_is_real_before_damping(self):
        """Document the raw behaviour the damping exists to fix."""
        raw = self._gold_idx([39.9, 40.04, 39.9, 40.04])
        self.assertNotEqual(len(set(raw)), 1,
                            "raw signal should indeed flip across the 40 threshold")

    def test_hysteresis_removes_the_flicker(self):
        raw = self._gold_idx([39.9, 40.04, 39.9, 40.04, 39.9, 40.04])
        damped = signals.apply_hysteresis(raw)
        self.assertEqual(len(set(damped)), 1,
                         "an oscillating score must not produce an oscillating signal")

    def test_sustained_move_still_gets_through(self):
        raw = self._gold_idx([39.0, 39.0, 39.0, 45.0, 45.0, 45.0, 45.0])
        damped = signals.apply_hysteresis(raw)
        self.assertNotEqual(damped[0], damped[-1],
                            "a sustained change must still be adopted")

    def test_none_entries_hold_previous(self):
        self.assertEqual(signals.apply_hysteresis([3, None, None, 3]),
                         [3, 3, 3, 3])

    def test_damped_never_leads_raw(self):
        """The damped series may lag the raw one, never anticipate it."""
        raw = [3, 3, 5, 5, 5, 5]
        damped = signals.apply_hysteresis(raw, persistence=3)
        self.assertEqual(damped[:4], [3, 3, 3, 3])
        self.assertEqual(damped[4], 5)


class TestNearestTriggers(unittest.TestCase):
    def test_no_bogus_gap_on_compound_gate(self):
        """CPI already past 2.5 must not report 还差 X% just because the gate failed."""
        cl = {"items": [{"key": "inflation_sticky", "label_cn": "通胀顽固",
                         "passed": False, "actual": 3.54, "threshold": 2.5,
                         "direction": ">=", "unit": "%"}]}
        out = stages.nearest_triggers(cl)
        self.assertTrue(out[0]["primary_met"])
        self.assertIsNone(out[0]["need"])
        self.assertNotIn("还差", out[0]["need_cn"])

    def test_real_gap_is_reported(self):
        cl = {"items": [{"key": "fs", "label_cn": "财政压力分", "passed": False,
                         "actual": 41.6, "threshold": 55.0, "direction": ">=",
                         "unit": "pt"}]}
        out = stages.nearest_triggers(cl)
        self.assertFalse(out[0]["primary_met"])
        self.assertAlmostEqual(out[0]["need"], 13.4, places=1)

    def test_passed_gates_excluded(self):
        cl = {"items": [{"key": "x", "label_cn": "ok", "passed": True, "actual": 9,
                         "threshold": 1, "direction": ">=", "unit": "pt"}]}
        self.assertEqual(stages.nearest_triggers(cl), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
