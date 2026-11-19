import unittest

import numpy as np

from backend.dqdv import compact_voltage_curve


class AdaptiveDerivativeTests(unittest.TestCase):
    def test_linear_curve_uses_only_base_grid(self):
        voltage = np.linspace(3.0, 4.0, 20_001)
        knots, capacity = compact_voltage_curve(voltage, 2 * voltage, 1000)
        self.assertEqual(len(knots), 1001)
        np.testing.assert_allclose(capacity, 2 * knots)

    def test_curvature_retains_measured_points(self):
        voltage = np.linspace(3.0, 3.010, 101)
        capacity = np.tanh((voltage - 3.0054) / 0.001)
        knots, charges = compact_voltage_curve(voltage, capacity, 10)
        self.assertGreater(len(knots), 11)
        self.assertTrue(np.all(np.diff(knots) > 0))
        self.assertLessEqual(np.max(np.abs(np.interp(voltage, knots, charges) - capacity)),
                             np.ptp(capacity) * 1e-4)
