from datetime import timedelta

import pytest
from sqlalchemy import select

from app.content_publication import PublicationCheckFailed, soundtrack_status
from app.dashboard import account_settings
from app.models import (
    AccountSettings,
    BudgetUsage,
    Discovery,
    InsightCheck,
    Job,
    PlaylistTrack,
    Relationship,
    Source,
    Target,
    now,
)
from app.ranked_follows import queue_ranked_cohort
from app.spotify_playlist import playlist_candidates
from app.timing import local_day


def test_audience_compares_only_connected_profile_observations(system):
    client, db, account, _ = system
    account.instagram_id = "42"
    at = now()
    for i, (profile_id, followers) in enumerate([("42", 3), ("42", 5), ("99", 120)]):
        job = Job(account_id=account.id, kind="insight", idempotency_key=f"counts-{i}")
        db.add(job)
        db.flush()
        db.add(
            InsightCheck(
                account_id=account.id,
                job_id=job.id,
                kind="profile",
                target="handle",
                profile={"id": profile_id, "followers": followers, "following": 38},
                observed_at=at + timedelta(minutes=i),
            )
        )
    db.commit()
    data = client.get(f"/api/v1/accounts/{account.id}/audience").json()
    assert (data["profile_followers"], data["profile_following"], data["follower_change"]) == (
        5,
        38,
        2,
    )


def test_soundtrack_is_independent_only_with_matching_receipt():
    snapshot = {"kind": "feed_photo", "caption": "approved", "music_canonical_id": "123"}
    intent = {"snapshot": snapshot, "media_id": "1_2"}
    observed = {"kind": "feed_photo", "caption": "approved", "media_id": "1_2"}
    assert soundtrack_status(intent, observed) == "unverified"
    assert soundtrack_status(intent, observed | {"music_canonical_id": "123"}) == "confirmed"
    for changed in [{"media_id": "2_2"}, {"caption": "different"}, {"music_canonical_id": "456"}]:
        with pytest.raises(PublicationCheckFailed):
            soundtrack_status(intent, observed | changed)


def test_music_library_settings_and_combined_pool(system):
    client, db, account, _ = system
    path = f"/api/v1/accounts/{account.id}/music-library"
    ids = ["a" * 22, "b" * 22]
    entries = [{"playlist_id": key, "label": key[0], "enabled": True} for key in ids]
    version = client.get(path).json()["version"]
    body = {"expected_version": version, "playlists": entries}
    assert client.put(path, json=body).status_code == 200
    assert client.put(path, json=body).status_code == 409
    assert client.put(path, json={**body, "playlists": entries * 2}).status_code == 422
    for key in ids:
        db.add(
            PlaylistTrack(
                playlist_id=key, spotify_id="1" * 22, title="Same song", artist="Same artist"
            )
        )
    db.add(
        PlaylistTrack(playlist_id=ids[1], spotify_id="2" * 22, title="Second song", artist="Artist")
    )
    db.commit()
    assert len(playlist_candidates(db, account.id, ids)) == 2
    config = account_settings(db, account.id)
    version = config.pop("version")
    config.pop("music_library")
    assert (
        client.patch(
            f"/api/v1/accounts/{account.id}/settings", json={**config, "expected_version": version}
        ).status_code
        == 200
    )
    assert [x["playlist_id"] for x in client.get(path).json()["playlists"]] == ids


def test_ranked_trial_spaces_dispatches_and_records_evidence(system):
    _, db, account, rel = system
    account.mode, account.instagram_id, account.session_state = "live", "42", "connected"
    account.live_acquisition_enabled = True
    account.read_only_verified_at = now()
    rel.follows_back = False
    src = Source(
        account_id=account.id, kind="post_likers", value="https://www.instagram.com/p/TEST/"
    )
    db.add(src)
    second = Target(id="other-target", username="other_example")
    db.add(second)
    db.flush()
    db.add_all(
        [
            Discovery(source_id=src.id, target_id=rel.target_id),
            Discovery(source_id=src.id, target_id=second.id),
            Relationship(
                account_id=account.id,
                target_id=second.id,
                observed_state="none",
                follows_back=False,
            ),
        ]
    )
    db.add(
        AccountSettings(
            account_id=account.id, values={"ranked_follow_mode": "likers", "follows_per_day": 2}
        )
    )
    db.flush()
    at = now().replace(hour=6, minute=0, second=0, microsecond=0)
    queue_ranked_cohort(db, account.id, at)
    queue_ranked_cohort(db, account.id, at + timedelta(seconds=30))
    job = db.scalars(select(Job)).one()
    assert job.kind == "follow" and job.payload["liked_posts"] == 1

    # Queue delays shift the following slot. The worker cannot catch up in a burst.
    job.state = "succeeded"
    job.dispatch_started_at = at + timedelta(hours=1)
    db.add(
        BudgetUsage(
            account_id=account.id,
            local_day=local_day(at, account_settings(db, account.id)),
            category="follows",
            consumed=1,
        )
    )
    db.flush()
    queue_ranked_cohort(db, account.id, at + timedelta(hours=12))
    assert db.scalars(select(Job)).one().id == job.id
    queue_ranked_cohort(db, account.id, at + timedelta(hours=13))
    db.flush()
    jobs = db.scalars(select(Job)).all()
    assert {item.target_id for item in jobs} == {rel.target_id, second.id}
    assert len(jobs) == 2
