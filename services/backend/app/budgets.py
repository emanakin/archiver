"""Budget updates require the caller's locked Account row and short transaction."""

from datetime import timedelta

from sqlalchemy import select

from .dashboard import account_settings
from .execution_errors import BudgetDayChanged, BudgetExhausted
from .models import BudgetReservation, BudgetUsage
from .timing import local_day

LIMITS = {
    "requests": "request_ceiling",
    "follows": "follows_per_day",
    "unfollows": "unfollows_per_day",
}


def reserve(db, account_id, attempt_id, category, units, at, *, extend=False):
    config = account_settings(db, account_id)
    day = local_day(at, config)
    usage = db.get(BudgetUsage, (account_id, day, category))
    if usage is None:
        usage = BudgetUsage(
            account_id=account_id, local_day=day, category=category, consumed=0, reserved=0
        )
        db.add(usage)
    ceiling = config[LIMITS[category]]
    if ceiling is not None and usage.consumed + usage.reserved + units > ceiling:
        raise BudgetExhausted(f"Configured {category} allowance exhausted")
    if extend:
        reservation = db.scalar(
            select(BudgetReservation)
            .where(
                BudgetReservation.account_id == account_id,
                BudgetReservation.attempt_id == attempt_id,
                BudgetReservation.category == category,
                BudgetReservation.released.is_(False),
            )
            .with_for_update()
        )
        if not reservation or reservation.local_day != day:
            raise BudgetDayChanged("Local budget calendar day changed")
        usage.reserved += units
        reservation.remaining += units
        reservation.expires_at = at + timedelta(minutes=2)
        return reservation
    usage.reserved += units
    reservation = BudgetReservation(
        account_id=account_id,
        attempt_id=attempt_id,
        local_day=day,
        category=category,
        remaining=units,
        expires_at=at + timedelta(minutes=2),
    )
    db.add(reservation)
    db.flush()
    return reservation


def consume(db, account_id, attempt_id, category, at):
    reservation = db.scalar(
        select(BudgetReservation)
        .where(
            BudgetReservation.attempt_id == attempt_id,
            BudgetReservation.category == category,
            BudgetReservation.released.is_(False),
        )
        .with_for_update()
    )
    config = account_settings(db, account_id)
    if reservation is not None and reservation.local_day != local_day(at, config):
        raise BudgetDayChanged("Local budget calendar day changed")
    if reservation is None or reservation.remaining < 1:
        raise BudgetExhausted("Operation request reservation exhausted")
    usage = db.get(BudgetUsage, (account_id, reservation.local_day, category))
    # A reduced owner allowance must take effect before the next outgoing request.
    ceiling = config[LIMITS[category]]
    if ceiling is not None and usage.consumed + 1 > ceiling:
        raise BudgetExhausted(f"Configured {category} allowance exhausted")
    usage.reserved -= 1
    usage.consumed += 1
    reservation.remaining -= 1
    reservation.expires_at = at + timedelta(minutes=2)


def release(db, account_id, attempt_id):
    for reservation in db.scalars(
        select(BudgetReservation)
        .where(
            BudgetReservation.account_id == account_id,
            BudgetReservation.attempt_id == attempt_id,
            BudgetReservation.released.is_(False),
        )
        .with_for_update()
    ):
        usage = db.get(BudgetUsage, (account_id, reservation.local_day, reservation.category))
        usage.reserved -= reservation.remaining
        reservation.remaining = 0
        reservation.released = True
