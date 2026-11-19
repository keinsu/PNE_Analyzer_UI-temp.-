import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from studio.project import save_project, load_project, default_output_dir


class ProjectTest(unittest.TestCase):
    def test_output_folder_permission_fallback_is_bounded(self):
        with patch('studio.project.Path.mkdir'), patch('studio.project.os.open', side_effect=PermissionError('denied')) as probe:
            self.assertEqual(default_output_dir(), str(Path.cwd()))
            self.assertLessEqual(probe.call_count, 3)

    def test_state_round_trip_and_source_identity(self):
        channel = SimpleNamespace(id="trial/channel-1", name="한글 셀", source_path="D:/시험/원본.zip",
                                  metadata={"sha256": "a"*64}, warnings=[])
        state = dict(selected_channels=[channel.id], baseline=5, dcr_manual={"physical-pulse-1": {"soc": 50., "include": False}},
                     range=[np.float64(3.2), 4.2], nonfinite=np.nan, selected_cycles="1,5,10-50:5")
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "한글 프로젝트.bas.json"
            save_project(str(path), [channel], state)
            actual = load_project(str(path))
            self.assertEqual(actual["sources"][0]["sha256"], "a"*64)
            self.assertEqual(actual["sources"][0]["name"], "한글 셀")
            self.assertEqual(actual["ui_state"]["dcr_manual"], state["dcr_manual"])
            self.assertIsNone(actual["ui_state"]["nonfinite"])
            self.assertEqual(actual["ui_state"]["range"], [3.2, 4.2])
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_reject_unsupported_version(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps(dict(schema_version=99)), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_project(str(path))


if __name__ == "__main__":
    unittest.main()
