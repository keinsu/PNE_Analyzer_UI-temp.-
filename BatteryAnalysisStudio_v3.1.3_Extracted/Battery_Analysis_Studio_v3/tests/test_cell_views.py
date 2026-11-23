import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from studio.cell_views import RPTComparison, checkpoint_label, rpt_capacity_energy, rpt_checkpoints, rpt_dcir_rows


def cell(cid, **metadata):
    return SimpleNamespace(id=cid, name=cid, metadata=metadata)


def rpt(rid, owner, stage, cycle, rows=None):
    link = {"cell_id": owner, "cycle": cycle}
    if stage is not None:
        link["stage"] = stage
    return SimpleNamespace(id=rid, name=rid, fingerprint=rid, source_path=f"{rid}.zip",
                           metadata={"import_kind": "rpt", "rpt_link": link},
                           frame=SimpleNamespace(rpt_rows=Mock(return_value=rows or [])))


class CellViewTests(unittest.TestCase):
    def test_checkpoints_return_selected_cell_and_reference_bol(self):
        active = cell("C", reference_bol_cell_id="A", reference_bol_label="Ref A")
        reference = rpt("a-bol", "A", "bol", 0)
        reference_100 = rpt("a-100", "A", "checkpoint", 100)
        first = rpt("c-100", "C", None, 100)
        last = rpt("c-200", "C", "checkpoint", 200)
        self.assertEqual(rpt_checkpoints(active, [last, reference_100, first, reference]),
                         [reference, first, last])
        self.assertEqual(checkpoint_label(reference, active), "BOL · Ref A")
        self.assertEqual(checkpoint_label(first, active), "100 Cycle")
        self.assertEqual(checkpoint_label(last, active), "200 Cycle")

    def test_capacity_prefers_standard_result_and_missing_soc_is_visible_in_detail(self):
        rows = [
            {"Item": "RPT 충전용량", "Value": 99.},
            {"Item": "RPT 충전용량", "Metric": "RPT 충전용량", "Value": 83.},
            {"Item": "방전 10초 DCIR", "Direction": "DISCHARGE", "Pulse": 1, "Value": 4.2,
             "Status": "SOC 개수 불일치"},
            {"Item": "방전 10초 DCIR", "Direction": "CHARGE", "SOC_pct": 50., "Value": 4.3},
        ]
        self.assertEqual(rpt_capacity_energy(rows)["충전 용량 (mAh)"], 83.)
        self.assertIsNone(rpt_capacity_energy(rows)["충전 에너지 (mWh)"])
        detail = rpt_dcir_rows("BOL", rows)
        self.assertEqual(len(detail), 1)
        self.assertIsNone(detail[0]["SOC (%)"])
        self.assertEqual(detail[0]["상태"], "SOC 개수 불일치")

    def test_rpt_rows_load_only_for_visible_cell_and_comparison_emits_checked_records(self):
        app = QApplication.instance() or QApplication([])
        active = cell("C")
        first = rpt("c-bol", "C", "bol", 0, [
            {"Item": "방전 10초 DCIR", "Direction": "DISCHARGE", "SOC_pct": 50., "Value": 4.2}])
        middle = rpt("c-100", "C", "checkpoint", 100)
        last = rpt("c-200", "C", "checkpoint", 200)
        unrelated = rpt("a-100", "A", "checkpoint", 100)
        panel = RPTComparison()
        comparison = Mock()
        panel.compareRequested.connect(comparison)
        panel.set_context(active, [last, middle, first, unrelated])
        first.frame.rpt_rows.assert_not_called()
        panel.show()
        app.processEvents()
        self.assertEqual(panel.selected_checkpoints(), [first, last])
        self.assertEqual(len(panel.graph.items), 1)
        panel.checkpoints.topLevelItem(1).setCheckState(0, Qt.CheckState.Checked)
        panel.compare_selected()
        comparison.assert_called_once_with([first, middle, last])
        panel.set_context(active, [last, middle, first, unrelated])
        first.frame.rpt_rows.assert_called_once()
        unrelated.frame.rpt_rows.assert_not_called()
        panel.close()
