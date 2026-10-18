"""Tests for the routing estimates that drive placement."""

import unittest

from schematic.models import Side
from schematic.layout.estimate import count_crossings, polyline_segments, route_shape


class TestRouteShapes(unittest.TestCase):
    def test_straight(self):
        bends, pts = route_shape((0, 0), Side.RIGHT, (200, 0), Side.LEFT)
        self.assertEqual(bends, 0)
        self.assertEqual(pts, [(0, 0), (200, 0)])

    def test_z_shape_jogs_between_escape_points(self):
        bends, pts = route_shape((0, 0), Side.RIGHT, (200, 60), Side.LEFT)
        self.assertEqual(bends, 2)
        jog_x = pts[1][0]
        self.assertTrue(20 <= jog_x <= 180, jog_x)

    def test_cramped_facing_pins_need_an_s_shape(self):
        # Escape points cross over: no column left for a jog.
        bends, _ = route_shape((0, 0), Side.RIGHT, (20, 100), Side.LEFT)
        self.assertEqual(bends, 4)

    def test_l_shape(self):
        bends, pts = route_shape((0, 0), Side.RIGHT, (100, 100), Side.TOP)
        self.assertEqual(bends, 1)
        self.assertEqual(pts[1], (100, 0))

    def test_u_shape(self):
        bends, pts = route_shape((0, 0), Side.RIGHT, (0, 100), Side.RIGHT)
        self.assertEqual(bends, 2)
        self.assertGreater(pts[1][0], 0)

    def test_pin_behind(self):
        bends, _ = route_shape((100, 0), Side.RIGHT, (0, 200), Side.LEFT)
        self.assertEqual(bends, 4)


class TestEstimatedCrossings(unittest.TestCase):
    def test_proper_crossing(self):
        a = polyline_segments([(0, 50), (100, 50)])
        b = polyline_segments([(50, 0), (50, 100)])
        self.assertEqual(count_crossings(a, b), 1)

    def test_shared_corridor_staircase_needs_no_crossing(self):
        # Two Z routes jogging in the same column with consistent order: the
        # router nests them as a staircase.
        a = polyline_segments([(0, 0), (100, 0), (100, 100), (200, 100)])
        b = polyline_segments([(0, 20), (100, 20), (100, 120), (200, 120)])
        self.assertEqual(count_crossings(a, b), 0)

    def test_shared_corridor_inversion_forces_a_crossing(self):
        a = polyline_segments([(0, 0), (100, 0), (100, 120), (200, 120)])
        b = polyline_segments([(0, 20), (100, 20), (100, 100), (200, 100)])
        self.assertEqual(count_crossings(a, b), 1)


if __name__ == "__main__":
    unittest.main()
