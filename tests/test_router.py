"""Grid router: hard rules on hand-built situations."""

from schematic_txt.layout.config import Weights
from schematic_txt.layout.router import RNet, Router, RTerminal

RIGHT, DOWN, LEFT, UP = 0, 1, 2, 3


def make(width=20, height=12):
    return Router(width, height, 0, 0, Weights())


def net(r, idx, *terms):
    n = RNet(
        idx, f"N{idx}", 1.0, False, [RTerminal(f"C{i}", "p", x, y, d) for i, (x, y, d) in enumerate(terms)]
    )
    for t in n.terminals:
        r.add_pin(t.x, t.y, t.d, idx)
    r.nets.append(n)
    return n


def edges_of(r, n):
    return {tuple(sorted((r.xy(a), r.xy(b)))) for a, b in n.edges}


def test_straight_connection():
    r = make()
    n = net(r, 0, (2, 5, RIGHT), (10, 5, LEFT))
    r.route_all(1)
    assert n.routed
    assert len(n.edges) == 8
    assert {p[1] for e in edges_of(r, n) for p in e} == {5}


def test_two_nets_never_share_an_edge_and_cross_cleanly():
    r = make()
    a = net(r, 0, (2, 5, RIGHT), (12, 5, LEFT))
    b = net(r, 1, (7, 1, DOWN), (7, 10, UP))
    r.route_all(1)
    assert a.routed and b.routed
    assert not edges_of(r, a) & edges_of(r, b)
    assert r.net_cost(b) > 0
    # the shared node is a straight perpendicular crossing
    assert 1 in [len(r._nets_at(n)) - 1 for n in range(r.W * r.H) if len(r._nets_at(n)) == 2]


def test_wire_never_enters_a_body():
    r = make()
    r.block_rect(6, 2, 8, 8)
    n = net(r, 0, (2, 5, RIGHT), (12, 5, LEFT))
    r.route_all(1)
    assert n.routed
    for e in edges_of(r, n):
        for x, y in e:
            assert not (6 <= x <= 8 and 2 <= y <= 8)


def test_pin_is_left_along_its_direction():
    r = make()
    n = net(r, 0, (5, 5, UP), (12, 9, LEFT))
    r.route_all(1)
    assert ((5, 4), (5, 5)) in edges_of(r, n)


def test_foreign_pin_front_is_not_used():
    r = make()
    r.add_pin(7, 4, DOWN, 5)  # pin of another net pointing down at (7, 5)
    n = net(r, 0, (2, 5, RIGHT), (12, 5, LEFT))
    r.route_all(1)
    assert n.routed
    assert (7, 5) not in {p for e in edges_of(r, n) for p in e}


def test_impossible_connection_is_reported():
    r = make()
    r.block_rect(0, 0, 19, 4)
    r.block_rect(0, 6, 19, 11)
    r.block_rect(6, 5, 6, 5)  # wall across the only corridor
    n = net(r, 0, (2, 5, RIGHT), (12, 5, LEFT))
    r.route_all(1)
    assert not n.routed
    assert n.failed_terms


def test_multi_terminal_net_forms_a_tree():
    r = make()
    n = net(r, 0, (2, 5, RIGHT), (12, 2, LEFT), (12, 9, LEFT))
    r.route_all(1)
    assert n.routed
    deg = {}
    for a, b in n.edges:
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    assert sum(1 for d in deg.values() if d == 3) == 1  # one T-junction
    assert not any(d >= 4 for d in deg.values())
