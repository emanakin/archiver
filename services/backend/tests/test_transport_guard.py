import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from app.adapters.guarded_client import attach_transport_guard
from app.execution_errors import OperationBlocked, Restriction, UncertainWrite


class Guard:
    fatal_error = None
    allow_auth = False

    def __init__(self):
        self.calls = []

    def before_request(self, **metadata):
        self.calls.append(metadata)


def test_actual_transport_attempt_has_timeout_no_redirect_and_no_retry():
    guard = Guard()
    sends = []

    def original(request, **kwargs):
        sends.append(kwargs)
        raise TimeoutError("synthetic request timeout")

    session = SimpleNamespace(send=original)
    attach_transport_guard(session, guard)
    request = SimpleNamespace(
        url="https://i.instagram.com/api/v1/friendships/create/123/", method="POST"
    )
    with pytest.raises(UncertainWrite):
        session.send(request)
    with pytest.raises(UncertainWrite):
        session.send(request)
    assert len(sends) == len(guard.calls) == 1
    assert sends[0]["timeout"] == (10, 25) and sends[0]["allow_redirects"] is False
    assert guard.calls[0] == {"mutation": True, "operation": "follow", "target_id": "123"}


def test_restriction_is_sticky_and_prevents_helper_fallback():
    guard = Guard()
    calls = []

    def original(request, **kwargs):
        calls.append(request.url)
        return SimpleNamespace(status_code=429, json=lambda: {"message": "feedback_required"})

    session = SimpleNamespace(send=original)
    attach_transport_guard(session, guard)
    read = SimpleNamespace(url="https://i.instagram.com/api/v1/friendships/show/123/", method="GET")
    with pytest.raises(Restriction):
        session.send(read)
    write = SimpleNamespace(
        url="https://i.instagram.com/api/v1/friendships/create/123/", method="POST"
    )
    with pytest.raises(Restriction):
        session.send(write)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url,method",
    [
        ("https://example.com/api/v1/friendships/show/123/", "GET"),
        ("https://i.instagram.com/api/v1/accounts/edit_profile/", "POST"),
    ],
)
def test_unapproved_destinations_and_post_capabilities_never_dispatch(url, method):
    guard = Guard()
    session = SimpleNamespace(send=lambda *args, **kwargs: pytest.fail("unapproved network call"))
    attach_transport_guard(session, guard)
    with pytest.raises(OperationBlocked):
        session.send(SimpleNamespace(url=url, method=method))
    assert guard.calls == []


@pytest.mark.parametrize(
    "allow_auth,document,allowed",
    [
        (True, "124930351917786857261002920888", True),
        (False, "124930351917786857261002920888", False),
        (True, "another-document", False),
    ],
)
def test_device_registration_is_exact_document_and_owner_auth_only(allow_auth, document, allowed):
    guard = Guard()
    guard.allow_auth = allow_auth
    sends = []

    def original(request, **kwargs):
        sends.append(True)
        return SimpleNamespace(status_code=200, json=lambda: {"data": {}})

    session = SimpleNamespace(send=original)
    attach_transport_guard(session, guard)
    request = SimpleNamespace(
        url="https://b.i.instagram.com/graphql_www",
        method="POST",
        headers={"X-Root-Field-Name": "usdid_registration"},
        body=urlencode(
            {"fb_api_req_friendly_name": "IGUSDIDRegistrationMutation", "client_doc_id": document}
        ),
    )
    if allowed:
        session.send(request)
        assert len(sends) == len(guard.calls) == 1
        assert not guard.calls[0]["mutation"]
    else:
        with pytest.raises(OperationBlocked):
            session.send(request)
        assert not sends and not guard.calls


@pytest.mark.parametrize("enabled", [True, False])
def test_media_transport_requires_scoped_guard_and_passes_exact_signed_metadata(enabled):
    guard = Guard()
    guard.allow_media = enabled
    sends = []
    session = SimpleNamespace(
        send=lambda request, **kwargs: (
            sends.append(request.url)
            or SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})
        )
    )
    attach_transport_guard(session, guard)
    request = SimpleNamespace(
        url="https://i.instagram.com/api/v1/media/configure/",
        method="POST",
        body=urlencode(
            {
                "signed_body": "SIGNATURE."
                + json.dumps({"upload_id": "123456", "caption": "Approved & visible"})
            }
        ),
    )
    if enabled:
        session.send(request)
        assert guard.calls == [
            {
                "media": {
                    "operation": "configure_photo",
                    "upload_id": "123456",
                    "caption": "Approved & visible",
                    "music_canonical_id": None,
                }
            }
        ]
        assert len(sends) == 1
    else:
        with pytest.raises(OperationBlocked):
            session.send(request)
        assert not sends


def test_configure_timeout_is_sticky_and_never_replayed():
    guard = Guard()
    guard.allow_media = True
    calls = []

    def timeout(request, **kwargs):
        calls.append(True)
        raise TimeoutError("Synthetic configure timeout")

    session = SimpleNamespace(send=timeout)
    attach_transport_guard(session, guard)
    request = SimpleNamespace(
        url="https://i.instagram.com/api/v1/media/configure_to_story/",
        method="POST",
        body=urlencode({"upload_id": "123456"}),
    )
    for _ in range(2):
        with pytest.raises(UncertainWrite):
            session.send(request)
    assert len(calls) == 1
