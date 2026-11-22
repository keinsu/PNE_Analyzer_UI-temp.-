import unittest
from unittest.mock import MagicMock

import pandas as pd

from studio.measurements import onset_resistances, rpt_pulses, dcir_changes
from studio.origin_export import _build_graph, prepare_summary_curves


class RPTMeasurementTests(unittest.TestCase):
    def test_starting_voltage_and_stable_current_at_1_and_10_seconds(self):
        frame = pd.DataFrame(dict(StepTime_s=range(11), Voltage_V=[4.0] + [3.98] * 9 + [3.95],
                                  Current_mA=[0.] + [-1000.] * 10))
        result = onset_resistances(frame, [1, 10])
        self.assertAlmostEqual(result[1], 20)
        self.assertAlmostEqual(result[10], 50)

    def test_repeated_step_is_ordered_and_incomplete_soc_is_never_shifted(self):
        frame = pd.DataFrame([
            dict(Cycle=1, _row=i * 11 + t, StepNo="12", PhysicalStepID=f"pulse-{i}",
                 StepTime_s=t, TypeStd="REST" if t == 0 else "DISCHARGE",
                 Current_mA=0 if t == 0 else -1000, Voltage_V=4.0 - .002 * t * (i + 1))
            for i in range(2) for t in range(11)])
        rows = rpt_pulses(frame, "12", [80, 50])
        self.assertEqual([entry["SOC_pct"] for entry in rows], [80, 50])
        self.assertAlmostEqual(rows[1]["Value"], 40)
        self.assertEqual([row["SOC_pct"] for row in rpt_pulses(frame, "12", [50])], [None, None])

    def test_measured_and_manual_baseline(self):
        points = [dict(Cycle=1, Value=20.), dict(Cycle=100, Value=25.)]
        self.assertEqual(dcir_changes(points, 12), {1: 0., 100: 25.})
        self.assertEqual(dcir_changes(points[1:], 20.), {1: 0., 100: 25.})
        self.assertEqual(dcir_changes(points[1:]), {})

    def test_summary_origin_routes_dcir_to_second_layer(self):
        curves = prepare_summary_curves([dict(name="Retention", x=[1, 2], y=[100, 99]),
                                         dict(name="DCIR", x=[1], y=[0], right_axis=True)])
        app = MagicMock()
        graph = app.GraphPages.Add.return_value
        left, right = MagicMock(), MagicMock()
        graph.Layers.side_effect = [left, right]
        _build_graph(app, curves, "Summary", "Cycle", "Retention", lambda *args: None,
                     lambda: False, (), right_label="DCIR")
        app.GraphPages.Add.assert_called_once_with("DoubleY")
        self.assertEqual(left.DataPlots.Add.call_args.args[1], 200)
        self.assertEqual(right.DataPlots.Add.call_args.args[1], 201)
        self.assertIn('label -yr "DCIR"', right.Execute.call_args.args[0])
