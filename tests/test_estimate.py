"""Route-shape estimate used by placement."""

import unittest

from schematic.layout.estimate import clean, crossings, route, segments, trunk

R, L, U, D = (1, 0), (-1, 0), (0, -1), (0, 1)


def est(a, da, b, db, rects=(), marks=()):
    return route(a, da, b, db, list(rects), 4.0, 0.1, 40.0, None, None, list(marks))


class TestRouteShapes(unittest.TestCase):
    def test_facing_pins_on_one_line_are_straight(self):
        cost, bends, hits, pts = est((0, 0), R, (6, 0), L)
        self.assertEqual((bends, hits, pts), (0, 0, [(0, 0), (6, 0)]))

    def test_offset_facing_pins_need_a_z(self):
        _, bends, _, pts = est((0, 0), R, (6, 2), L)
        self.assertEqual(bends, 2)
        self.assertEqual(pts[0], (0, 0))
        self.assertEqual(pts[-1], (6, 2))

    def test_perpendicular_pins_form_an_l(self):
        _, bends, _, pts = est((0, 0), R, (5, 4), U)
        self.assertEqual(bends, 1)
        self.assertEqual(pts, [(0, 0), (5, 0), (5, 4)])

    def test_pins_facing_away_need_four_bends(self):
        _, bends, _, _ = est((0, 0), L, (6, 3), R)
        self.assertEqual(bends, 4)

    def test_body_in_the_way_is_avoided_or_priced(self):
        body = (2, -2, 4, 2)
        cost, bends, hits, pts = est((0, 0), R, (6, 0), L, rects=[body])
        self.assertEqual(hits, 0)
        self.assertGreater(bends, 0)

    def test_foreign_pin_fronts_count_as_obstacles(self):
        # The only Z column x=1 passes the node in front of another pin.
        _, _, hits, _ = est((0, 0), R, (2, 3), L, marks=[(1, 1)])
        self.assertEqual(hits, 1)

    def test_fold_back_is_rejected(self):
        self.assertEqual(clean([(0, 0), (4, 0), (2, 0)]), [])
        self.assertEqual(clean([(0, 0), (2, 0), (4, 0), (4, 3)]), [(0, 0), (4, 0), (4, 3)])


class TestCrossings(unittest.TestCase):
    def test_proper_crossing(self):
        a = segments([(0, 2), (6, 2)])
        b = segments([(3, 0), (3, 5)])
        self.assertEqual(crossings(*a, *b), (1, 0, 0))

    def test_contacts_are_conflicts(self):
        # a wire ending on another net, and two nets on one line
        a = segments([(0, 2), (6, 2)])
        b = segments([(3, 2), (3, 5)])
        self.assertEqual(crossings(*a, *b), (0, 1, 0))
        c = segments([(4, 2), (9, 2)])
        self.assertEqual(crossings(*a, *c), (0, 1, 0))

    def test_side_by_side_steps(self):
        a = segments([(0, 2), (6, 2)])
        b = segments([(2, 3), (9, 3)])
        self.assertEqual(crossings(*a, *b), (0, 0, 4))


class TestTrunk(unittest.TestCase):
    def test_bus_column_uses_one_straight_trunk(self):
        # three pins facing right, stacked: one trunk, straight taps
        pts = [(0, 0), (0, 5), (0, 10)]
        dirs = [R, R, R]
        cost, bends, hits, polys = trunk(pts, dirs, [], 4.0, 0.1, 40.0, 0.5, [None] * 3)
        self.assertEqual(hits, 0)
        self.assertEqual(bends, 2)      # the trunk ends turn into the outer taps
        self.assertIn([(2, 0), (2, 10)], polys)


if __name__ == "__main__":
    unittest.main()
