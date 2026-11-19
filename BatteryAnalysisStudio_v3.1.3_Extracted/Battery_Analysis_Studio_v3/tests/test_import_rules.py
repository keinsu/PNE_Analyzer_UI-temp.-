import tempfile
import unittest
from pathlib import Path

import pandas as pd

from studio.analysis import cycle_aggregates
from studio.compact_data import prepare_channel
from studio.import_rules import apply_rules, rpt_rows, step_options
from studio.models import ChannelData
from studio.project import load_project, project_channels, save_project
from studio.studies import Study


def fixture():
    rows = [dict(Cycle=1, StepNo=step, PhysicalStepID=str(step), TypeStd=mode,
                 MatchStatus="CSV", StepTime_s=t, Capacity_mAh=t * amount,
                 Voltage_V=3.6 + (t * .1), Current_mA=-100 if mode == "DISCHARGE" else 100,
                 _row=2 * i + t)
            for i, (step, mode, amount) in enumerate([(1, "CHARGE", 5), (2, "DISCHARGE", 4),
                                                       (3, "DISCHARGE", 3), (4, "DISCHARGE", 9)])
            for t in range(2)]
    return ChannelData("cell", "sample", "data.csv", pd.DataFrame(rows), metadata={"sha256": "example"})


class ImportRuleTests(unittest.TestCase):
    def test_capacity_is_sum_of_selected_steps_in_both_summary_modes(self):
        channel = fixture()
        self.assertEqual(len(step_options(channel)), 4)
        configured = apply_rules(channel, dict(kind="cycle", capacity={"DISCHARGE": ["2", "3"]}, rpt=[]))
        self.assertEqual(channel.id, configured.id)
        self.assertNotEqual(channel.fingerprint, configured.fingerprint)
        for table in cycle_aggregates(configured).values():
            self.assertEqual(table.iloc[0]["Discharge_Capacity_mAh"], 7)

    def test_rpt_saved_in_compact_project_and_study(self):
        rules = dict(kind="rpt", capacity={}, rpt=[dict(name="종료 전압", step="4", field="Voltage_V", operation="last")])
        configured = apply_rules(fixture(), rules)
        value = rpt_rows(configured, rules["rpt"])[0]["Value"]
        self.assertAlmostEqual(value, 3.7)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compact = prepare_channel(configured, root / "cache")
            path = root / "example.basproj"
            save_project(str(path), [compact], {})
            reloaded = project_channels(load_project(str(path)))[0]
            self.assertEqual(reloaded.metadata["import_rules"], rules)
            self.assertEqual(reloaded.frame.rpt_rows()[0]["Value"], value)
            study = Study.create(root / "studies", "RPT")
            candidate, _, changed = study.preview_cycles([], lambda: False, [configured])
            self.assertTrue(changed)
            study.commit(candidate)
            reopened = Study(study.directory).channels()[0]
            self.assertEqual(reopened.frame.rpt_rows()[0]["Value"], value)
