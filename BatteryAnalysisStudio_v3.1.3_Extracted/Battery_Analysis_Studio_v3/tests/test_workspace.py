"""Offscreen user-flow tests of the integrated workspace, with byte-level ZIPs."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QThread, QPointF
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from studio.app import StudioWindow
from studio.ingestion import load_inputs
from studio.analysis import cycle_summary, prepare_cycle_tables, raw_curves, derivative_curves
from studio.project import save_project, load_project
from studio.widgets import CURVE_WIDTH, DataTable, CurvePlot, PALETTE


def workspace_zip(directory):
    """Independent instrument-layout fixture, two cycles and real-sized curves."""
    path = Path(directory) / "워크스페이스 시험.zip"
    records, summaries = [], []
    for cycle in (1, 2):
        for discharge in (False, True):
            current, step = (-1000., 3) if discharge else (1000., 1)
            voltage = np.linspace(4200., 3000., 81) if discharge else np.linspace(3000., 4200., 81)
            capacity = np.linspace(0., 22.222222 * (1 if cycle == 1 else .9), 81)
            for t in range(81):
                record = bytearray(176)
                values = {0: len(records)+1, 4: t, 16: voltage[t], 20: current, 24: capacity[t],
                          32: 0. if discharge else capacity[t], 36: capacity[t] if discharge else 0.,
                          40: capacity[t]*3.6, 64: 25.+cycle, 68: 12., 72: voltage[t]*current/1000}
                for offset, value in values.items():
                    struct.pack_into("<f", record, offset, value)
                records.append(record)
            summaries.append(f"{step},{'방전' if discharge else '충전'},0,{cycle},0,80,{voltage[-1]/1000},{current/1000},{capacity[-1]/1000},{capacity[-1]*3.6/1000}")
    csv = "시험 메타데이터\nStepNo,StepType,StateCode,CurCycle,TotalCycle,StepTime,Voltage,Current,Capacity,WattHour\n" + "\n".join(summaries)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("시험/M02Ch001/data.cyc", bytes(72)+b"".join(records))
        archive.writestr("시험/M02Ch001/data_StepEnd.csv", csv.encode("cp949"))
    return path


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
        cls.app.setFont(QFont("Malgun Gothic", 10))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = workspace_zip(self.temp.name)
        self.window = StudioWindow()
        self.window.show()
        self.app.processEvents()

    def pump_until(self, condition, timeout=10.):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                self.app.processEvents()
                return
            time.sleep(.005)
        self.fail("작업 완료 시간 초과: " + self.window.notice.text())

    def idle(self):
        self.pump_until(lambda: not self.window._jobs and not self.window._threads)

    def tearDown(self):
        self.window._selection_timer.stop()
        self.window.cancel_jobs()
        self.idle()
        self.window.dcr.cancel()
        deadline = time.monotonic()+5
        while getattr(self.window.dcr, "_jobs", None) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def load_direct(self):
        self.window.channels = load_inputs([str(self.path)])
        self.window._populate_tree()
        self.window._selection_timer.stop()
        return self.window.channels[0]

    def test_import_zip_summary_raw_derivatives_without_reopening(self):
        self.window.import_paths([str(self.path)])
        self.idle()
        self.assertEqual(len(self.window.channels), 1)
        channel = self.window.channels[0]
        self.assertTrue(channel.metadata['disk_backed'])
        self.assertIn(channel.id, self.window._summaries)
        self.assertEqual(self.window.summary.table.model.rowCount(), 1)
        self.assertEqual(len(self.window.summary.graph.curves), 1)
        self.assertIn("2", self.window.cards[3].text())
        self.window._restoring = True
        self.window.nav.setCurrentRow(2)
        self.window.raw_cycles.setText("1,2")
        self.window._restoring = False
        self.window.calculate_raw()
        self.idle()
        self.assertEqual(len(self.window.raw.graph.curves), 4)
        self.assertEqual(self.window.raw.graph.list.count(), 2)
        self.assertEqual(self.window.raw.graph.list.item(0).text(), 'Cycle 1')
        self.window.raw.graph.list.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual([item.isVisible() for item in self.window.raw.graph.items], [False, False, True, True])
        self.assertEqual(self.window.raw.table.model.rowCount(), 4)
        self.window._restoring = True
        self.window.nav.setCurrentRow(3)
        self.window.dq_cycles.setText("1,2")
        self.window._restoring = False
        self.window.calculate_derivatives()
        self.idle()
        self.assertEqual(len(self.window.dq_plot.curves), 4)
        self.assertEqual(len(self.window.vq_plot.curves), 4)
        self.assertEqual(len(self.window.dvdq_plot.curves), 4)
        self.assertEqual(self.window.dq_plot.list.count(), 2)
        self.assertEqual(self.window.dq_plot.list.item(0).foreground().color(),
                         self.window.raw.graph.list.item(0).foreground().color())
        self.assertTrue(all(len(c["x"]) == 500 for c in self.window.dq_plot.curves))
        self.assertEqual(len(self.window.channels), 1)

    def test_import_defers_raw_tables_and_dcr_until_opened(self):
        with patch.object(self.window.dcr, "detect", wraps=self.window.dcr.detect) as detect:
            self.window.import_paths([str(self.path)])
            self.idle()
            self.assertEqual(self.window.source_table.model.rowCount(), 0)
            detect.assert_not_called()
            self.window.nav.setCurrentRow(2)
            self.idle()
            self.assertTrue(self.window.raw.table_toggle.isChecked())
            self.assertEqual(self.window.source_table.model.rowCount(), 0)
            self.window.raw.table_toggle.setChecked(False)
            self.window.raw_tables.setCurrentIndex(2)
            self.assertEqual(self.window.source_table.model.rowCount(), 324)
            detect.assert_not_called()
            self.window.nav.setCurrentRow(4)
            self.pump_until(lambda: not self.window.dcr._busy)
            detect.assert_called_once()
            self.assertIsNotNone(self.window.dcr.detection)

    def test_origin_button_exports_checked_cycles_from_one_set(self):
        from studio.widgets import PALETTE

        graph = self.window.raw.graph
        curve = dict(x=[0., 1.], y=[3., 4.], color=PALETTE[0])
        graph.set_curves([dict(curve, source_id="Cell1", channel="1번", cycle=3),
                          dict(curve, source_id="Cell1", channel="1번", cycle=20),
                          dict(curve, source_id="Cell2", channel="2번", cycle=50)],
                         group_cycles=True)
        graph.list.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertTrue(graph.origin_btn.isEnabled())
        with (patch("studio.app.QInputDialog.getItem", return_value=("1. 1번", True)),
              patch("studio.origin_export.paste_origin_to_powerpoint",
                    return_value="시험.pptx · 3번 슬라이드") as writer):
            graph.origin_btn.click()
            self.idle()
        self.assertEqual([c.label for c in writer.call_args.args[0]], ["Cycle 20"])
        graph.set_stale(True)
        self.assertFalse(graph.origin_btn.isEnabled())

    def test_managed_study_is_shown_in_primary_analysis_workspace(self):
        from PySide6.QtWidgets import QMessageBox
        from studio.studies import Study

        study = Study.create(Path(self.temp.name) / "library", "A분석")
        manager = self.window.study_workspace
        manager.open(study)
        self.assertEqual(self.window.study_button.text(), "A분석")
        source = load_inputs([str(self.path)], cache_dir=study.cache_dir, disk_backed=True)[0]
        preview = study.preview_cycles([(source, None, 'Cell-01', 101, None)], lambda: False)
        with patch("studio.study_ui.QMessageBox.exec", return_value=QMessageBox.StandardButton.Yes):
            manager._confirm(study, preview)
        self.idle()
        self.assertEqual(self.window.summary.graph.curves[0]["x"].tolist(), [101, 102])
        self.assertEqual(self.window.selected_channels()[0].name, "Cell-01")
        self.assertTrue(self.window.merge_button.isHidden())
        self.window.baseline.setText("102")
        manager.save()
        manager.open(Study(study.directory))
        self.idle()
        self.assertEqual(self.window.baseline.text(), "102")
        self.assertIn("102", self.window.cards[3].text())

    def test_summary_render_and_metric_persist_after_baseline_change(self):
        channel = self.load_direct()
        self.window.baseline.setText("2")
        self.window._summaries = {channel.id: cycle_summary(channel, baseline_cycle=2)}
        combo = self.window.metric_widgets[1]
        combo.setCurrentIndex(combo.findData("Energy_Retention_pct"))
        self.window.render_summaries()
        self.assertEqual(combo.currentData(), "Energy_Retention_pct")
        table = self.window.life.table.model.frame
        self.assertEqual(table.Source_ID.nunique(), 1)
        self.assertEqual(table.loc[table.Cycle == 2, "Energy_Retention_pct"].iloc[0], 100.)
        self.assertIn("2", self.window.cards[3].text())

    def test_summary_reuses_same_setting_cache(self):
        self.load_direct()
        with patch("studio.app.prepare_cycle_tables", wraps=prepare_cycle_tables) as compute:
            self.window.calculate_summaries()
            self.idle()
            self.assertEqual(compute.call_count, 1)
            self.window.calculate_summaries()
            self.assertEqual(compute.call_count, 1)
            self.window.baseline.setText("1")
            self.window.calculate_summaries()
            self.idle()
            self.assertEqual(compute.call_count, 1)
            self.assertIn("1", self.window.cards[3].text())
            self.window.aggregate.setCurrentIndex(1)
            self.window.calculate_summaries()
            self.assertEqual(compute.call_count, 1)
            self.assertEqual(self.window.summary.table.model.frame.Cycle.tolist(), [2])
            self.window.summary_start.setValue(1)
            self.assertEqual(sorted(self.window.summary.table.model.frame.Cycle.tolist()), [1, 2])

    def test_project_restores_selection_settings_and_graph_view(self):
        channel = self.load_direct()
        self.window._summaries = {channel.id: cycle_summary(channel)}
        self.window.render_summaries()
        self.window.raw_cycles.setText("1,2")
        self.window.dq_points.setValue(700)
        self.window.baseline.setText("2")
        self.window.summary.graph.plot.setRange(xRange=[1.1, 1.8], yRange=[88., 102.], padding=0)
        self.window.summary.graph.list.item(0).setCheckState(Qt.CheckState.Unchecked)
        state = self.window.ui_state()
        project_path = str(Path(self.temp.name)/"작업.basproj")
        save_project(project_path, self.window.channels, state)
        restored = load_project(project_path)
        self.window.raw_cycles.setText("1")
        self.window.dq_points.setValue(500)
        self.window._pending_project = restored
        self.window.import_paths([str(self.path)], replace=True)
        self.idle()
        self.assertEqual(self.window.raw_cycles.text(), "1,2")
        self.assertEqual(self.window.dq_points.value(), 700)
        self.assertEqual(self.window.baseline.text(), "2")
        self.assertEqual([c.id for c in self.window.selected_channels()], [channel.id])
        np.testing.assert_allclose(self.window.summary.graph.plot.viewRange(), state["graphs"]["summary"]["range"], rtol=1e-8, atol=1e-8)
        self.assertEqual(self.window.summary.graph.list.item(0).checkState(), Qt.CheckState.Unchecked)

    def test_previous_worker_cannot_overwrite_new_result(self):
        results, threads = [], []
        gate = threading.Event()
        first_finished = threading.Event()
        def delayed(progress, cancel):
            gate.wait(.3)
            first_finished.set()
            return "old"
        def latest(progress, cancel):
            return "new" if first_finished.is_set() else "overlap"
        def receive(value):
            results.append(value)
            threads.append(QThread.currentThread())
        self.window._start("test", delayed, receive)
        self.window._start("test", latest, receive)
        gate.set()
        self.idle()
        self.assertEqual(results, ["new"])
        self.assertTrue(all(thread is self.app.thread() for thread in threads), "결과 반영은 GUI 스레드에서 실행되어야 합니다.")

    def test_raw_settings_change_during_job_keeps_result_stale(self):
        channel = self.load_direct()
        self.window._restoring = True
        self.window.nav.setCurrentRow(2)
        self.window.raw_cycles.setText("1")
        self.window._restoring = False
        initial = raw_curves(channel, [1])
        self.window.raw.graph.set_curves(initial)
        gate = threading.Event()
        def delayed(*args, **kwargs):
            gate.wait(.5)
            return raw_curves(*args, **kwargs)
        with patch("studio.app.raw_curves", delayed):
            self.window.calculate_raw()
            self.window.raw_y.setCurrentIndex(self.window.raw_y.findData("Current_mA"))
            gate.set()
            self.idle()
        self.assertTrue(self.window.raw.graph._stale)
        self.assertFalse(self.window.raw.graph.csv_btn.isEnabled())

    def test_virtual_table_copy_matches_sorted_selection(self):
        table = DataTable()
        table.set_frame(pd.DataFrame(dict(Cycle=[2, 1], Capacity=[90., 100.])))
        table.view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        table.view.selectAll()
        table.copy_selection()
        self.assertEqual(self.app.clipboard().text(), "1\t100\n2\t90")
        table.deleteLater()

    def test_graph_hover_uses_selected_original_curve_without_mutating_data(self):
        graph = CurvePlot()
        graph.resize(800, 500)
        graph.show()
        original = np.linspace(0, 1, 1001)
        graph.set_curves([dict(x=original.copy(), y=original.copy(), name="first", point_kind="measured"),
                          dict(x=original.copy(), y=original.copy()+1, name="selected", point_kind="measured")])
        self.app.processEvents()
        graph.list.setCurrentRow(1)
        point = graph.plot.getViewBox().mapViewToScene(QPointF(.5, .5))
        graph._hover((point,))
        self.assertIn("selected", graph.readout.text())
        self.assertIn("실측점", graph.readout.text())
        self.assertAlmostEqual(graph.marker.getData()[1][0], 1.5, places=2)
        graph.list.item(0).setCheckState(Qt.CheckState.Unchecked)
        np.testing.assert_array_equal(graph.curves[0]["x"], original)
        graph.close()
        graph.deleteLater()

    def test_graph_uses_high_contrast_palette_and_thicker_lines(self):
        graph = CurvePlot()
        graph.set_curves([dict(x=[0., 1.], y=[0., 1.], name="contrast")])
        pen = graph.items[0].opts["pen"]
        self.assertGreaterEqual(pen.widthF(), 1.7)
        self.assertEqual(pen.color().name().upper(), PALETTE[0])
        graph.deleteLater()


if __name__ == "__main__":
    unittest.main()
