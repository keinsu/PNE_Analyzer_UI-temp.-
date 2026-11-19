from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from studio.origin_export import _MISSING, paste_origin_to_powerpoint, prepare_curves


class OriginExportTests(unittest.TestCase):
    def setUp(self):
        raw = [dict(source_id=source, cycle=cycle, direction=direction, name=source,
                    color="#3480B1", x=np.array(x), y=np.array([1., float("nan"), 3.]),
                    plot_indices=np.array([0, 2]))
               for source, cycle, direction, x in [("A", 50, "discharge", [4., 3., 2.]),
                                                    ("B", 3, "charge", [1., 2., 3.]),
                                                    ("A", 3, "charge", [1., 2., 3.])]]
        self.curves = prepare_curves(raw, "A")

    def fake_origin(self):
        cols = [SimpleNamespace() for _ in range(4)]
        sheet = Mock(Columns=lambda i: cols[i])
        layer = Mock()
        app = Mock()
        app.WorksheetPages.Add.return_value = SimpleNamespace(Name="Book1", Layers=lambda _: sheet)
        app.GraphPages.Add.return_value = SimpleNamespace(Name="Graph1", Layers=lambda _: layer)
        app.PutWorksheet.return_value = True
        return app, cols, sheet, layer

    def fake_slide(self, *, shape_type=7):
        shape = SimpleNamespace(Type=shape_type, Width=400., Height=200., OLEFormat=SimpleNamespace(ProgID="Origin.Graph"))
        pasted = Mock(Count=1, Item=lambda _: shape)
        slide = Mock()
        slide.Shapes.PasteSpecial.return_value = pasted
        return slide, pasted, shape

    def test_selected_slide_gets_editable_origin_with_exact_xy_data(self):
        app, cols, sheet, layer = self.fake_origin()
        slide, inserted, shape = self.fake_slide()
        with (patch("studio.origin_export._origin_session", return_value=nullcontext(app)),
              patch("studio.origin_export._active_slide", return_value=(slide, "시험.pptx", 3, (960, 540))),
              patch("studio.origin_export._clipboard_state", side_effect=[(1, 1), (2, 2), (2, 2)])):
            destination = paste_origin_to_powerpoint(self.curves, "A", "전압 (V)", "용량 (mAh)",
                                                     Mock(), lambda: False, ((1, 4), (0, 3)))
        self.assertEqual(destination, "시험.pptx · 3번 슬라이드")
        app.CopyPage.assert_called_once_with("Graph1", 4)
        slide.Shapes.PasteSpecial.assert_called_once_with(10)
        self.assertEqual((shape.Left, shape.Top), (280, 170))
        inserted.Delete.assert_not_called()
        self.assertEqual([c.label for c in self.curves], ["Cycle 3 · 충전", "Cycle 50 · 방전"])
        self.assertEqual([col.Type for col in cols], [3, 0, 3, 0])
        self.assertEqual(cols[1].LongName, "Cycle 3 · 충전")
        self.assertEqual(cols[3].LongName, "Cycle 50 · 방전")
        self.assertEqual(sheet.NewDataRange.call_args_list[1].args, (0, 2, 2, 3))
        self.assertEqual(app.PutWorksheet.call_args_list[1].args, ("Book1", [1., _MISSING, 3.], 0, 1))
        self.assertIn("set %C -d 1", layer.Execute.call_args.args[0])
        self.assertIn("layer.x.from=1", layer.Execute.call_args.args[0])

    def test_non_origin_paste_is_removed(self):
        app, _, _, _ = self.fake_origin()
        slide, inserted, _ = self.fake_slide(shape_type=13)
        with (patch("studio.origin_export._origin_session", return_value=nullcontext(app)),
              patch("studio.origin_export._active_slide", return_value=(slide, "시험.pptx", 3, (960, 540))),
              patch("studio.origin_export._clipboard_state", side_effect=[(1, 1), (2, 2), (2, 2)])):
            with self.assertRaisesRegex(RuntimeError, "편집 가능한 Origin"):
                paste_origin_to_powerpoint(self.curves, "A", "X", "Y", Mock(), lambda: False)
        inserted.Delete.assert_called_once()

    def test_changed_clipboard_is_not_pasted(self):
        app, _, _, _ = self.fake_origin()
        slide, _, _ = self.fake_slide()
        with (patch("studio.origin_export._origin_session", return_value=nullcontext(app)),
              patch("studio.origin_export._active_slide", return_value=(slide, "시험.pptx", 3, (960, 540))),
              patch("studio.origin_export._clipboard_state", side_effect=[(1, 1), (2, 2), (3, 3)])):
            with self.assertRaisesRegex(RuntimeError, "다른 복사 작업"):
                paste_origin_to_powerpoint(self.curves, "A", "X", "Y", Mock(), lambda: False)
        slide.Shapes.PasteSpecial.assert_not_called()
