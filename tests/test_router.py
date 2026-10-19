"""Grid router: hard rules on hand-built situations."""

import unittest

from schematic.layout.config import Weights
from schematic.layout.router import RNet, RTerminal, Router

RIGHT, DOWN, LEFT, UP = 0, 1, 2, 3


def make(width=20, height=12):
    return Router(width, height, 0, 0, Weights())


def net(r, idx, *terms):
    n = RNet(idx, f"N{idx}", 1.0, False, [RTerminal(f"C{i}", "p", x, y, d) for i, (x, y, d) in enumerate(terms)])
    for t in n.terminals:
        r.add_pin(t.x, t.y, t.d, idx)
    r.nets.append(n)
    return n


def edges_of(r, n):
    return {tuple(sorted((r.xy(a), r.xy(b)))) for a, b in n.edges}


class TestRouter(unittest.TestCase):
    def test_straight_connection(self):
        r = make()
        n = net(r, 0, (2, 5, RIGHT), (10, 5, LEFT))
        r.route_all(1)
        self.assertTrue(n.routed)
        self.assertEqual(len(n.edges), 8)
        self.assertEqual({p[1] for e in edges_of(r, n) for p in e}, {5})

    def test_two_nets_never_share_an_edge_and_cross_cleanly(self):
        r = make()
        a = net(r, 0, (2, 5, RIGHT), (12, 5, LEFT))
        b = net(r, 1, (7, 1, DOWN), (7, 10, UP))
        r.route_all(1)
        self.assertTrue(a.routed and b.routed)
        self.assertFalse(edges_of(r, a) & edges_of(r, b))
        self.assertEqual(r.net_cost(b) > 0, True)
        # the shared node is a straight perpendicular crossing
        self.assertIn(1, [len(r._nets_at(n)) - 1 for n in range(r.W * r.H) if len(r._nets_at(n)) == 2])

    def test_wire_never_enters_a_body(self):
        r = make()
        r.block_rect(6, 2, 8, 8)
        n = net(r, 0, (2, 5, RIGHT), (12, 5, LEFT))
        r.route_all(1)
        self.assertTrue(n.routed)
        for e in edges_of(r, n):
            for x, y in e:
                self.assertFalse(6 <= x <= 8 and 2 <= y <= 8)

    def test_pin_is_left_along_its_direction(self):
        r = make()
        n = net(r, 0, (5, 5, UP), (12, 9, LEFT))
        r.route_all(1)
        self.assertIn(((5, 4), (5, 5)), edges_of(r, n))

    def test_foreign_pin_front_is_not_used(self):
        r = make()
        r.add_pin(7, 4, DOWN, 5)           # pin of another net pointing down at (7, 5)
        n = net(r, 0, (2, 5, RIGHT), (12, 5, LEFT))
        r.route_all(1)
        self.assertTrue(n.routed)
        self.assertNotIn((7, 5), {p for e in edges_of(r, n) for p in e})

    def test_impossible_connection_is_reported(self):
        r = make()
        r.block_rect(0, 0, 19, 4)
        r.block_rect(0, 6, 19, 11)
        r.block_rect(6, 5, 6, 5)           # wall across the only corridor
        n = net(r, 0, (2, 5, RIGHT), (12, 5, LEFT))
        r.route_all(1)
        self.assertFalse(n.routed)
        self.assertTrue(n.failed_terms)

    def test_multi_terminal_net_forms_a_tree(self):
        r = make()
        n = net(r, 0, (2, 5, RIGHT), (12, 2, LEFT), (12, 9, LEFT))
        r.route_all(1)
        self.assertTrue(n.routed)
        deg = {}
        for a, b in n.edges:
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
        self.assertEqual(sum(1 for d in deg.values() if d == 3), 1)   # one T-junction
        self.assertFalse(any(d >= 4 for d in deg.values()))


if __name__ == "__main__":
    unittest.main()
