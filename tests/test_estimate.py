"""Route-shape estimate used by placement."""

from schematic_txt.layout.estimate import clean, crossings, route, segments, trunk

R, L, U, D = (1, 0), (-1, 0), (0, -1), (0, 1)


def est(a, da, b, db, rects=(), marks=()):
    return route(a, da, b, db, list(rects), 4.0, 0.1, 40.0, None, None, list(marks))


def test_facing_pins_on_one_line_are_straight():
    _cost, bends, hits, pts = est((0, 0), R, (6, 0), L)
    assert (bends, hits, pts) == (0, 0, [(0, 0), (6, 0)])


def test_offset_facing_pins_need_a_z():
    _, bends, _, pts = est((0, 0), R, (6, 2), L)
    assert bends == 2
    assert pts[0] == (0, 0)
    assert pts[-1] == (6, 2)


def test_perpendicular_pins_form_an_l():
    _, bends, _, pts = est((0, 0), R, (5, 4), U)
    assert bends == 1
    assert pts == [(0, 0), (5, 0), (5, 4)]


def test_pins_facing_away_need_four_bends():
    _, bends, _, _ = est((0, 0), L, (6, 3), R)
    assert bends == 4


def test_body_in_the_way_is_avoided_or_priced():
    body = (2, -2, 4, 2)
    _cost, bends, hits, _pts = est((0, 0), R, (6, 0), L, rects=[body])
    assert hits == 0
    assert bends > 0


def test_foreign_pin_fronts_count_as_obstacles():
    # The only Z column x=1 passes the node in front of another pin.
    _, _, hits, _ = est((0, 0), R, (2, 3), L, marks=[(1, 1)])
    assert hits == 1


def test_fold_back_is_rejected():
    assert clean([(0, 0), (4, 0), (2, 0)]) == []
    assert clean([(0, 0), (2, 0), (4, 0), (4, 3)]) == [(0, 0), (4, 0), (4, 3)]


def test_proper_crossing():
    a = segments([(0, 2), (6, 2)])
    b = segments([(3, 0), (3, 5)])
    assert crossings(*a, *b) == (1, 0, 0)


def test_contacts_are_conflicts():
    # a wire ending on another net, and two nets on one line
    a = segments([(0, 2), (6, 2)])
    b = segments([(3, 2), (3, 5)])
    assert crossings(*a, *b) == (0, 1, 0)
    c = segments([(4, 2), (9, 2)])
    assert crossings(*a, *c) == (0, 1, 0)


def test_side_by_side_steps():
    a = segments([(0, 2), (6, 2)])
    b = segments([(2, 3), (9, 3)])
    assert crossings(*a, *b) == (0, 0, 4)


def test_bus_column_uses_one_straight_trunk():
    # three pins facing right, stacked: one trunk, straight taps
    pts = [(0, 0), (0, 5), (0, 10)]
    dirs = [R, R, R]
    _cost, bends, hits, polys = trunk(pts, dirs, [], 4.0, 0.1, 40.0, 0.5, [None] * 3)
    assert hits == 0
    assert bends == 2  # the trunk ends turn into the outer taps
    assert [(2, 0), (2, 10)] in polys
