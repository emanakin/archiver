from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def local_day(at, config):
    return at.astimezone(ZoneInfo(config["timezone"])).date().isoformat()


def within_window(at, config):
    local = at.astimezone(ZoneInfo(config["timezone"]))
    minute = local.hour * 60 + local.minute
    start, end = (
        int(value[:2]) * 60 + int(value[3:])
        for value in (config["window_start"], config["window_end"])
    )
    return start == end or (
        start <= minute < end if start < end else minute >= start or minute < end
    )


def next_window(at, config, next_day=False):
    # Walk UTC instants: skipped local minutes naturally advance and repeated
    # local minutes cannot duplicate a deterministically keyed job.
    first_day = local_day(at, config)
    candidate = at.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(60 * 72):
        if within_window(candidate, config) and (
            not next_day or local_day(candidate, config) != first_day
        ):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError("No operating window found within 72 hours")


def scheduled_instant(day, clock_time, timezone_name):
    zone = ZoneInfo(timezone_name)
    wall = datetime.combine(day, time.fromisoformat(clock_time))
    # Earliest fold wins; missing local minutes advance to the next valid one.
    for _ in range(181):
        candidate = wall.replace(tzinfo=zone, fold=0).astimezone(UTC)
        if candidate.astimezone(zone).replace(tzinfo=None) == wall:
            return candidate
        wall += timedelta(minutes=1)
    raise ValueError("Scheduled local time is unavailable")
