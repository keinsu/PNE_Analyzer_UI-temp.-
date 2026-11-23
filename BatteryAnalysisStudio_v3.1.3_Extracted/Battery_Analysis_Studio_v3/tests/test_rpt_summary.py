import unittest
from types import SimpleNamespace

from studio.rpt_summary import rpt_summary


class RPTSummaryTests(unittest.TestCase):
    def test_overview_preserves_channels_units_soc_order_and_missing_status(self):
        first = SimpleNamespace(name="A", source_path="a.csv",
                                metadata={"rpt_link": {"cell_id": "cell-a", "cycle": 100}})
        second = SimpleNamespace(name="B", source_path="b.csv", metadata={})
        rows = [[dict(Metric="RPT 충전에너지", Value=42),
                 dict(Item="방전 10초 DCIR", SOC_pct=50, Value=12),
                 dict(Item="방전 10초 DCIR", SOC_pct=90, Value=9)],
                [dict(Item="방전 10초 DCIR", SOC_pct=None, Value=7, Status="SOC 개수 불일치")]]
        table = rpt_summary([first, second], rows, {"cell-a": "Cell A"})
        self.assertEqual(table["RPT 채널"].tolist(), ["A", "B"])
        self.assertEqual(table.iloc[0]["측정 Cycle"], 100)
        self.assertEqual(table.iloc[0]["충전 에너지 (mWh)"], 42)
        self.assertEqual(table.iloc[0]["SOC 50% (mΩ)"], 12)
        self.assertLess(table.columns.get_loc("SOC 90% (mΩ)"), table.columns.get_loc("SOC 50% (mΩ)"))
        self.assertIn("불일치", table.iloc[1]["DCIR 상태"])
