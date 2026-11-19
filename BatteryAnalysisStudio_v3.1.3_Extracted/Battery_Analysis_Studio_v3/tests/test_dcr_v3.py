"""Independent DCR fixtures: physical-step identity, expected math and UI state."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QThread

from backend.dcr_analysis import (
    _interp_at, segment_steps, _pulse_step_context, detect_dcr_blocks,
    compute_dcr_table, compute_pulse_result,
)
from ui.dcr_report_page import DCRReportPage
from ui.dcr_review_page import DCRReviewPage
from studio.dcr_panel import DCRPanel


def pulse_fixture():
    rows = []
    for t in (0, 1, 2):
        rows.append(dict(TypeStd="REST", StepNo="1", PhysicalStepID="rest1", StepTime_s=t,
                         Voltage_V=4.0, Current_mA=10., Capacity_mAh=0.))
    for t in (0., .1, 1., 5., 10., 20., 30.):
        rows.append(dict(TypeStd="REST" if t == 0 else "DISCHARGE", StepNo="2",
                         PhysicalStepID="pulse2", StepTime_s=t, Voltage_V=4.05 - .02 * t,
                         Current_mA=0. if t == 0 else -1000., Capacity_mAh=t / 3.6))
    for t in (0., 1., 10., 30.):
        rows.append(dict(TypeStd="REST" if t == 0 else "CHARGE", StepNo="3",
                         PhysicalStepID="return3", StepTime_s=t, Voltage_V=3.4 + .01 * t,
                         Current_mA=0. if t == 0 else 1000., Capacity_mAh=t / 3.6))
    frame = pd.DataFrame(rows)
    frame["Cycle"] = 1
    frame["StepMode"] = "CC"
    frame["Source_File"] = "cell-A"
    frame["MatchStatus"] = "CSV"
    frame["_row"] = np.arange(len(frame))
    return frame


class DCRCalculationTests(unittest.TestCase):
    def test_no_extrapolation_and_gap(self):
        self.assertTrue(np.isnan(_interp_at(0., np.array([.1, 1., 2.]), np.array([4., 3.9, 3.8]))))
        self.assertTrue(np.isnan(_interp_at(3., np.array([.1, 1., 2.]), np.array([4., 3.9, 3.8]))))
        self.assertAlmostEqual(_interp_at(1.5, np.array([1., 2.]), np.array([4., 3.])), 3.5)
        t = np.array([0., 1., 2., 20., 21., 22.])
        self.assertTrue(np.isnan(_interp_at(10., t, t)))
        self.assertEqual(_interp_at(20., t, t), 20.)

    def test_v0_rest_current_and_balance_expected(self):
        frame = pulse_fixture()
        detection = detect_dcr_blocks(frame)
        self.assertEqual(len(detection["blocks"]), 1)
        pulse = detection["blocks"][0]["pulse_groups"][0]["pulses"][0]
        result = compute_pulse_result(frame, detection["segments"], pulse, [0., 1., 10., 30., 31.])
        self.assertEqual(result["v0_source"], "pulse_t0")
        self.assertAlmostEqual(result["v0"], 4.05)
        self.assertAlmostEqual(result["rest_current_mA"], 10.)
        self.assertAlmostEqual(result["r_by_t"][1.], .02 * 1_000_000. / 1010., places=8)
        self.assertAlmostEqual(result["r_by_t"][10.], .2 * 1_000_000. / 1010., places=8)
        self.assertTrue(np.isnan(result["r_by_t"][0.]))
        self.assertTrue(np.isnan(result["r_by_t"][31.]))
        self.assertAlmostEqual(pulse["pulse_capacity_mAh"], 30 / 3.6)
        self.assertTrue(pulse["return_capacity_balanced"])
        table = compute_dcr_table(frame, detection, [1., 10.])["table"]
        self.assertEqual(table[0]["PhysicalStepID"], "pulse2")
        self.assertAlmostEqual(table[0]["Delta_I_1s_mA"], -1010.)
        self.assertEqual(table[0]["Status_1s"], "측정점")

    def test_physical_id_prevents_same_number_bleed(self):
        frame = pd.DataFrame({"Cycle": [1] * 4, "StepNo": ["2"] * 4,
                              "PhysicalStepID": ["a", "a", "b", "b"],
                              "StepTime_s": [0., 1., 2., 3.], "TypeStd": ["DISCHARGE"] * 4,
                              "Current_mA": [-100.] * 4, "Capacity_mAh": [0., 1., 0., 2.],
                              "Voltage_V": [4., 3.9, 3.8, 3.7], "Source_File": ["a"] * 4,
                              "_row": [0, 1, 2, 3]})
        segments = segment_steps(frame)
        self.assertEqual(len(segments), 2)
        context, previous = _pulse_step_context(frame, segments[1])
        self.assertEqual(context["PhysicalStepID"].unique().tolist(), ["b"])
        self.assertEqual(context["_row"].tolist(), [2, 3])

    def test_return_imbalance_is_not_deleted_and_cancel(self):
        frame = pulse_fixture()
        frame.loc[frame.PhysicalStepID.eq("return3"), "Capacity_mAh"] *= .2
        detection = detect_dcr_blocks(frame)
        pulse = detection["blocks"][0]["pulse_groups"][0]["pulses"][0]
        self.assertFalse(pulse["return_capacity_balanced"])
        self.assertEqual(len(compute_dcr_table(frame, detection, [1.])["table"]), 1)
        with self.assertRaises(InterruptedError):
            detect_dcr_blocks(frame, cancel=lambda: True)

    def test_cp_uses_current_at_requested_time(self):
        frame = pulse_fixture()
        pulse_mask = frame.PhysicalStepID.eq("pulse2") & frame.StepTime_s.gt(0)
        frame.loc[pulse_mask, "StepMode"] = "CP"
        frame.loc[pulse_mask, "Current_mA"] = -1000. + 10. * frame.loc[pulse_mask, "StepTime_s"]
        detection = detect_dcr_blocks(frame)
        pulse = detection["blocks"][0]["pulse_groups"][0]["pulses"][0]
        result = compute_pulse_result(frame, detection["segments"], pulse, [1., 10.])
        self.assertEqual(result["control_mode"], "CP/variable")
        self.assertAlmostEqual(result["delta_current_by_t"][1.], -1000.)
        self.assertAlmostEqual(result["delta_current_by_t"][10.], -910.)
        self.assertAlmostEqual(result["r_by_t"][10.], .2 * 1_000_000. / 910.)


class DCRViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_soc_axis_does_not_mix_voltage(self):
        page = DCRReportPage(integrated=True)
        rows = [dict(Source_File="A", Direction="discharge", Block=1, SOC_pct=50.,
                     Reference_Voltage_V=3.7, Current_mA=-1000., Pulse_Index=1, R_1s_mOhm=20.),
                dict(Source_File="B", Direction="discharge", Block=1, SOC_pct=None,
                     Reference_Voltage_V=3.6, Current_mA=-1000., Pulse_Index=1, R_1s_mOhm=30.)]
        page.set_results(rows, [1.])
        self.assertIsNone(page._x_value(rows[1]))
        self.assertEqual(len(page.plot.listDataItems()), 1)
        page.xModeCombo.setCurrentIndex(page.xModeCombo.findData("voltage"))
        self.assertEqual(len(page.plot.listDataItems()), 2)
        self.assertEqual(page._x_value(rows[1]), 3.6)
        headers, matrix = page.summary_export_rows(1.)
        self.assertEqual(headers[0], "파일/채널")
        self.assertEqual(len(matrix), 2)
        self.assertIn("A", matrix[0][0])
        self.assertIn("B", matrix[1][0])
        page.close()

    def test_review_edits_stable_and_invalid_time(self):
        frame = pulse_fixture()
        detection = detect_dcr_blocks(frame)
        page = DCRReviewPage()
        page.set_detection(detection)
        page.table.item(0, 3).setText("50")
        edits = page.edits()
        page.set_detection(detection)
        page.apply_edits(edits)
        self.assertEqual(page.table.item(0, 3).text(), "50")
        page.timePointsInp.setText("0, -1")
        with self.assertRaises(ValueError):
            page._parse_time_points()
        page.close()

    def test_panel_state_is_json_safe_and_undo(self):
        import json
        panel = DCRPanel()
        panel.active_id = "channel-a"
        panel.detection = detect_dcr_blocks(pulse_fixture())
        panel.review.set_detection(panel.detection)
        panel._edit_snapshot = panel.review.edits()
        panel.review.table.item(0, 3).setText("60")
        self.assertTrue(panel._history["channel-a"])
        state = panel.state()
        json.dumps(state, allow_nan=False)
        self.assertEqual(list(state["channels"]["channel-a"]["edits"].values())[0]["soc"], "60")
        panel.undo_edit()
        self.assertEqual(panel.review.table.item(0, 3).text(), "")
        panel.shutdown()
        panel.close()

    def test_shared_channel_async_analysis_and_restore(self):
        import time
        channel = SimpleNamespace(id="source-hash::channel", name="Cell A", frame=pulse_fixture())
        panel = DCRPanel()
        panel.set_channels([channel])
        panel.set_active_channel(channel.id)
        deadline = time.monotonic() + 10
        while panel._busy and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(.02)
        self.assertFalse(panel._busy)
        self.assertEqual(panel._last_completion_thread, self.application.thread())
        self.assertIsNotNone(panel.detection, panel.message.text())
        self.assertEqual(len(panel.review.pulse_rows), 1)
        self.assertAlmostEqual(panel.review.pulse_rows[0]["v0"], 4.05)
        panel.review.table.item(0, 3).setText("50")
        panel.review._emit_start()
        while panel._busy and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(.02)
        self.assertTrue(panel.result_valid, panel.message.text())
        self.assertEqual(panel._last_completion_thread, self.application.thread())
        self.assertEqual(panel.result_rows[0]["SOC_pct"], 50.)
        self.assertEqual(panel.result_rows[0]["Source_ID"], channel.id)
        state = panel.state()
        panel.shutdown()
        panel.close()
        restored = DCRPanel()
        restored.set_channels([channel])
        restored.restore_state(state)
        deadline = time.monotonic() + 10
        while restored._busy and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(.02)
        self.assertEqual(restored.review.table.item(0, 3).text(), "50")
        self.assertEqual(restored._last_completion_thread, self.application.thread())
        restored.shutdown()
        restored.close()

    def test_cancel_discards_late_worker_result(self):
        import time
        panel = DCRPanel()
        channel = SimpleNamespace(id="cancel-channel", name="Cell A", frame=pulse_fixture())
        panel.set_channels([channel])
        panel.set_active_channel(channel.id)
        panel.cancel()
        self.assertTrue(panel.shutdown())
        for _ in range(5):
            self.application.processEvents()
            time.sleep(.01)
        self.assertIsNone(panel.detection)
        self.assertFalse(panel._busy)
        panel.close()

    def test_worker_callback_executes_in_gui_thread(self):
        import time
        panel = DCRPanel()
        worker_threads = []
        callback_threads = []

        def work(cancel):
            worker_threads.append(QThread.currentThread())
            return "done"

        def complete(result):
            self.assertEqual(result, "done")
            callback_threads.append(QThread.currentThread())

        panel._run("Thread affinity test", work, complete)
        deadline = time.monotonic() + 10
        while panel._busy and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(.01)
        self.assertEqual(callback_threads, [self.application.thread()])
        self.assertNotEqual(worker_threads[0], self.application.thread())
        self.assertEqual(panel._last_completion_thread, self.application.thread())
        panel.shutdown()
        panel.close()


if __name__ == "__main__":
    unittest.main()
