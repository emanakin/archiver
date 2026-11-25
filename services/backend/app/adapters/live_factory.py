from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..execution_errors import SessionRequired
from ..models import Account, AccountSession
from ..session_store import SessionStore
from .guarded_client import build_client
from .instagram import LiveInstagramAdapter


class GuardBinding:
    def __init__(self, current):
        self.current = current

    @property
    def fatal_error(self):
        return self.current.fatal_error

    @fatal_error.setter
    def fatal_error(self, value):
        self.current.fatal_error = value

    @property
    def allow_auth(self):
        return False

    @property
    def allow_media(self):
        return getattr(self.current, "allow_media", False)

    @property
    def allow_dm_receipt(self):
        return getattr(self.current, "allow_dm_receipt", False)

    @property
    def music_queries(self):
        return getattr(self.current, "music_queries", ())

    @property
    def approved_music(self):
        return getattr(self.current, "approved_music", None)

    @property
    def feed_music(self):
        return getattr(self.current, "feed_music", None)

    def before_request(self, **metadata):
        return self.current.before_request(**metadata)


class LiveAdapterFactory:
    def __init__(self, engine):
        self.engine = engine
        self.store = SessionStore(settings().session_store_path, settings().session_key_file)
        self.clients = {}

    def __call__(self, guard):
        account_id = guard.lane.account_id
        with Session(self.engine) as db:
            metadata = db.get(AccountSession, account_id)
            if metadata is None:
                raise SessionRequired("Account has no worker session")
            revision = metadata.revision
        cached = self.clients.get(account_id)
        if cached and cached[0] == revision:
            _, client, binding = cached
            binding.current = guard
        else:
            if cached:
                for name in ("private", "public", "graphql"):
                    session = getattr(cached[1], name, None)
                    if session:
                        session.close()
            binding = GuardBinding(guard)
            client = build_client(binding, self.store.load(account_id, revision))
            self.clients[account_id] = (revision, client, binding)

        def persist():
            guard.lane.assert_owned()
            updated = self.store.save(account_id, client.get_settings())
            with Session(self.engine) as db, db.begin():
                db.scalar(select(Account).where(Account.id == account_id).with_for_update())
                metadata = db.get(AccountSession, account_id)
                metadata.revision = updated
            self.clients[account_id] = (updated, client, binding)

        return LiveInstagramAdapter(client, persist)
