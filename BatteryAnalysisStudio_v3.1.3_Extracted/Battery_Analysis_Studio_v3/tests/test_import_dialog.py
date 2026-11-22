import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QPushButton
from studio.import_dialog import RuleDialog, classify_channels


class ImportDialogTests(unittest.TestCase):
    def test_capacity_checkbox_serializes_step_and_restores_preset(self):
        app = QApplication.instance() or QApplication([])
        channel = SimpleNamespace(name="First", source_path="source.cyc", metadata={})
        with patch("studio.import_dialog.step_options", return_value=[("7", "CHARGE", 10, 100)]):
            dialog = RuleDialog(None, [channel])
        checkbox = dialog.capacity["CHARGE"]["7"]
        self.assertFalse(checkbox.isChecked())
        checkbox.click()
        rules = dialog.rules()
        self.assertEqual(rules["capacity"]["CHARGE"], ["7"])
        checkbox.click()
        dialog.set_rules(rules)
        self.assertTrue(checkbox.isChecked())
        self.assertFalse(any(button.text() == "계산 미리보기" for button in dialog.findChildren(QPushButton)))
        dialog.close()

    def test_one_dialog_for_nested_channels_and_one_for_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "trial"
            folder.mkdir()
            archive = root / "other.zip"
            channels = [SimpleNamespace(source_path=str(folder / "CH1" / "a.cyc"), metadata={}),
                        SimpleNamespace(source_path=str(folder / "CH2" / "a.cyc"), metadata={}),
                        SimpleNamespace(source_path=str(archive), metadata={"source_member": "A/a.cyc"}),
                        SimpleNamespace(source_path=str(archive), metadata={"source_member": "B/a.cyc"})]
            with patch("studio.import_dialog.RuleDialog") as dialog, patch(
                    "studio.import_dialog.apply_rules", side_effect=lambda channel, rules: channel):
                dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
                self.assertEqual(classify_channels(None, channels, paths=[folder, archive]), channels)
                self.assertEqual([args.args[1] for args in dialog.call_args_list], [channels[:2], channels[2:]])
