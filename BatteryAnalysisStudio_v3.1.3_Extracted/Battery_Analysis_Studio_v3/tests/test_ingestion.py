"""Independent byte-level and CSV fixtures; no instrument-data claim."""
from __future__ import annotations

import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

import numpy as np

from studio.ingestion import load_inputs, _read_steps, _Source


def make_pne_bytes(cycles=(1, 1), initial=False):
    """Pack raw fields individually, independently of ingestion's NumPy dtype."""
    records, summaries = [], []
    if initial:
        raw = bytearray(176)
        struct.pack_into("<f", raw, 4, 7.04e28)
        struct.pack_into("<f", raw, 16, 6.98e19)
        records.append(bytes(raw))
    for i, cycle in enumerate(cycles):
        discharge = i % 2 == 1
        current = -1000.0 if discharge else 1000.0
        voltages = [4000., 3500., 3000.] if discharge else [3000., 3500., 4000.]
        for t, voltage in enumerate(voltages):
            raw = bytearray(176)
            for offset, value in {0: len(records) + 1, 4: t, 8: 0, 16: voltage,
                                  20: current, 24: t * .3, 32: 0 if discharge else t * .3,
                                  36: t * .3 if discharge else 0, 40: t * 1.05, 64: 25., 68: 10., 72: voltage * current / 1000}.items():
                struct.pack_into("<f", raw, offset, value)
            records.append(bytes(raw))
        summaries.append(f"{i * 2 + 1},{'방전' if discharge else '충전'},0,{cycle},0,2,{voltages[-1] / 1000},{current / 1000},0.0006,0.0021")
    text = "시험 메타데이터\nStepNo,StepType,StateCode,CurCycle,TotalCycle,StepTime,Voltage,Current,Capacity,WattHour\n" + "\n".join(summaries)
    return bytes(72) + b"".join(records), text.encode("cp949")


def make_csv_bytes():
    return ("시험 메타데이터\nCycle,StepNo,StepType,StepTime_s,Voltage_V,Current_mA,Capacity_mAh,Energy_mWh,Temp_C\n"
            "1,1,충전,0,3.0,1000,0,0,25\n"
            "1,1,충전,1,3.5,1000,0.2777778,0.9,25\n"
            "1,1,충전,2,4.0,1000,0.5555556,1.9,26\n"
            "1,3,방전,0,4.0,-1000,0,0,26\n"
            "1,3,방전,1,3.5,-1000,0.2777778,0.9,26\n"
            "1,3,방전,2,3.0,-1000,0.5555556,1.9,25\n").encode("cp949")


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_disk_cache_preserves_raw_and_reuses_preview_indices(self):
        import pandas as pd
        from tests.benchmark_workspace import synthetic_zip
        from studio.analysis import raw_curves
        from studio.raw_store import PREVIEW_POINTS

        path = self.root / "미리보기.zip"
        synthetic_zip(path, 1, 2, 1800)
        expected = load_inputs([str(path)])[0]
        kwargs = dict(cache_dir=self.root / "disk", disk_backed=True)
        disk = load_inputs([str(path)], **kwargs)[0]
        cached = load_inputs([str(path)], **kwargs)[0]
        self.assertTrue(disk.metadata["disk_backed"])
        self.assertTrue(cached.metadata["cache_hit"])
        pd.testing.assert_frame_equal(expected.frame, cached.read_rows(), check_exact=True)
        full = raw_curves(expected, [2])
        preview = raw_curves(cached, [2], display_budget=PREVIEW_POINTS)
        self.assertTrue(cached.frame.preview_path.is_file())
        for before, after in zip(full, preview, strict=True):
            np.testing.assert_array_equal(before["x"], after["x"])
            np.testing.assert_array_equal(before["y"], after["y"])
            indices = after["plot_indices"]
            self.assertLess(len(indices), len(before["x"]))
            self.assertEqual(indices[0], 0)
            self.assertEqual(indices[-1], len(before["x"]) - 1)

    def write_zip(self, name="시험.zip", cycles=(1, 1), initial=False, extra_csv=False):
        cyc, steps = make_pne_bytes(cycles, initial)
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("한단계추가/시험/M02Ch001/data.cyc", cyc)
            archive.writestr("한단계추가/시험/M02Ch001/data_StepEnd.csv", steps)
            archive.writestr("../unsafe.cyc", cyc)
            if extra_csv:
                archive.writestr("한단계추가/일반.csv", make_csv_bytes())
        return path

    def test_nested_zip_and_go_matching_thresholds(self):
        channel = load_inputs([str(self.write_zip())])[0]
        self.assertEqual(channel.name, "M02Ch001")
        self.assertEqual(channel.frame["MatchStatus"].unique().tolist(), ["MATCHED"])
        self.assertEqual(channel.steps["StepNo"].tolist(), ["1", "3"])
        self.assertEqual(channel.steps["TypeStd"].tolist(), ["CHARGE", "DISCHARGE"])
        np.testing.assert_allclose(channel.frame["Voltage_V"].to_numpy(), [3, 3.5, 4, 4, 3.5, 3])
        np.testing.assert_allclose(channel.frame["Capacity_mAh"].to_numpy(), [0, .3, .6, 0, .3, .6], atol=1e-6)
        self.assertEqual(channel.cycles, [1])
        self.assertEqual(channel.metadata["status_counts"], {"MATCHED": 2})
        self.assertEqual(len(channel.accepted_frame()), 6)

    def test_initialization_preserved_and_excluded(self):
        channel = load_inputs([str(self.write_zip(initial=True))])[0]
        self.assertEqual(channel.frame.iloc[0]["MatchStatus"], "INITIAL")
        self.assertEqual(len(channel.frame), 7)
        self.assertEqual(len(channel.accepted_frame()), 6)
        self.assertEqual(channel.steps["MatchStatus"].tolist(), ["INITIAL", "MATCHED", "MATCHED"])

    def test_cycle_restart_has_unique_internal_cycles(self):
        channel = load_inputs([str(self.write_zip(cycles=(1, 2, 1)))])[0]
        self.assertEqual(channel.cycles, [1, 2, 3])
        self.assertEqual(channel.steps["OriginalCycle"].tolist(), ["1", "2", "1"])

    def test_unknown_format_fails_without_truncation(self):
        path = self.root / "unknown.cyc"
        path.write_bytes(b"\x00" * (72 + 177))
        with self.assertRaisesRegex(ValueError, "72B"):
            load_inputs([str(path)])
        path.write_bytes(b"\x00" * (72 + 176))
        with self.assertRaisesRegex(ValueError, "셀 전압"):
            load_inputs([str(path)])

    def test_csv_cp949_units_and_repeated_physical_step(self):
        path = self.root / "한글 CSV.csv"
        path.write_bytes(make_csv_bytes())
        channel = load_inputs([str(path)])[0]
        self.assertEqual(channel.metadata["encoding"], "cp949")
        self.assertEqual(channel.frame["PhysicalStepID"].nunique(), 2)
        self.assertEqual(channel.frame.iloc[-1]["TypeStd"], "DISCHARGE")
        self.assertAlmostEqual(channel.frame.iloc[-1]["Capacity_mAh"], .5555556)
        self.assertEqual(channel.frame["MatchStatus"].unique().tolist(), ["CSV"])

    def test_missing_stepend_retains_raw_without_fabricated_cycle(self):
        path = self.root / "standalone.cyc"
        path.write_bytes(make_pne_bytes()[0])
        channel = load_inputs([str(path)])[0]
        self.assertEqual(len(channel.frame), 6)
        self.assertEqual(channel.cycles, [])
        self.assertEqual(len(channel.accepted_frame()), 0)
        self.assertEqual(channel.steps["MatchStatus"].unique().tolist(), ["UNMATCHED"])

    def test_batch_ignores_unpaired_cyc_and_auxiliary_csv(self):
        cyc, steps = make_pne_bytes()
        path = self.root / "중복 방지.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("시험/M02Ch043[043]/real.cyc", cyc)
            archive.writestr("시험/M02Ch043[043]/real_StepEnd.csv", steps)
            archive.writestr("시험/M02Ch043[043]/empty1.cyc", cyc)
            archive.writestr("시험/M02Ch043[043]/empty2.cyc", cyc)
            archive.writestr("시험/M02Ch043[043]/previous_export.csv", make_csv_bytes())
        channels = load_inputs([str(path)])
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].name, "M02Ch043[043]")
        self.assertEqual(len(channels[0].accepted_frame()), 6)
        self.assertEqual(len(channels[0].metadata["import_notices"]), 2)

    def test_multiple_valid_pairs_have_distinct_labels(self):
        cyc, steps = make_pne_bytes()
        path = self.root / "분할시험.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name in ("part1", "part2"):
                archive.writestr(f"시험/M02Ch043[043]/{name}.cyc", cyc)
                archive.writestr(f"시험/M02Ch043[043]/{name}_StepEnd.csv", steps)
        channels = load_inputs([str(path)])
        self.assertEqual(len(channels), 2)
        self.assertEqual(len({channel.name for channel in channels}), 2)
        self.assertTrue(all("M02Ch043[043] · part" in channel.name for channel in channels))

    def test_partial_failure_and_cancel(self):
        path = self.write_zip()
        channels = load_inputs([str(self.root / "missing.csv"), str(path)])
        self.assertTrue(channels[0].metadata["import_errors"])
        with self.assertRaises(InterruptedError):
            load_inputs([str(path)], cancel=lambda: True)

    def test_cache_is_safe_and_invalidated_by_content(self):
        path = self.root / "csv.csv"
        path.write_bytes(make_csv_bytes())
        cache = self.root / "cache"
        first = load_inputs([str(path)], cache_dir=str(cache))[0]
        second = load_inputs([str(path)], cache_dir=str(cache))[0]
        self.assertFalse(first.metadata["cache_hit"])
        self.assertTrue(second.metadata["cache_hit"])
        self.assertEqual(first.id, second.id)
        np.testing.assert_allclose(first.frame["Voltage_V"], second.frame["Voltage_V"])
        for file in cache.glob("*.npz"):
            with np.load(file, allow_pickle=False) as arrays:
                self.assertTrue(all(a.dtype.kind != "O" for a in arrays.values()))
        path.write_bytes(make_csv_bytes().replace(b"3.5", b"3.6"))
        changed = load_inputs([str(path)], cache_dir=str(cache))[0]
        self.assertFalse(changed.metadata["cache_hit"])
        self.assertNotEqual(first.id, changed.id)

    def test_directory_and_individual_channel(self):
        root = self.root / "시험" / "M02Ch002"
        root.mkdir(parents=True)
        cyc, steps = make_pne_bytes()
        (root / "test.cyc").write_bytes(cyc)
        (root / "test_StepEnd.csv").write_bytes(steps)
        channels = load_inputs([str(root)])
        direct = load_inputs([str(root / "test.cyc")])
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].frame["StepNo"].tolist(), direct[0].frame["StepNo"].tolist())

    def test_stepend_delimiter_and_optional_metadata(self):
        data = b"StepNo;StepType;CurCycle;StepTime;Voltage;Current;Capacity\n1;CC Charge;1;1d 00:00:02;4.2;1;0.01\n"
        steps, _ = _read_steps(data)
        self.assertEqual(steps.iloc[0]["seconds"], 86402)

    def test_alignment_skips_unrelated_stepend_without_renumbering(self):
        cyc, steps = make_pne_bytes()
        text = steps.decode("cp949")
        lines = text.splitlines()
        lines.insert(3, "2,Rest,0,1,0,200,1.1,0,0,0")
        root = self.root / "channel"
        root.mkdir()
        (root / "test.cyc").write_bytes(cyc)
        (root / "test_StepEnd.csv").write_text("\n".join(lines), encoding="utf-8")
        channel = load_inputs([str(root)])[0]
        self.assertEqual(channel.steps["StepNo"].tolist(), ["1", "3"])
        self.assertEqual(channel.steps["MatchStatus"].tolist(), ["MATCHED", "MATCHED"])
        self.assertEqual(channel.metadata["orphan_stepend_rows"], 1)

    def test_review_is_retained_but_requires_explicit_quantitative_use(self):
        cyc, steps = make_pne_bytes()
        steps = steps.decode("cp949").replace(",3.0,-1.0,", ",3.05,-1.0,").encode("cp949")
        root = self.root / "review"
        root.mkdir()
        (root / "test.cyc").write_bytes(cyc)
        (root / "test_StepEnd.csv").write_bytes(steps)
        channel = load_inputs([str(root)])[0]
        self.assertEqual(channel.steps["MatchStatus"].tolist(), ["MATCHED", "REVIEW"])
        self.assertEqual(len(channel.accepted_frame()), 3)
        self.assertEqual(len(channel.accepted_frame(include_review=True)), 6)

    def test_matching_can_be_cancelled_after_read(self):
        path = self.write_zip(cycles=tuple(range(1, 50)))
        count = [0]
        def cancel():
            count[0] += 1
            return count[0] > 60
        with self.assertRaises(InterruptedError):
            load_inputs([str(path)], cancel=cancel)

    def test_csv_control_mode_does_not_erase_signed_direction(self):
        path = self.root / "mode.csv"
        data = make_csv_bytes().decode("cp949").replace("StepType", "StepMode").replace("충전", "CC").replace("방전", "CP")
        path.write_text(data, encoding="utf-8")
        channel = load_inputs([str(path)])[0]
        self.assertEqual(channel.frame["TypeStd"].unique().tolist(), ["CHARGE", "DISCHARGE"])
        self.assertEqual(channel.frame["StepMode"].unique().tolist(), ["CC", "CP"])

    def test_packaged_demo_has_known_dcr_and_complete_source_provenance(self):
        from studio.demo import generate_demo
        from backend.dcr_analysis import detect_dcr_blocks, compute_dcr_table
        paths = generate_demo(self.root / "demo")
        channels = load_inputs(paths)
        self.assertEqual(len(channels), 3)
        self.assertEqual(channels[0].metadata["status_counts"], {"MATCHED": 28})
        self.assertEqual(channels[0].cycles, [1, 2, 3, 4, 5])
        self.assertTrue(all("SYNTHETIC" in c.source_path for c in channels))
        for channel in (channels[0], channels[2]):
            frame = channel.accepted_frame()
            detected = detect_dcr_blocks(frame)
            result = compute_dcr_table(frame, detected, [1., 10., 30.])
            self.assertEqual(len(detected["blocks"]), 2)
            self.assertEqual(len(result["table"]), 4)
            first = result["table"][0]
            np.testing.assert_allclose([first[f"R_{t}s_mOhm"] for t in (1, 10, 30)], [10, 20, 35], atol=1e-3)
            self.assertTrue(all(r["Return_Capacity_Balanced"] for r in result["table"]))

    def test_source_cycle_zero_is_preserved_and_unknown_cycle_is_negative(self):
        path = self.write_zip(cycles=(0, 1, 2))
        channel = load_inputs([str(path)])[0]
        self.assertEqual(channel.cycles, [0, 1, 2])
        self.assertEqual(channel.steps["Cycle"].tolist(), [0, 1, 2])
        missing = self.root / "unknown_cycle.cyc"
        missing.write_bytes(make_pne_bytes()[0])
        unassigned = load_inputs([str(missing)])[0]
        self.assertEqual(unassigned.frame["Cycle"].unique().tolist(), [-1])

    def test_identical_channel_contents_keep_distinct_source_ids(self):
        cyc, steps = make_pne_bytes()
        inputs = self.root / "same_names"
        for folder in (inputs / "Ch1", inputs / "Ch2"):
            folder.mkdir(parents=True)
            (folder / "same.cyc").write_bytes(cyc)
            (folder / "same_StepEnd.csv").write_bytes(steps)
        channels = load_inputs([str(inputs)])
        self.assertEqual(len(channels), 2)
        self.assertNotEqual(channels[0].id, channels[1].id)
        self.assertEqual(channels[0].metadata["sha256"], channels[1].metadata["sha256"])
        direct = load_inputs([str(inputs / "Ch1" / "same.cyc")])[0]
        self.assertEqual(channels[0].id, direct.id)
        archive = self.root / "same.zip"
        with zipfile.ZipFile(archive, "w") as z:
            for name in ("Ch1", "Ch2"):
                z.writestr(f"{name}/same.cyc", cyc)
                z.writestr(f"{name}/same_StepEnd.csv", steps)
        zipped = load_inputs([str(archive)])
        self.assertNotEqual(zipped[0].id, zipped[1].id)

    def test_csv_filtered_record_positions_and_totalcycle_remain_aligned(self):
        path = self.root / "filtered.csv"
        path.write_text("Cycle,TotalCycle,StepNo,Type,StepTime,Voltage(V),Current(mA),Capacity(mAh)\n"
                        "0,0,1,charge,0,3.0,1000,0\n"
                        "0,0,1,charge,1,invalid,1000,0.2\n"
                        "0,0,1,charge,2,3.5,1000,0.4\n"
                        "1,1,1,charge,0,3.0,1000,0\n"
                        "1,1,1,charge,1,4.0,1000,0.3\n", encoding="utf-8")
        channel = load_inputs([str(path)])[0]
        self.assertEqual(channel.frame["_row"].tolist(), [0, 2, 3, 4])
        self.assertEqual(channel.frame["TotalCycle"].tolist(), ["0", "0", "1", "1"])
        self.assertEqual(channel.cycles, [0, 1])
        self.assertTrue(any("1행" in warning for warning in channel.warnings))

    def test_csv_fractional_cycles_are_rejected_instead_of_truncated(self):
        path = self.root / "fractional.csv"
        path.write_bytes(make_csv_bytes().replace(b"\n1,", b"\n1.5,"))
        with self.assertRaisesRegex(ValueError, "정수"):
            load_inputs([str(path)])

    def test_repeated_labels_are_compact_and_cache_preserves_values_and_categories(self):
        import pandas as pd
        from studio.demo import generate_demo
        paths = generate_demo(self.root / "compact")
        cache = self.root / "compact_cache"
        first = load_inputs([paths[0]], cache_dir=cache)[0]
        self.assertIsInstance(first.frame["Source_File"].dtype, pd.CategoricalDtype)
        expanded = first.frame.copy()
        for col in expanded:
            if isinstance(expanded[col].dtype, pd.CategoricalDtype):
                expanded[col] = expanded[col].astype(object)
        compact_bytes = int(first.frame.memory_usage(deep=True).sum())
        expanded_bytes = int(expanded.memory_usage(deep=True).sum())
        self.assertLess(compact_bytes, expanded_bytes * .7)
        second = load_inputs([paths[0]], cache_dir=cache)[0]
        self.assertTrue(second.metadata["cache_hit"])
        pd.testing.assert_frame_equal(first.frame, second.frame)
        self.assertEqual(first.fingerprint, second.fingerprint)


if __name__ == "__main__":
    unittest.main()
