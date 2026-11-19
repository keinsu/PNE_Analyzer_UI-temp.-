"""Regression tests against preserved Qt math plus integration boundaries."""
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

from backend.cycle_metrics import compute_cycle_summary
from backend.dqdv import compute_dqdv
from studio.analysis import analysis_frame, cycle_summary, raw_curves, derivative_curves, track_peaks, merge_same_cell


def sample_channel():
    parts, row = [], 0
    for cycle, capacity in ((1, 1000.), (2, 900.)):
        for mode, start, end, current in (("CHARGE", 3., 4.2, 1000.), ("DISCHARGE", 4.2, 3., -1000.)):
            n = 101
            cap = np.linspace(0, capacity, n)
            parts.append(pd.DataFrame(dict(Cycle=cycle, OriginalCycle=cycle,
                         PhysicalStepID=f"{cycle}-{mode}", StepNo="1" if mode == "CHARGE" else "2",
                         StepMode="CC", TypeStd=mode, StepTime_s=np.linspace(0, 3600, n),
                         TotalTime_s=np.linspace(row * 36, (row + n - 1) * 36, n),
                         Voltage_V=np.linspace(start, end, n), Current_mA=current, Capacity_mAh=cap,
                         Energy_mWh=cap * 3.6, Temp_C=25., _row=np.arange(row, row + n),
                         Source_File="sample", MatchStatus="CSV")))
            row += n
    return SimpleNamespace(id="a", name="sample", source_path="sample.csv", frame=pd.concat(parts, ignore_index=True),
                           steps=pd.DataFrame(), metadata={}, warnings=[])


class AnalysisTest(unittest.TestCase):
    def test_legacy_cycle_and_derivative_equivalence(self):
        channel = sample_channel()
        before = channel.frame.copy(deep=True)
        expected = compute_cycle_summary(channel.frame)
        actual = cycle_summary(channel)
        for field in ("Charge_Capacity_mAh", "Discharge_Capacity_mAh", "Coulombic_Efficiency_pct", "Energy_Efficiency_pct", "CC_Time_s", "CV_Capacity_mAh", "Discharge_Capacity_Retention_pct"):
            np.testing.assert_allclose(actual[field], expected[field], equal_nan=True, rtol=1e-12, atol=1e-12)
        expected_dq = compute_dqdv(channel.frame, 1, "discharge")
        actual_dq = derivative_curves(channel, [1], "discharge")[0]
        self.assertEqual(actual_dq["error"], "")
        np.testing.assert_allclose(actual_dq["y"], expected_dq["dqdv"], rtol=1e-12, atol=1e-12)
        np.testing.assert_allclose(actual_dq["dvdq_y"], expected_dq["dvdq"], rtol=1e-12, atol=1e-12)
        pd.testing.assert_frame_equal(channel.frame, before)

    def test_baseline_never_silently_replaced(self):
        channel = sample_channel()
        result = cycle_summary(channel, baseline_cycle=2)
        self.assertEqual(float(result.loc[result.Cycle == 2, "Discharge_Capacity_Retention_pct"].iloc[0]), 100)
        self.assertAlmostEqual(float(result.loc[result.Cycle == 1, "Discharge_Energy_Retention_pct"].iloc[0]), 1000/900*100)
        with self.assertRaises(ValueError):
            cycle_summary(channel, baseline_cycle=17)
        channel.frame.loc[(channel.frame.Cycle == 2) & (channel.frame.TypeStd == "DISCHARGE"), "Capacity_mAh"] = 0
        with self.assertRaises(ValueError):
            cycle_summary(channel, baseline_cycle=2)

    def test_review_and_initial_rows_not_implicitly_used(self):
        channel = sample_channel()
        channel.frame.loc[0, "MatchStatus"] = "INITIAL"
        channel.frame.loc[1, "MatchStatus"] = "REVIEW"
        self.assertEqual(len(analysis_frame(channel)), len(channel.frame)-2)
        self.assertEqual(len(analysis_frame(channel, True)), len(channel.frame)-1)

    def test_step_selection_does_not_mix_physical_steps(self):
        channel = sample_channel()
        second = channel.frame.loc[(channel.frame.Cycle == 1) & (channel.frame.TypeStd == "DISCHARGE")].iloc[:20].copy()
        second["_row"] += 500
        second["PhysicalStepID"], second["StepNo"] = "pulse", "9"
        second["Voltage_V"] = np.linspace(4.3, 4.1, 20)
        second["Capacity_mAh"] = np.linspace(0, 10, 20)
        channel.frame = pd.concat([channel.frame, second], ignore_index=True)
        auto = derivative_curves(channel, [1], "discharge")[0]
        self.assertIn("1-DISCHARGE", auto["step"])
        self.assertEqual(len(auto["raw_indices"]), 101)
        explicit = derivative_curves(channel, [1], "discharge", step="9")[0]
        self.assertEqual(explicit["error"], "")
        self.assertEqual(len(explicit["raw_indices"]), 20)
        self.assertIn("pulse", explicit["step"])
        representative = cycle_summary(channel)
        self.assertEqual(representative.loc[0, "Discharge_Step_Count"], 2)
        self.assertEqual(representative.loc[0, "Discharge_Capacity_mAh"], 1000.)
        self.assertEqual(cycle_summary(channel, aggregation="step_sum").loc[0, "Discharge_Capacity_mAh"], 1010.)

    def test_raw_preserves_row_gaps_and_independent_steps(self):
        channel = sample_channel()
        channel.frame.loc[150, "MatchStatus"] = "REVIEW"
        curves = raw_curves(channel, [1], direction="discharge")
        self.assertEqual(len(curves), 2)
        self.assertTrue(all(150 not in curve["raw_indices"] for curve in curves))
        self.assertTrue(all(curve["point_kind"] == "measured" for curve in curves))

    def test_invalid_settings_and_absent_cycle(self):
        channel = sample_channel()
        for settings in ({"n_points": 49}, {"smooth_window": 20}, {"smooth_window": 501},
                         {"smooth_polyorder": 7}, {"peak_prominence_pct": 0}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                derivative_curves(channel, [1], **settings)
        with self.assertRaises(ValueError):
            raw_curves(channel, [55])
        with self.assertRaises(ValueError):
            derivative_curves(channel, [55])

    def test_missing_direction_is_explicit_and_keeps_success(self):
        channel = sample_channel()
        channel.frame = channel.frame[channel.frame.TypeStd == "DISCHARGE"]
        curves = derivative_curves(channel, [1])
        self.assertTrue(curves[0]["error"])
        self.assertEqual(curves[1]["error"], "")

    def test_peak_matching_is_one_to_one_and_records_missing(self):
        curves = [dict(source_id="a", direction="charge", cycle=1, peaks=[dict(voltage=3.4, value=1.), dict(voltage=3.8, value=2.)]),
                  dict(source_id="a", direction="charge", cycle=2, peaks=[dict(voltage=3.81, value=1.9)])]
        result = track_peaks(curves)
        missing = result[(result.Cycle == 2) & (result.State == "missing")]
        self.assertEqual(missing.Peak_ID.tolist(), [1])
        matched = result[(result.Cycle == 2) & (result.State == "matched")]
        self.assertEqual(matched.Peak_ID.tolist(), [2])

    def test_explicit_same_cell_merge_preserves_original_and_rejects_overlap(self):
        first, second = sample_channel(), sample_channel()
        second.id, second.name = "b", "second"
        second.frame["Source_File"] = "second"
        before = first.frame.copy(deep=True)
        with self.assertRaises(ValueError):
            merge_same_cell([first, second], [0, 0])
        joined = merge_same_cell([first, second], [0, 2])
        self.assertEqual(sorted(joined.frame.Cycle.unique()), [1, 2, 3, 4])
        self.assertEqual(sorted(joined.frame.OriginalCycle.unique()), [1, 2])
        self.assertEqual(set(joined.frame.OriginalSource_ID), {"a", "b"})
        self.assertEqual(len(joined.frame._row.unique()), len(joined.frame))
        pd.testing.assert_frame_equal(first.frame, before)
        result = cycle_summary(joined)
        self.assertEqual(len(result), 4)

    def test_merge_retains_unknown_rows_and_offsets_step_metadata(self):
        first, second = sample_channel(), sample_channel()
        second.id, second.name = "b", "second"
        for channel in (first, second):
            channel.frame.loc[0, ["Cycle", "OriginalCycle", "MatchStatus"]] = [-1, -1, "INITIAL"]
            channel.frame.loc[1, ["Cycle", "OriginalCycle", "MatchStatus"]] = [-1, -1, "UNMATCHED"]
            channel.steps = pd.DataFrame([
                dict(Cycle=-1, OriginalCycle=-1, PhysicalStepID="unknown", MatchStatus="INITIAL"),
                dict(Cycle=0, OriginalCycle=0, PhysicalStepID="zero", MatchStatus="MATCHED"),
                dict(Cycle=1, OriginalCycle=1, PhysicalStepID="1-CHARGE", MatchStatus="MATCHED")])
            # Genuine Cycle 0 is valid and must be shifted, unlike sentinel -1.
            channel.frame.loc[2, ["Cycle", "OriginalCycle", "PhysicalStepID"]] = [0, 0, "zero"]
        original_frame = first.frame.copy(deep=True)
        original_steps = second.steps.copy(deep=True)
        merged = merge_same_cell([first, second], [0, 3])
        unknown = merged.frame.MatchStatus.isin(["INITIAL", "UNMATCHED"])
        self.assertEqual(merged.frame.loc[unknown, "Cycle"].tolist(), [-1, -1, -1, -1])
        self.assertEqual(merged.steps.loc[merged.steps.MatchStatus == "INITIAL", "Cycle"].tolist(), [-1, -1])
        self.assertEqual(merged.steps.loc[merged.steps.OriginalSource_ID == "b", "Cycle"].tolist(), [-1, 3, 4])
        self.assertEqual(merged.steps.loc[merged.steps.OriginalSource_ID == "b", "PhysicalStepID"].tolist(),
                         ["b:unknown", "b:zero", "b:1-CHARGE"])
        self.assertEqual(set(merged.frame.OriginalSource_ID), {"a", "b"})
        self.assertEqual(set(merged.frame.OriginalRow), set(first.frame._row))
        pd.testing.assert_frame_equal(first.frame, original_frame)
        pd.testing.assert_frame_equal(second.steps, original_steps)


if __name__ == "__main__":
    unittest.main()
