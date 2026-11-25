"""Compare saved captions with current suggestions without editing drafts or publishing."""

import argparse
import json
import sys
from collections import Counter

from sqlalchemy import select

from app.caption_pipeline import DEFAULT_VISION, STOCK, compose_editorial, historical_captions, media_evidence, normalized
from app.caption_writer import style_examples
from app.config import settings
from app.db import SessionLocal
from app.models import Account, ContentAsset, ContentDraft, DmSubmission, InsightCheck, Job


def replay(username, limit):
    config = settings()
    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.username == username))
        if not account:
            raise SystemExit("Account not found")
        history, voice = historical_captions(db, account.id)
        references = style_examples(db, account.id, config.dm_caption_sources)
        drafts = db.scalars(
            select(ContentDraft)
            .where(ContentDraft.account_id == account.id, ContentDraft.discarded_at.is_(None))
            .order_by(ContentDraft.updated_at.desc())
        ).all()
        # Round-robin formats so the most recent imports do not dominate the audit.
        groups = {}
        for draft in drafts:
            groups.setdefault(draft.kind, []).append(draft)
        selected = []
        while len(selected) < limit and any(groups.values()):
            for group in groups.values():
                if group and len(selected) < limit:
                    selected.append(group.pop(0))
        known = {}
        for check in db.scalars(select(InsightCheck).where(
            InsightCheck.account_id == account.id, InsightCheck.kind == "posts"
        )):
            for item in check.items or []:
                if item.get("caption"):
                    known.update({key: item["caption"] for key in (item.get("id"), item.get("url")) if key})
        inputs = []
        for draft in selected:
            job_id = (draft.caption_review or {}).get("job_id")
            job = db.get(Job, job_id) if job_id else None
            submission = db.scalar(select(DmSubmission).where(
                DmSubmission.account_id == account.id, DmSubmission.draft_id == draft.id
            ).limit(1))
            source = (job.payload.get("source_caption") if job else None) or (
                known.get(submission.media_ref, "") if submission else ""
            )
            assets = [db.get(ContentAsset, key) for key in draft.photo_assets or [draft.asset_id]]
            inputs.append((draft.id, draft.kind, draft.caption, source, assets))
        db.expunge_all()
    examples, generated = [], []
    for draft_id, kind, old, source, assets in inputs:
        try:
            notes, printed, speech, audio_unread = media_evidence(kind, assets, config)
            caption, reason = compose_editorial(
                config.dm_editorial_model, source, notes, printed, speech,
                [*history, *generated], voice, references,
            )
            if caption:
                generated.append(caption)
            review = reason or (
                "Review against the video. Audio transcription was unavailable."
                if audio_unread else "Review visual grounding." if not notes else None
            )
        except Exception:
            caption, review, notes = None, "Media or local model unavailable", ""
        examples.append({"draft_id": draft_id, "format": kind, "previous_caption": old,
                         "suggestion": caption, "source_caption_available": bool(source),
                         "vision_available": bool(notes), "review": review})
        print(f"Reviewed {len(examples)} of {len(inputs)}", file=sys.stderr, flush=True)
    counts = Counter(normalized(draft.caption) for draft in drafts if normalized(draft.caption))
    return {
        "saved_drafts": len(drafts), "captioned_drafts": sum(counts.values()),
        "distinct_saved_captions": len(counts), "stock_saved_captions": sum(
            bool(STOCK.search(draft.caption)) for draft in drafts if draft.caption
        ),
        "history_captions": len(history), "owner_voice_examples": len(voice),
        "editorial_model": config.dm_editorial_model,
        "vision_model": config.dm_caption_vision_model or DEFAULT_VISION,
        "new_suggestions": len(generated), "examples": examples,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--limit", type=int, choices=range(1, 11), default=10)
    args = parser.parse_args()
    print(json.dumps(replay(args.account, args.limit), indent=2, ensure_ascii=True))
