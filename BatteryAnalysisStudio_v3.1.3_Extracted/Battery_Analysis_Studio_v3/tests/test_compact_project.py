"""Verify shared curves and source-independent project reopen."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from studio.analysis import raw_curves, derivative_curves
from studio.compact_data import DCR_TIMES, _dcr_snapshot, compact_curves, prepare_channel
from studio.models import ChannelData
from studio.project import load_project, project_channels, save_project
from backend.dcr_analysis import _stored_pulse_result
from tests.test_dcr_v3 import pulse_fixture


def measurements():
    time = np.arange(12_001, dtype=float)
    return pd.DataFrame(dict(Cycle=1, _row=np.arange(len(time)), TypeStd="CHARGE",
                             MatchStatus="CSV", StepNo=1, PhysicalStepID="charge",
                             Voltage_V=3 + time / 10_000, Capacity_mAh=time / 12,
                             StepTime_s=time, Current_mA=300.0))


class CompactProjectTests(unittest.TestCase):
    def test_shared_curves_open_without_source(self):
        frame = measurements()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.csv"
            source.write_text("original")
            raw = ChannelData("id", "charge", str(source), frame, metadata={"sha256": "sample"})
            channel = prepare_channel(raw, root / "cache")
            self.assertLess(channel.row_count, len(frame) / 4)
            self.assertEqual(len(channel.frame.tables(False)["representative"]), 1)
            self.assertEqual(len(raw_curves(channel, [1])), 1)
            curve = derivative_curves(channel, [1], direction="charge", n_points=0)[0]
            self.assertFalse(curve["error"])
            project = root / "run.basproj"
            save_project(str(project), [channel], {})
            source.unlink()
            restored = project_channels(load_project(str(project)))[0]
            self.assertEqual(restored.row_count, channel.row_count)
            self.assertEqual(len(restored.frame.tables(False)["representative"]), 1)
            self.assertEqual(restored.frame.dcr(False)["blocks"], [])

    def test_flat_cv_retains_duration_and_current_change(self):
        frame = measurements().iloc[:1201].copy()
        frame["Voltage_V"] = 4.2
        frame["Current_mA"] = 300 * np.exp(-frame["StepTime_s"] / 200)
        compact = compact_curves(frame)
        self.assertLess(len(compact), len(frame) / 4)
        for name in ("Voltage_V", "Current_mA", "StepTime_s", "Capacity_mAh"):
            np.testing.assert_allclose(compact[name].iloc[[0, -1]], frame[name].iloc[[0, -1]])
        self.assertTrue(np.all(np.diff(compact["StepTime_s"]) > 0))

    def test_original_pulse_is_sampled_at_import(self):
        frame = pulse_fixture()
        frame = frame[~((frame["StepNo"] == "2") & (frame["StepTime_s"] == .1))]
        channel = ChannelData("a", "pulse", "source", frame)
        detection = _dcr_snapshot(channel, False, None)
        pulse = detection["blocks"][0]["pulse_groups"][0]["pulses"][0]
        stored = _stored_pulse_result(pulse["fixed_dcr"], DCR_TIMES)
        self.assertAlmostEqual(stored["r_by_t"][1.0], .02 * 1_000_000 / 1010)
        self.assertTrue(np.isnan(stored["r_by_t"][0.1]))
        self.assertTrue(np.isnan(stored["r_by_t"][0.5]))
        self.assertTrue(np.isnan(stored["r_by_t"][60.0]))

    def test_saved_dcr_marks_missing_points(self):
        stored = dict(r_by_t={"0.1": None, "1.0": 10}, v_by_t={"1.0": 3.9},
                      applied_current_by_t={"1.0": -100}, delta_current_by_t={"1.0": -100},
                      time_status={"0.1": "측정점 부족", "1.0": "측정점"})
        result = _stored_pulse_result(stored, DCR_TIMES)
        self.assertTrue(np.isnan(result["r_by_t"][0.1]))
        self.assertEqual(result["r_by_t"][1], 10)
        self.assertEqual(result["time_status"][0.1], "측정점 부족")
