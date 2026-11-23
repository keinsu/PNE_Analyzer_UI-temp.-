"""Regression coverage for independent derivative caching and lazy project data."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from studio.compact_data import CompactStore, prepare_channel, read_step_table
from studio.derivative_cache import cached_derivatives
from studio.models import ChannelData
from studio.project import load_project, project_channels, save_project
from tests.test_compact_project import measurements


def derivative_result(cycle):
    grid = np.arange(3, dtype=float)
    return dict(cycle=cycle, voltage_grid=grid, dqdv=grid, capacity_on_vgrid=grid,
                capacity_grid=grid, dvdq=grid, x=grid, y=grid, vq_x=grid, vq_y=grid,
                dvdq_x=grid, dvdq_y=grid, error="")


class WorkflowDataCacheTests(unittest.TestCase):
    def test_overlapping_selection_reuses_successful_curves_and_keeps_data_errors(self):
        channel = SimpleNamespace(id="cell", fingerprint="measurements", name="Cell")

        def solve(ch, cycles, **options):
            result = []
            for cycle in cycles:
                result.append(derivative_result(cycle))
                if cycle == 2:
                    result.append(dict(cycle=cycle, error="유효한 연속 측정점이 5개 미만입니다."))
            return result

        with tempfile.TemporaryDirectory() as folder, patch(
                "studio.derivative_cache.gettempdir", return_value=folder), patch(
                "studio.derivative_cache.derivative_curves", side_effect=solve) as compute:
            cached_derivatives(channel, [1, 2], {"direction": "both"}, None)
            curves = cached_derivatives(channel, [2, 3], {"direction": "both"}, None)
            self.assertEqual([c["cycle"] for c in curves], [2, 2, 3])
            self.assertEqual([call.args[1] for call in compute.call_args_list], [[1, 2], [3]])
            self.assertEqual(curves[1]["error"], "유효한 연속 측정점이 5개 미만입니다.")
            np.testing.assert_array_equal(curves[0]["x"], np.arange(3))

    def test_project_open_defers_diagnostics_and_reuses_supplied_summary(self):
        raw = ChannelData("cell", "Cell", "source.csv", measurements(),
                          steps=pd.DataFrame({"StepNo": [7]}))
        table = pd.DataFrame({"Cycle": [1], "Discharge_Energy_mWh": [123.]})
        with tempfile.TemporaryDirectory() as folder, patch(
                "studio.compact_data.cycle_aggregates",
                side_effect=AssertionError("supplied summary must be reused")):
            root = Path(folder)
            prepared = prepare_channel(raw, root, cycle_tables={
                False: {"representative": table, "step_sum": table}})
            self.assertEqual(prepared.frame.tables(False)["representative"].iloc[0]["Discharge_Energy_mWh"], 123.)
            project = root / "trial.basproj"
            save_project(str(project), [prepared], {})
            with patch("studio.ingestion._cache_read", side_effect=AssertionError("unexpected Step read")):
                restored = project_channels(load_project(str(project)))[0]
            self.assertTrue(restored.steps.empty)
            self.assertEqual(read_step_table(restored)["StepNo"].tolist(), [7])
            self.assertEqual(restored.frame.dcr(False)["blocks"], [])

    def test_legacy_embedded_results_are_read_without_unpacking_steps(self):
        raw = ChannelData("cell", "Cell", "source.csv", measurements())
        with tempfile.TemporaryDirectory() as folder:
            prepared = prepare_channel(raw, folder)
            path = prepared.frame.path
            manifest_path = path / "channel.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            dcr_path, rpt_path = path / "dcr.json", path / "rpt.json"
            manifest["metadata"]["fixed_dcr"] = json.loads(dcr_path.read_text(encoding="utf-8"))
            manifest["metadata"]["rpt_rows"] = [{"Value": 20.0}]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            dcr_path.unlink()
            rpt_path.unlink()
            legacy = CompactStore(path)
            with patch("studio.ingestion._cache_read", side_effect=AssertionError("unexpected Step read")):
                self.assertTrue(legacy.channel_metadata()[0]["compact_curves"])
                self.assertEqual(legacy.rpt_rows(), [{"Value": 20.0}])
                self.assertEqual(legacy.dcr(False)["blocks"], [])
