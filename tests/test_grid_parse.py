"""Reading what a person said: times, durations, windows and days.

All pure — a fixed `today` is passed in, so weekday arithmetic is testable without
waiting for Thursday.

Parsing is tolerant where it is unambiguous (`14:00`, `14`, `1400`, `2pm` are one
time) and refuses where it would have to guess. A calendar that silently books the
wrong hour is worse than one that asks again, so the refusals get as much attention
as the acceptances.
"""

from __future__ import annotations

from datetime import date

import pytest

from jarvis.packs.calendar.grid import GridError, format_duration, format_time
from jarvis.packs.calendar.when import parse_day, parse_duration, parse_time, parse_window

# A Thursday, so weekday arithmetic is unambiguous.
TODAY = date(2026, 9, 24)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("14:00", 840),
        ("00:00", 0),
        ("9:30", 570),
        ("09:30", 570),
        ("14", 840),
        ("1400", 840),
        ("2pm", 840),
        ("2 PM", 840),
        ("12am", 0),
        ("12pm", 720),
        ("24:00", 1440),
        ("11:59pm", 1439),
        ("9.30", 570),
    ],
)
def test_parse_time_accepts_the_ways_a_time_gets_written(text: str, minutes: int) -> None:
    assert parse_time(text) == minutes


@pytest.mark.parametrize("text", ["", "   ", "25:00", "14:75", "half past two", "13pm", "0pm", "abc"])
def test_parse_time_refuses_what_it_cannot_read(text: str) -> None:
    with pytest.raises(GridError):
        parse_time(text)


@pytest.mark.parametrize(("minutes", "text"), [(0, "00:00"), (840, "14:00"), (570, "09:30"), (1440, "24:00")])
def test_format_time(minutes: int, text: str) -> None:
    assert format_time(minutes) == text


@pytest.mark.parametrize(
    ("minutes", "text"),
    [(30, "30m"), (45, "45m"), (60, "1h"), (90, "1h 30m"), (120, "2h"), (1440, "24h")],
)
def test_format_duration(minutes: int, text: str) -> None:
    assert format_duration(minutes) == text


@pytest.mark.parametrize(
    ("text", "minutes"),
    [(90, 90), ("90", 90), ("90m", 90), ("2h", 120), ("1h 30m", 90), ("1h30m", 90), ("2H", 120)],
)
def test_parse_duration(text: str | int, minutes: int) -> None:
    assert parse_duration(text) == minutes


@pytest.mark.parametrize("text", ["", "soon", "2 days", "h"])
def test_parse_duration_refuses(text: str) -> None:
    with pytest.raises(GridError):
        parse_duration(text)


@pytest.mark.parametrize(
    ("text", "span"),
    [
        (None, (0, 1440)),
        ("", (0, 1440)),
        ("all", (0, 1440)),
        ("morning", (360, 720)),
        ("afternoon", (720, 1020)),
        ("evening", (1020, 1320)),
        ("night", (0, 360)),
        ("day", (360, 1320)),
        ("13:00-17:00", (780, 1020)),
        ("13:00 to 17:00", (780, 1020)),
        ("9:00", (540, 600)),
        ("AFTERNOON", (720, 1020)),
    ],
)
def test_parse_window(text: str | None, span: tuple[int, int]) -> None:
    assert parse_window(text) == span


@pytest.mark.parametrize("text", ["17:00-13:00", "13:15-17:00", "afternoonish"])
def test_parse_window_refuses(text: str) -> None:
    with pytest.raises(GridError):
        parse_window(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("today", TODAY),
        ("", TODAY),
        ("tomorrow", date(2026, 9, 25)),
        ("yesterday", date(2026, 9, 23)),
        ("thursday", TODAY),  # today IS Thursday, and that is what a person means
        ("thu", TODAY),
        ("Thursday,", TODAY),
        ("friday", date(2026, 9, 25)),
        ("monday", date(2026, 9, 28)),
        ("next friday", date(2026, 9, 25)),
        ("next thursday", date(2026, 10, 1)),  # "next" means strictly after today
        ("2026-12-01", date(2026, 12, 1)),
    ],
)
def test_parse_day(text: str, expected: date) -> None:
    assert parse_day(text, TODAY) == expected


@pytest.mark.parametrize("text", ["soon", "32nd of Octember", "2026-13-01"])
def test_parse_day_refuses(text: str) -> None:
    with pytest.raises(GridError):
        parse_day(text, TODAY)
