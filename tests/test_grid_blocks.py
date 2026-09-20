"""Block arithmetic: what fits, what collides, and where the gaps are.

Every one of these is a property a booking depends on. The interesting cases are all
boundaries — a block ending exactly when another starts, a gap that is one slot too
short, a run that touches midnight — because that is where an off-by-one becomes a
double booking.
"""

from __future__ import annotations

import pytest

from jarvis.packs.calendar import grid
from jarvis.packs.calendar.grid import GridError
from jarvis.packs.calendar.when import parse_time


@pytest.mark.parametrize(
    ("start", "minutes", "span"),
    [(840, 120, (840, 960)), (840, 30, (840, 870)), (0, 1440, (0, 1440)), (1320, 120, (1320, 1440))],
)
def test_parse_block_accepts_aligned_blocks(start: int, minutes: int, span: tuple[int, int]) -> None:
    assert grid.parse_block(start, minutes) == span


@pytest.mark.parametrize(
    ("start", "minutes", "fragment"),
    [
        (855, 60, "not on a 30-minute boundary"),
        (840, 15, "at least 30 minutes"),
        (840, 45, "multiple of 30"),
        (1380, 120, "after midnight"),
    ],
)
def test_a_refusal_says_what_to_do_instead(start: int, minutes: int, fragment: str) -> None:
    with pytest.raises(GridError, match=fragment):
        grid.parse_block(start, minutes)


def test_an_off_grid_time_is_told_its_neighbours() -> None:
    """A refusal has to be actionable, not merely correct."""
    with pytest.raises(GridError) as caught:
        grid.parse_block(parse_time("14:15"), 60)

    assert "14:00" in str(caught.value)
    assert "14:30" in str(caught.value)


# -- free runs -----------------------------------------------------------------


def test_an_empty_day_is_one_whole_run() -> None:
    assert grid.free_runs(set()) == [(0, 48)]


def test_a_fully_booked_day_has_no_run() -> None:
    assert grid.free_runs(set(range(48))) == []


def test_runs_do_not_merge_across_a_booking() -> None:
    assert grid.free_runs({20, 21}) == [(0, 20), (22, 48)]


def test_a_run_that_starts_at_midnight_closes_correctly() -> None:
    assert grid.free_runs({0}) == [(1, 48)]


def test_a_run_that_ends_at_midnight_closes_correctly() -> None:
    assert grid.free_runs({47}) == [(0, 47)]


# -- availability --------------------------------------------------------------


def test_windows_respect_both_the_window_and_the_minimum() -> None:
    busy = {24, 25, 26, 27}  # 12:00-14:00
    assert grid.available_windows(busy, (720, 1020), 60) == [(840, 1020)]  # 14:00-17:00


def test_a_gap_shorter_than_the_minimum_is_not_offered() -> None:
    busy = {20, 22}  # leaves one 30-minute slot free in the middle
    assert grid.available_windows(busy, (0, 1440), 60) == [(0, 600), (690, 1440)]


def test_a_fully_booked_window_yields_nothing() -> None:
    assert grid.available_windows(set(range(48)), (720, 1020), 30) == []


def test_the_minimum_must_be_on_the_grid() -> None:
    with pytest.raises(GridError):
        grid.available_windows(set(), (0, 1440), 45)


# -- collisions ----------------------------------------------------------------


def test_overlaps_names_the_slots_it_would_hit() -> None:
    busy = {28, 29, 30, 31}  # 14:00-16:00 booked
    assert grid.overlaps(busy, 840, 960) == [28, 29, 30, 31]  # exactly on top of it
    assert grid.overlaps(busy, 900, 1020) == [30, 31]  # straddling the end
    assert grid.overlaps(busy, 780, 900) == [28, 29]  # straddling the start


def test_adjacent_blocks_do_not_overlap() -> None:
    """Ending exactly when another begins is a schedule, not a collision."""
    busy = {28, 29}  # 14:00-15:00 booked
    assert grid.overlaps(busy, 900, 960) == []  # 15:00-16:00, right after
    assert grid.overlaps(busy, 780, 840) == []  # 13:00-14:00, right before
    assert grid.overlaps(busy, 960, 1020) == []  # 16:00-17:00, well clear
    assert grid.overlaps(busy, 840, 900) == [28, 29]  # exactly on top of it


def test_overlaps_on_a_clean_day_finds_nothing() -> None:
    assert grid.overlaps(set(), 0, 1440) == []
