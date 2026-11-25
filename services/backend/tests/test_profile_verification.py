"""Synthetic owner-code recovery: no library installation or network required."""

from types import SimpleNamespace

import pytest

from app.adapters.guarded_client import manual_profile_verification, screen_names
from app.execution_errors import (
    ProfileContinuationUnavailable,
    ProfileVerificationRequired,
    Restriction,
    SessionRequired,
)


def prepare(code="123456", missing=None, authenticated=True, direct=False):
    calls = []

    def extract(result, app):
        name = app.rsplit(".", 1)[1]
        calls.append(name)
        if name == "code_entry_async" and "request-code" not in calls and not direct:
            return ""
        return "" if name == missing else "synthetic-context"

    def step(name):
        def invoke(*args, **kwargs):
            calls.append(name)
            return {"phase": name}

        return invoke

    def provide():
        calls.append("prompt")
        return code

    client = SimpleNamespace(
        bloks_extract_context_data=extract,
        bloks_ap_two_step_verification_entrypoint=step("open"),
        bloks_ap_two_step_verification_code_entry=step("request-code"),
        bloks_ap_two_step_verification_submit_code=step("submit"),
        bloks_apply_login_response=lambda result: authenticated and result.get("phase") == "submit",
    )
    guard = SimpleNamespace(
        allow_auth=True,
        manual_profile_verification=True,
        fatal_error=None,
        profile_code_provider=provide,
    )
    return client, guard, calls


@pytest.mark.parametrize("flag", ["allow_auth", "manual_profile_verification"])
def test_default_or_read_scope_stops_before_driving_challenge(flag):
    client, guard, calls = prepare()
    setattr(guard, flag, False)
    with pytest.raises(ProfileVerificationRequired):
        manual_profile_verification(client, guard, {})
    assert calls == [] and isinstance(guard.fatal_error, SessionRequired)
    assert not isinstance(guard.fatal_error, Restriction)


def test_explicit_recovery_requests_code_at_correct_step_and_submits_once():
    client, guard, calls = prepare()
    assert manual_profile_verification(client, guard, {})["logged_in"] is True
    assert calls == [
        "entrypoint_async",
        "open",
        "code_entry_async",
        "code_entry",
        "request-code",
        "code_entry_async",
        "prompt",
        "submit",
    ]
    with pytest.raises(ProfileVerificationRequired):
        manual_profile_verification(client, guard, {})
    assert calls.count("submit") == 1 and calls.count("prompt") == 1


def test_server_can_return_submission_context_without_another_code_screen():
    client, guard, calls = prepare(direct=True)
    assert manual_profile_verification(client, guard, {})["logged_in"] is True
    assert "request-code" not in calls
    assert calls.count("submit") == 1 and calls.count("prompt") == 1


@pytest.mark.parametrize("code", ["", "not-a-code", "12345", "123456789"])
def test_cancelled_or_invalid_code_never_submits(code):
    client, guard, calls = prepare(code)
    with pytest.raises(ProfileVerificationRequired) as error:
        manual_profile_verification(client, guard, {})
    assert "submit" not in calls
    if code:
        assert code not in str(error.value)


@pytest.mark.parametrize("missing", ["entrypoint_async", "code_entry", "code_entry_async"])
def test_missing_context_never_prompts_or_submits(missing):
    client, guard, calls = prepare(missing=missing)
    with pytest.raises(ProfileVerificationRequired):
        manual_profile_verification(client, guard, {})
    assert "prompt" not in calls and "submit" not in calls


def test_rejected_code_stops_without_resubmission():
    client, guard, calls = prepare(authenticated=False)
    with pytest.raises(ProfileVerificationRequired):
        manual_profile_verification(client, guard, {})
    with pytest.raises(ProfileVerificationRequired):
        manual_profile_verification(client, guard, {})
    assert calls.count("submit") == 1


@pytest.mark.parametrize("after_approval", ["code", "connected", "unsupported"])
def test_phone_approval_waits_for_owner_then_refreshes_once(after_approval):
    calls = []
    phase = {"approved": False}

    def extract(result, app):
        if app.endswith("entrypoint_async"):
            return "entry-context"
        if phase["approved"] and after_approval == "code" and app.endswith("code_entry_async"):
            return "submit-context"
        return ""

    def entry(context, **kwargs):
        assert context == "entry-context"
        calls.append("entry")
        return {"approved": phase["approved"]}

    def approve():
        assert calls == ["entry"]
        calls.append("owner-approval")
        phase["approved"] = True
        return True

    def code():
        assert calls == ["entry", "owner-approval", "entry"]
        calls.append("code")
        return "123456"

    def submit(context, value, **kwargs):
        assert context == "submit-context" and value == "123456"
        calls.append("submit")
        return {"session": True}

    client = SimpleNamespace(
        bloks_extract_context_data=extract,
        bloks_ap_two_step_verification_entrypoint=entry,
        bloks_ap_two_step_verification_submit_code=submit,
        bloks_apply_login_response=lambda result: (
            result.get("session") or (after_approval == "connected" and result.get("approved"))
        ),
    )
    guard = SimpleNamespace(
        allow_auth=True,
        manual_profile_verification=True,
        profile_code_provider=code,
        profile_approval_provider=approve,
    )
    if after_approval == "unsupported":
        with pytest.raises(ProfileVerificationRequired):
            manual_profile_verification(client, guard, {})
    else:
        assert manual_profile_verification(client, guard, {})["logged_in"]
    assert calls.count("entry") == 2 and calls.count("owner-approval") == 1
    assert calls.count("submit") == (1 if after_approval == "code" else 0)


def test_cancelled_phone_approval_does_not_refresh_or_prompt_for_code():
    client, guard, calls = prepare(missing="code_entry", authenticated=False)
    guard.profile_approval_provider = lambda: False
    with pytest.raises(ProfileVerificationRequired):
        manual_profile_verification(client, guard, {})
    assert calls.count("open") == 1 and "submit" not in calls and "prompt" not in calls


def test_phone_approval_can_be_needed_after_opening_code_screen():
    client, guard, calls = prepare(missing="code_entry_async")
    original_extract = client.bloks_extract_context_data
    approved = False

    def approve():
        nonlocal approved
        assert "request-code" in calls and "prompt" not in calls
        approved = True
        return True

    def extract(result, app):
        if approved and app.endswith("code_entry_async"):
            return "synthetic-submit-context"
        return original_extract(result, app)

    client.bloks_extract_context_data = extract
    guard.profile_approval_provider = approve
    assert manual_profile_verification(client, guard, {})["logged_in"]
    assert calls.count("open") == 2 and calls.count("submit") == 1


def test_screen_names_keep_only_bloks_app_ids():
    payload = {
        "layout": {
            "bloks_payload": {
                "action": '(bk.action.f4i "com.bloks.www.ap.two_step_verification.entrypoint_async"'
                ' (f4i "context_data" "synthetic-private-context") "sessionid=synthetic")',
                "nested": ["com.bloks.www.synthetic.approval_screen", 7, None],
            }
        }
    }
    names = screen_names(payload)
    assert names == [
        "com.bloks.www.ap.two_step_verification.entrypoint_async",
        "com.bloks.www.synthetic.approval_screen",
    ]
    assert "synthetic-private-context" not in str(names) and "sessionid" not in str(names)


@pytest.mark.parametrize("choice", ["approved", "code"])
def test_owner_choice_and_each_verification_screen_are_journaled(choice):
    events = []

    def extract(result, app):
        return "entry-context" if app.endswith("entrypoint_async") else ""

    client = SimpleNamespace(
        bloks_extract_context_data=extract,
        bloks_ap_two_step_verification_entrypoint=lambda context, **kwargs: {
            "action": "com.bloks.www.synthetic.approval_screen"
        },
        bloks_extract_login_response=lambda result: {},
        bloks_apply_login_response=lambda result: False,
    )
    guard = SimpleNamespace(
        allow_auth=True,
        manual_profile_verification=True,
        profile_code_provider=lambda: "123456",
        profile_approval_provider=lambda: choice,
        record_event=lambda event, detail: events.append((event, detail)),
    )
    with pytest.raises(ProfileContinuationUnavailable) as error:
        manual_profile_verification(client, guard, {"action": "com.bloks.www.synthetic.login"})
    assert ("owner_choice", {"choice": choice}) in events
    stages = [detail["stage"] for event, detail in events if event == "verification_screens"]
    assert stages == ["login", "entrypoint", "after_" + choice]
    assert events[-1][1]["screens"] == ["com.bloks.www.synthetic.approval_screen"]
    assert ("code-entry" in str(error.value)) == (choice == "code")
