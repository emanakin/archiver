"""MFA starts after the password response and method selection, without replay."""

from types import SimpleNamespace

import pytest

from app import manage
from app.adapters.guarded_client import complete_requested_mfa
from app.execution_errors import SessionRequired


def prepare(code="654321", method="sms", authenticated=True):
    calls = []

    def step(name):
        def invoke(*args, **kwargs):
            calls.append(name)
            return {}

        return invoke

    def prompt():
        assert calls == ["entry", "picker", "select"]
        calls.append("prompt")
        return code

    client = SimpleNamespace(
        _extract_two_step_verification_context=lambda _: "synthetic-context",
        _infer_bloks_two_factor_challenge=lambda *_: method,
        bloks_two_step_verification_entrypoint=step("entry"),
        bloks_two_step_verification_method_picker=step("picker"),
        bloks_two_step_verification_select_method=step("select"),
        bloks_two_step_verification_verify_code=step("submit"),
        bloks_apply_login_response=lambda _: authenticated,
    )
    guard = SimpleNamespace(allow_auth=True, fatal_error=None, mfa_code_provider=prompt)
    return client, guard, calls


@pytest.mark.parametrize("method", ["sms", "totp"])
def test_code_is_prompted_after_delivery_selection_and_submitted_once(method):
    client, guard, calls = prepare(method=method)
    assert complete_requested_mfa(client, guard, {})["logged_in"]
    assert calls == ["entry", "picker", "select", "prompt", "submit"]
    with pytest.raises(SessionRequired):
        complete_requested_mfa(client, guard, {})
    assert calls.count("submit") == 1


@pytest.mark.parametrize("code", ["", "bad-code", "12345", "1234567"])
def test_invalid_or_cancelled_mfa_stops_without_a_submission(code):
    client, guard, calls = prepare(code)
    with pytest.raises(SessionRequired):
        complete_requested_mfa(client, guard, {})
    assert "submit" not in calls


def test_read_scope_cannot_drive_mfa():
    client, guard, calls = prepare()
    guard.allow_auth = False
    with pytest.raises(SessionRequired):
        complete_requested_mfa(client, guard, {})
    assert calls == []


def test_mfa_rejection_never_retries():
    client, guard, calls = prepare(authenticated=False)
    with pytest.raises(SessionRequired):
        complete_requested_mfa(client, guard, {})
    with pytest.raises(SessionRequired):
        complete_requested_mfa(client, guard, {})
    assert calls.count("submit") == 1


def test_cli_collects_only_password_before_connecting(monkeypatch):
    prompts, connections = [], []

    def private_input(prompt):
        prompts.append(prompt)
        assert "password" in prompt.lower()
        return "synthetic-password"

    monkeypatch.setattr("sys.argv", ["manage", "connect", "--account-id", "synthetic-id"])
    monkeypatch.setattr(manage, "getpass", private_input)
    monkeypatch.setattr(
        manage, "connect_account", lambda *args, **kwargs: connections.append(kwargs)
    )
    manage.main()
    assert len(prompts) == 1 and len(connections) == 1
    assert connections[0]["password"] == "synthetic-password"
    assert not connections[0].get("verification_code")
