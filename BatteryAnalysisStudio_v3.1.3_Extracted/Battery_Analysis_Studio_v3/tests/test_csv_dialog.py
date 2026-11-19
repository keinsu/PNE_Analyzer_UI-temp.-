from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication, QDialogButtonBox

from studio.csv_dialog import CSVImportDialog
from studio.ingestion import load_inputs


class CSVDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def select(self, dialog, key, column):
        combo = dialog.column_combos[key]
        index = combo.findData(column)
        self.assertGreaterEqual(index, 0)
        combo.setCurrentIndex(index)

    def test_cp949_auto_header_and_options_reproduce_import(self):
        path = self.root / "한글.csv"
        path.write_text("시험 정보\nCycle,StepNo,StepType,StepTime,Voltage(V),Current(mA),Capacity(mAh)\n"
                        "1,1,충전,0,3.0,1000,0\n1,1,충전,1,4.0,1000,0.2777778\n", encoding="cp949")
        dialog = CSVImportDialog(path)
        self.assertEqual(dialog.encoding, "cp949")
        self.assertEqual(dialog.header_spin.value(), 2)
        self.assertEqual(dialog._validation_error(), "")
        options = dialog.options()
        self.assertEqual(options["skip_lines"], 1)
        channel = load_inputs([str(path)], csv_options=options)[0]
        saved = channel.metadata["csv_options"]
        self.assertEqual(saved["mapping"], options["mapping"])
        restored = load_inputs([str(path)], csv_options={str(path.resolve()): saved})[0]
        self.assertEqual(channel.id, restored.id)
        self.assertEqual(channel.frame["Capacity_mAh"].tolist(), restored.frame["Capacity_mAh"].tolist())
        dialog.close()

    def test_manual_unknown_columns_unit_override_and_separator(self):
        path = self.root / "manual.csv"
        path.write_text("Metadata\nC;Kind;T;U;I;Q;E\n1;charge;0;3000;1;0;0\n1;charge;1;4000;1;0.0002777778;0.0009\n", encoding="utf-8")
        dialog = CSVImportDialog(path)
        dialog.header_spin.setValue(2)
        dialog.separator_combo.setCurrentIndex(dialog.separator_combo.findData(";"))
        for key, column in (("cycle", "C"), ("stepType", "Kind"), ("time", "T"),
                            ("voltage", "U"), ("current", "I"), ("capacity", "Q"), ("energy", "E")):
            self.select(dialog, key, column)
        dialog.current_unit_combo.setCurrentIndex(dialog.current_unit_combo.findData("A"))
        dialog.capacity_unit_combo.setCurrentIndex(dialog.capacity_unit_combo.findData("Ah"))
        dialog.voltage_unit_combo.setCurrentIndex(dialog.voltage_unit_combo.findData("mV"))
        dialog.energy_unit_combo.setCurrentIndex(dialog.energy_unit_combo.findData("Wh"))
        self.assertEqual(dialog._validation_error(), "")
        channel = load_inputs([str(path)], csv_options=dialog.options())[0]
        self.assertAlmostEqual(channel.frame.iloc[-1]["Current_mA"], 1000)
        self.assertAlmostEqual(channel.frame.iloc[-1]["Capacity_mAh"], .2777778)
        self.assertAlmostEqual(channel.frame.iloc[-1]["Voltage_V"], 4)
        self.assertAlmostEqual(channel.frame.iloc[-1]["Energy_mWh"], .9)
        self.assertEqual(channel.metadata["csv_options"]["separator"], ";")
        restored = load_inputs([str(path)], csv_options=channel.metadata["csv_options"])[0]
        self.assertAlmostEqual(restored.frame.iloc[-1]["Voltage_V"], 4)
        dialog.close()

    def test_split_capacity_validation_prevents_same_or_missing_columns(self):
        path = self.root / "split.csv"
        path.write_text("Cycle,Voltage(V),chgCapacity(mAh),dchgCapacity(mAh)\n1,3.0,1,0\n1,4.0,2,0\n", encoding="utf-8")
        dialog = CSVImportDialog(path)
        self.assertEqual(dialog.mode_combo.currentData(), "split")
        self.assertEqual(dialog._validation_error(), "")
        self.select(dialog, "dchgCap", "chgCapacity(mAh)")
        self.assertIn("서로 다른", dialog._validation_error())
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        self.select(dialog, "dchgCap", "")
        self.assertIn("모두 연결", dialog._validation_error())
        dialog.close()

    def test_wrong_header_and_missing_file_do_not_accept(self):
        path = self.root / "bad.csv"
        path.write_text("Cycle,Cycle,Capacity\n1,3.0,5\n", encoding="utf-8")
        dialog = CSVImportDialog(path)
        self.assertIn("중복", dialog._validation_error())
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        dialog.close()
        missing = CSVImportDialog(self.root / "missing.csv")
        self.assertTrue(missing._validation_error())
        self.assertFalse(missing.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        missing.close()


if __name__ == "__main__":
    unittest.main()
