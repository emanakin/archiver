from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .schemas import Intent, SourceCreate, Strict

InsightKind = Literal[
    "profile", "relationship", "followers", "following", "posts", "stories", "likers"
]


class InsightCreate(Intent):
    account_id: str
    kind: InsightKind
    target: str = Field(min_length=1, max_length=512)
    requested_limit: int = Field(default=5, ge=1, le=20)
    cursor: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def normalize(self):
        self.target = SourceCreate(
            account_id=self.account_id,
            kind="post_likers" if self.kind == "likers" else "profile_followers",
            value=self.target,
        ).value
        if self.cursor and self.kind not in {"followers", "following", "posts"}:
            raise ValueError("This observation does not support pagination")
        return self


class InsightProfile(Strict):
    id: str
    username: str
    full_name: str | None = None
    followers: int | None = Field(default=None, ge=0)
    following: int | None = Field(default=None, ge=0)
    posts: int | None = Field(default=None, ge=0)
    is_private: bool | None = None


class InsightItem(Strict):
    id: str
    kind: Literal["user", "post", "story"]
    username: str | None = None
    caption: str | None = None
    url: str | None = None
    media_type: str | None = None
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    posted_at: datetime | None = None


class InsightObservation(Strict):
    profile: InsightProfile | None = None
    relationship: "InsightRelationship | None" = None
    items: list[InsightItem] = Field(default_factory=list, max_length=20)
    next_cursor: str | None = Field(default=None, max_length=200)
    complete: bool | None = None
    note: str = Field(max_length=500)


class InsightRelationship(Strict):
    target_id: str
    state: Literal["none", "following", "requested", "unknown"]
    follows_back: bool | None = None


class InsightResponse(InsightObservation):
    id: str
    account_id: str
    job_id: str
    kind: InsightKind
    target: str
    requested_limit: int
    cursor: str | None
    state: str
    error_code: str | None
    requests_used: int
    observed_at: datetime | None
    created_at: datetime
