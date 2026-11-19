"""Verify the actual workspace export paths, including frozen-result provenance."""
import csv
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QImage
from PySide6.QtWidgets import QApplication

from studio.app import StudioWindow
from studio.demo import generate_demo
from studio.ingestion import load_inputs
from studio.analysis import cycle_summary, derivative_curves, merge_same_cell
from studio.project import save_project, load_project


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/malgun.ttf')

    def setUp(self):
        self.warning_patch = patch('studio.widgets.QMessageBox.warning', side_effect=lambda *args: (_ for _ in ()).throw(RuntimeError(str(args[2]))))
        self.warning_patch.start()
        self.addCleanup(self.warning_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.channels = load_inputs(generate_demo(self.root))
        self.win = StudioWindow()
        self.win.channels = self.channels
        self.win._populate_tree()
        self.win._selection_timer.stop()
        self.win._summaries = {c.id: cycle_summary(c) for c in self.channels}
        self.win.render_summaries()

    def idle(self):
        end = time.monotonic() + 15
        while time.monotonic() < end:
            self.app.processEvents()
            if not self.win._jobs and not self.win._threads:
                return
            time.sleep(.005)
        self.fail(self.win.notice.text())

    def tearDown(self):
        self.win.cancel_jobs()
        self.idle()
        self.win.dcr.shutdown()
        self.win.close()
        self.app.processEvents()
        self.temp.cleanup()

    def test_excel_contains_sources_settings_life_and_valid_retention(self):
        target = self.root / '검증 결과.xlsx'
        with patch('studio.app.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            self.win.export_excel()
        self.idle()
        self.assertTrue(target.exists(), self.win.notice.text())
        with pd.ExcelFile(target) as book:
            self.assertIn('Sources', book.sheet_names)
            self.assertIn('Settings', book.sheet_names)
        life = pd.read_excel(target, sheet_name='Life_1_1')
        self.assertAlmostEqual(life.loc[life.Cycle == 2, 'Discharge_Capacity_Retention_pct'].iloc[0], 100.)
        self.assertTrue((life.Baseline_Cycle == 2).all())
        self.assertIn('Baseline_Cycle', life)
        self.assertEqual(len(pd.read_excel(target, sheet_name='Sources')), 3)
        self.assertFalse(target.with_suffix('.partial.xlsx').exists())

    def test_curve_csv_retains_calculation_settings_and_only_visible_curves(self):
        curves = derivative_curves(self.channels[0], [1, 2], direction='both')
        self.win.render_derivatives(curves)
        graph = self.win.dq_plot
        graph.list.item(1).setCheckState(Qt.CheckState.Unchecked)
        target = self.root / '미분.csv'
        with patch('studio.widgets.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            graph.export_csv()
        with target.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1000)
        self.assertEqual(json.loads(rows[0]['Settings_JSON'])['n_points'], 500)
        self.assertEqual(rows[0]['PointKind'], 'calculated')
        self.assertTrue(rows[0]['Source_ID'])

    def test_png_export_and_same_cell_project_rebuild(self):
        target = self.root / '그래프.png'
        self.win.show()
        self.app.processEvents()
        with patch('studio.widgets.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            self.win.summary.graph.export_png()
        self.assertFalse(QImage(str(target)).isNull())
        merged = merge_same_cell(self.channels[:2], [0, 5])
        self.win.channels.append(merged)
        project_path = self.root / '연결.basproj'
        save_project(str(project_path), self.win.channels, self.win.ui_state())
        project = load_project(str(project_path))
        self.win.channels = self.channels.copy()
        self.win._restore_merged_channels(project)
        restored = next(c for c in self.win.channels if c.id == merged.id)
        pd.testing.assert_frame_equal(restored.frame, merged.frame)


if __name__ == '__main__':
    unittest.main()
