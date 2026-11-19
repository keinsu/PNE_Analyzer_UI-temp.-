import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from studio.analysis import apply_cycle_baseline, cycle_aggregates, prepare_cycle_tables, raw_curves
from studio.ingestion import load_inputs
from studio.studies import Study
from tests.benchmark_workspace import synthetic_zip


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.study = Study.create(self.root / "library", "A분석")

    def snapshot(self, name, cycles):
        path = self.root / f"{name}.zip"
        synthetic_zip(path, 1, cycles, 50)
        return load_inputs([str(path)], cache_dir=self.study.cache_dir, disk_backed=True)[0]

    def preview(self, channel, cell=None, segment=None, start=101, name="Cell-01"):
        return self.study.preview_cycles([(channel, cell, name, start, segment)], lambda: False)

    def accept(self, preview):
        draft, _, changed = preview
        self.assertTrue(changed)
        self.study.commit(draft)

    def test_live_snapshots_reopen_and_append_only_changed_cycles(self):
        first = self.snapshot("150", 50)
        self.accept(self.preview(first))
        self.study.save_settings({"baseline": "102"})
        self.study = Study(self.study.directory)
        cell = self.study.data["cells"][0]
        segment = cell["segments"][0]
        self.assertEqual((segment["start"], segment["end"]), (101, 150))
        later = self.snapshot("180", 80)
        later.name = "New device channel"
        with patch("studio.studies.cycle_aggregates", wraps=cycle_aggregates) as prepare:
            self.accept(self.preview(later, cell["id"], segment["id"], start=999))
            self.assertEqual(prepare.call_args.kwargs["cycles"], list(range(51, 81)))
        self.assertFalse(self.preview(later, cell["id"], segment["id"])[2])
        self.assertFalse(self.preview(first, cell["id"], segment["id"])[2])
        study_cell = self.study.channels()[0]
        self.assertEqual(study_cell.id, cell["id"])
        base = prepare_cycle_tables(study_cell, False, None)["representative"]
        result = apply_cycle_baseline(base, study_cell.name, 102)
        self.assertEqual(result.Cycle.tolist(), list(range(101, 181)))
        self.assertEqual(result.iloc[1].Discharge_Capacity_Retention_pct, 100)
        self.assertEqual(self.study.data["settings"]["baseline"], "102")
        measured = raw_curves(study_cell, [180])
        expected = raw_curves(later, [80])
        np.testing.assert_array_equal(measured[0]["y"], expected[0]["y"])
        self.study.undo()
        self.assertEqual(self.study.channels()[0].cycles[-1], 150)

    def test_separate_segment_overlap_and_summary_without_raw(self):
        first = self.snapshot("one", 4)
        self.accept(self.preview(first, start=1))
        cell = self.study.data["cells"][0]
        second = self.snapshot("two", 3)
        with self.assertRaisesRegex(ValueError, "겹침"):
            self.preview(second, cell["id"], start=4)
        self.accept(self.preview(second, cell["id"], start=5))
        frame = prepare_cycle_tables(self.study.channels()[0], False, None)["representative"]
        self.assertEqual(frame.Cycle.tolist(), list(range(1, 8)))
        raw_dir = self.study.directory / cell["segments"][0]["store"]
        offline = raw_dir.with_name("moved-for-test")
        os.rename(raw_dir, offline)
        restored = Study(self.study.directory).channels()[0]
        self.assertTrue(restored.warnings)
        self.assertEqual(prepare_cycle_tables(restored, False, None)["representative"].Cycle.tolist(),
                         list(range(1, 8)))

    def test_rpt_registers_without_changing_life_and_older_revision_rejected(self):
        path = self.root / "RPT.dat"
        path.write_bytes(b"synthetic RPT record")
        draft = self.study.preview_rpt([path], lambda: False)
        self.accept(draft)
        self.assertFalse(self.study.preview_rpt([path], lambda: False)[2])
        self.assertEqual(self.study.channels(), [])
        with self.assertRaisesRegex(ValueError, "다른 창"):
            self.study.commit(draft[0])

    def test_historical_change_reports_capacity_and_requires_commit(self):
        from dataclasses import replace
        initial = self.snapshot("original", 5)
        self.accept(self.preview(initial))
        cell = self.study.data["cells"][0]
        segment = cell["segments"][0]
        frame = initial.read_rows()
        mask = (frame["Cycle"] == 2) & (frame["TypeStd"] == "DISCHARGE")
        frame.loc[mask, "Capacity_mAh"] *= 0.8
        revised = replace(initial, frame=frame, metadata=dict(initial.metadata, sha256="revised",
                                                               parse_fingerprint="revised"))
        draft, messages, changed = self.preview(revised, cell["id"], segment["id"])
        self.assertTrue(changed)
        self.assertTrue(any("102 Cycle 방전용량" in message for message in messages))
        stored = Study(self.study.directory).data["cells"][0]["segments"][0]
        self.assertEqual(stored["digest"], initial.metadata["sha256"])
        self.study.commit(draft)
        self.assertEqual(self.study.data["cells"][0]["segments"][0]["digest"], "revised")

    def test_batched_hash_matches_legacy_saved_signatures(self):
        import hashlib
        import json
        import pandas as pd
        from studio.studies import _compare_batches

        source = self.snapshot("legacy", 8)
        excluded = {"_row", "Source_File", "TotalTime_s"}
        mapping = {"Cycle", "OriginalCycle", "TotalCycle", "StepNo", "StepMode",
                   "PhysicalStepID", "TypeStd", "MatchStatus", "StateCode"}

        def old_digest(frame):
            prefix = json.dumps(list(frame.columns)).encode()
            return hashlib.sha256(prefix + pd.util.hash_pandas_object(
                frame, index=False).to_numpy().tobytes()).hexdigest()

        legacy = {}
        for cycle in source.cycles:
            frame = source.read_rows(source.cycle_ranges[cycle]).drop(columns=list(excluded))
            legacy[str(cycle)] = dict(hash=old_digest(frame), rows=len(frame),
                                     raw_hash=old_digest(frame.drop(columns=list(mapping))))
        with patch("studio.studies._COMPARE_BATCH_ROWS", 350):
            result = list(_compare_batches(source, source.cycles, legacy, lambda: False))
        self.assertEqual({str(c): entry for c, entry, _, _ in result}, legacy)
        self.assertTrue(all(same for _, _, same, _ in result))
