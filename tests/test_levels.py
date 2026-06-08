"""Unit tests for the category-levels percentile core.

No DB needed — these exercise the pure ranking/classification helpers that
the player page and My Team matrix depend on. The midrank + inversion logic
is exactly the kind of thing that breaks silently, so it's pinned here.

Run directly (no pytest required):

    .venv/bin/python tests/test_levels.py

or under pytest if installed:

    .venv/bin/python -m pytest tests/test_levels.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fantasybb.queries import _percentile, _tier, pitcher_role  # noqa: E402


def test_percentile_midrank():
    # 3 sits dead center of 1..5 -> midrank 50th.
    assert _percentile([1, 2, 3, 4, 5], 3, lower_better=False) == 50
    # Top and bottom of the distribution.
    assert _percentile([1, 2, 3, 4, 5], 5, lower_better=False) == 90
    assert _percentile([1, 2, 3, 4, 5], 1, lower_better=False) == 10


def test_percentile_ties_share_midpoint():
    # Two 10s and one 20: a 10 ranks at (0 below + 1.0 half-of-equals)/3 = 33rd.
    assert _percentile([10, 10, 20], 10, lower_better=False) == 33


def test_percentile_lower_is_better_inverts():
    # For ERA/WHIP the smallest value should be elite (highest percentile).
    assert _percentile([2.0, 3.0, 4.0], 2.0, lower_better=True) == 83
    assert _percentile([2.0, 3.0, 4.0], 4.0, lower_better=True) == 17


def test_percentile_handles_empty_and_none():
    assert _percentile([], 5, lower_better=False) is None
    assert _percentile([1, 2, 3], None, lower_better=False) is None


def test_percentile_value_outside_pool():
    # A value above every pool member is 100th; below every member is 0th.
    assert _percentile([10, 20, 30], 99, lower_better=False) == 100
    assert _percentile([10, 20, 30], 1, lower_better=False) == 0


def test_tier_thresholds():
    assert _tier(95)[0] == "Elite"
    assert _tier(90)[0] == "Elite"
    assert _tier(70)[0] == "Above avg"
    assert _tier(45)[0] == "Average"
    assert _tier(25)[0] == "Below avg"
    assert _tier(10)[0] == "Poor"
    assert _tier(None) == ("—", "na")


def test_pitcher_role():
    assert pitcher_role(30, 28) == "SP"      # mostly starts
    assert pitcher_role(65, 0) == "RP"       # pure reliever
    assert pitcher_role(30, 10) == "RP"      # swingman, mostly relief
    assert pitcher_role(0, 0) == "RP"        # no appearances -> RP
    assert pitcher_role(None, None) == "RP"  # null-safe


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")
