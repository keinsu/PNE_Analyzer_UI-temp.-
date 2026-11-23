import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from studio.compact_data import CompactStore
from studio.ingestion import load_inputs
from studio.models import ChannelData
from studio.studies import Study, StudyCellStore
from tests.benchmark_workspace import synthetic_zip


class StudyWorkflowTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.study = Study.create(self.root / "library", "BOL and life")

    def accept(self, *, life=(), rpt=()):
        draft, _, changed = self.study.preview_cycles(life, lambda: False, rpt)
        self.assertTrue(changed)
        self.study.commit(draft)
        self.study = Study(self.study.directory)

    def rpt_channel(self, name, link, resistance=20.0):
        rows = []
        for step, direction in (("1", "CHARGE"), ("2", "DISCHARGE"), ("12", "DISCHARGE")):
            for seconds in range(11):
                pulse = step == "12"
                current = 1000 if direction == "CHARGE" else -1000
                voltage = 4 - resistance * 1e-3 * seconds / 10 if pulse else 4 - seconds / 20
                rows.append(dict(Cycle=1, StepNo=step, PhysicalStepID=step, _row=len(rows),
                                 Source_File=name, TypeStd="REST" if pulse and seconds == 0 else direction,
                                 MatchStatus="CSV", StepMode="CC", StepTime_s=seconds, Voltage_V=voltage,
                                 Current_mA=0 if pulse and seconds == 0 else current,
                                 Capacity_mAh=0.3 * seconds, Energy_mWh=1.2 * seconds))
        rules = {"rpt": [], "rpt_protocol": {"charge": "1", "discharge": "2", "pulse": "12", "soc": "50"}}
        return ChannelData(name, name, str(self.root / (name + ".csv")), pd.DataFrame(rows),
                           metadata={"sha256": name, "parse_fingerprint": name, "import_kind": "rpt",
                                     "import_rules": rules, "rpt_link": link})

    def life_channel(self):
        path = self.root / "life.zip"
        synthetic_zip(path, 1, 5, 50)
        return load_inputs([str(path)], cache_dir=self.study.cache_dir, disk_backed=True)[0]

    def test_bol_first_and_later_life_use_same_management_id(self):
        bol = self.rpt_channel("BOL-device", {"cell_id": "", "cell_name": "Sample A",
                                               "management_id": "A-001", "stage": "bol", "cycle": 0})
        self.accept(rpt=[bol])
        cell = self.study.data["cells"][0]
        self.assertFalse(self.study.channels()[0].metadata["has_life"])
        self.assertEqual(self.study.channels()[0].metadata["management_id"], "A-001")
        self.assertAlmostEqual(self.study.channels()[0].metadata["dcir_baseline_mohm"], 20)
        self.accept(life=[(self.life_channel(), cell["id"], "", 1, None)])
        rpt = self.rpt_channel("later-device", {"cell_id": cell["id"], "stage": "checkpoint", "cycle": 5}, 25)
        self.accept(rpt=[rpt])
        info = self.study.channels()[0].metadata
        self.assertTrue(info["has_life"])
        self.assertEqual(info["latest_rpt_cycle"], 5)
        self.assertEqual([event["label"] for event in self.study.timeline(cell["id"])],
                         ["BOL RPT", "1–5 Cycle 수명", "5 Cycle RPT"])
        self.assertEqual([point["Cycle"] for point in info["dcir50"]], [5])
        self.assertAlmostEqual(info["dcir50"][0]["Value"], 25)

    def test_life_first_can_reference_another_cells_bol_without_opening_diagnostics(self):
        self.accept(life=[(self.life_channel(), None, "Sample B", 1, None, "B-001")])
        target = self.study.data["cells"][0]
        self.assertIsNone(self.study.channels()[0].metadata["dcir_baseline_mohm"])
        bol = self.rpt_channel("BOL-reference", {"cell_id": "", "cell_name": "Reference Cell",
                                                  "management_id": "REF-001", "stage": "bol", "cycle": 0})
        self.accept(rpt=[bol])
        reference = self.study.data["cells"][1]
        self.study.set_bol_reference(target["id"], reference["id"])
        self.study = Study(self.study.directory)
        with patch.object(StudyCellStore, "step_table", side_effect=AssertionError("eager life Steps")), \
             patch.object(CompactStore, "steps", side_effect=AssertionError("eager RPT Steps")), \
             patch.object(CompactStore, "rpt_rows", side_effect=AssertionError("eager RPT results")):
            channels = self.study.channels()
        info = channels[0].metadata
        self.assertEqual(info["reference_bol_cell_id"], reference["id"])
        self.assertIn("REF-001", info["reference_bol_label"])
        self.assertAlmostEqual(info["dcir_baseline_mohm"], 20)
        self.assertFalse(info["has_bol"])
        self.assertEqual([event["kind"] for event in self.study.timeline(target["id"])], ["life"])
