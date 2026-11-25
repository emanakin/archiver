"""Explicit, idempotent demo data. No network access."""

from sqlalchemy import select

from .db import SessionLocal
from .models import Account, Relationship, Source, Target


def main():
    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.username == "archive_demo"))
        if account:
            print("Demo already exists:", account.id)
            return
        account = Account(username="archive_demo", mode="mock", paused=True)
        db.add(account)
        db.flush()
        for i, name in enumerate(["studio_demo", "runner_demo", "film_demo", "protected_demo"]):
            target = Target(id=f"demo-{i}", username=name)
            db.add(target)
            db.flush()
            db.add(
                Relationship(
                    account_id=account.id,
                    target_id=target.id,
                    observed_state="none",
                    protected=(i == 3),
                )
            )
        db.add(Source(account_id=account.id, kind="profile_followers", value="demo_inspiration"))
        db.commit()
        print("Demo account created:", account.id)


if __name__ == "__main__":
    main()
