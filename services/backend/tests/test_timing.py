from datetime import UTC, datetime

from app.schemas import SettingsValues
from app.timing import local_day, next_window, within_window


def test_toronto_spring_gap_moves_to_next_valid_minute():
    config = SettingsValues(window_start="02:30", window_end="04:00").model_dump()
    before = datetime(2026, 3, 8, 6, 59, tzinfo=UTC)
    assert next_window(before, config) == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)


def test_toronto_fall_repeat_uses_utc_instants_and_calendar_day():
    config = SettingsValues(window_start="01:00", window_end="02:00").model_dump()
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    second = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    assert within_window(first, config) and within_window(second, config)
    assert local_day(first, config) == local_day(second, config) == "2026-11-01"


def test_overnight_window_and_next_local_calendar_day():
    config = SettingsValues(window_start="22:00", window_end="02:00").model_dump()
    instant = datetime(2026, 11, 16, 4, 30, tzinfo=UTC)
    assert within_window(instant, config)
    following = next_window(instant, config, next_day=True)
    assert local_day(following, config) != local_day(instant, config)
