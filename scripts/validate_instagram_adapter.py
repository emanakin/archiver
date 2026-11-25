"""Run inside the optional Instagram image with --network none."""
from unittest.mock import patch
from app.adapters.guarded_client import build_client
from app.execution_errors import (
    ProfileVerificationRequired,
    Restriction,
    SessionRequired,
    TransientRead,
)

class Guard:
    fatal_error = None
    allow_auth = False
    def before_request(self, **_metadata):
        raise AssertionError("This offline contract check must not send a request")

guard = Guard()
client = build_client(guard)
for session in (client.private, client.public, client.graphql):
    assert all(adapter.max_retries.total == 0 for adapter in session.adapters.values())
assert client.with_challenge_flow is False
calls = []
def timeout(*args, **kwargs):
    from instagrapi.exceptions import ClientRequestTimeout
    calls.append(True)
    raise ClientRequestTimeout("Synthetic timeout")
with patch.object(client, "_send_private_request", side_effect=timeout):
    try:
        client.private_request("friendships/show/123/")
        raise AssertionError("Expected safe failure")
    except TransientRead:
        pass
assert len(calls) == 1
assert isinstance(guard.fatal_error, TransientRead)

guard = Guard()
client = build_client(guard, {"authorization_data": {"ds_user_id": "1"}})
calls = []
def session_failure(*args, **kwargs):
    calls.append(True)
    raise SessionRequired("Synthetic session failure")
with patch.object(client, "_send_private_request", side_effect=session_failure):
    try:
        client.user_follow("123")
        raise AssertionError("Expected session failure to block write")
    except SessionRequired:
        pass
assert len(calls) == 1, "Library precheck failure must never reach the mutation"
class AuthGuard:
    fatal_error = None
    allow_auth = True
    def __init__(self):
        self.calls = []
    def before_request(self, **metadata):
        self.calls.append(metadata)

import requests
auth = AuthGuard()
sent = []
def registration_response(_session, request, **kwargs):
    assert request.url == "https://b.i.instagram.com/graphql_www"
    sent.append(True)
    response = requests.Response()
    response.status_code = 200
    response.url = request.url
    response.request = request
    response._content = b'{"data":{"usdid_registration":{"success":true}}}'
    return response

with patch("requests.sessions.Session.send", registration_response):
    client = build_client(auth)
    assert client.usdid_register() is True
    assert len(sent) == len(auth.calls) == 1 and auth.calls[0]["mutation"] is False
    try:
        client.bloks_caa_resolve_two_step_verification({})
        raise AssertionError("Profile-code challenges must stop for official recovery")
    except ProfileVerificationRequired as stopped:
        assert not isinstance(stopped, Restriction)
assert len(sent) == 1

# Exercise the reviewed helper signatures/payload construction, with every
# transport replaced and networking disabled by the caller.
auth = AuthGuard()
auth.manual_profile_verification = True
steps = []
def code_provider():
    assert steps == ["com.bloks.www.ap.two_step_verification.entrypoint_async",
                     "com.bloks.www.ap.two_step_verification.code_entry"]
    steps.append("private-prompt")
    return "123456"
auth.profile_code_provider = code_provider
client = build_client(auth)
def synthetic_step(app, params, **kwargs):
    steps.append(app)
    if app.endswith("code_entry_async"):
        assert params["client_input_params"]["code"] == "123456"
    assert kwargs["login"] is True
    return {}
def profile_context(_result, app):
    if app.endswith("code_entry_async") and len(steps) < 2:
        return ""
    return "synthetic-context"
with patch.object(client, "bloks_extract_context_data", side_effect=profile_context), \
     patch.object(client, "bloks_async_action", side_effect=synthetic_step), \
     patch.object(client, "bloks_app", side_effect=synthetic_step), \
     patch.object(client, "bloks_apply_login_response", side_effect=lambda _: len(steps) == 4):
    assert client.bloks_caa_resolve_two_step_verification({})["logged_in"] is True
    try:
        client.bloks_caa_resolve_two_step_verification({})
        raise AssertionError("Manual profile code must never resubmit")
    except ProfileVerificationRequired:
        pass
assert len(steps) == 4 and not auth.calls

# A profile screen without a code context stays alive for explicit phone
# approval. Resume this client once, without another password submission.
auth = AuthGuard()
auth.manual_profile_verification = True
client = build_client(auth)
steps = []
entry_app = "com.bloks.www.ap.two_step_verification.entrypoint_async"
submit_app = "com.bloks.www.ap.two_step_verification.code_entry_async"

def approve_phone():
    assert steps == [entry_app]
    steps.append("phone-approved")
    return True

def phone_code():
    assert steps == [entry_app, "phone-approved", entry_app]
    steps.append("code")
    return "123456"

def phone_context(_result, app):
    if app == entry_app:
        return "synthetic-entry-context"
    if app == submit_app and "phone-approved" in steps:
        return "synthetic-submit-context"
    return ""

def phone_step(app, params, **kwargs):
    steps.append(app)
    return {}

auth.profile_approval_provider = approve_phone
auth.profile_code_provider = phone_code
with patch.object(client, "bloks_caa_login_prepare", return_value=True), \
     patch.object(client, "bloks_caa_login_send_request", return_value={"step": entry_app}) as password_call, \
     patch.object(client, "bloks_extract_context_data", side_effect=phone_context), \
     patch.object(client, "bloks_async_action", side_effect=phone_step), \
     patch.object(client, "bloks_apply_login_response", side_effect=lambda _: bool(steps) and steps[-1] == submit_app):
    assert client.login("synthetic.owner", "synthetic-password")
    assert password_call.call_count == 1
assert steps == [entry_app, "phone-approved", entry_app, "code", submit_app] and not auth.calls
client.password = None

# Parent login sends the password once; our CAA continuation then opens the
# returned MFA method and prompts. No call to login() is repeated with a code.
from instagrapi import Client
auth = AuthGuard()
client = build_client(auth)
steps = []
def password_response(*args, **kwargs):
    steps.append("password")
    assert not kwargs.get("verification_code")
    return {"logged_in": False, "two_step_verification_context": "synthetic-mfa-context",
            "sms_two_factor_on": True, "totp_two_factor_on": False}
def mfa_code_provider():
    assert len(steps) == 4 and steps[0] == "password"
    assert steps[-1] == "com.bloks.www.two_step_verification.method_picker.navigation.async"
    steps.append("private-mfa-prompt")
    return "123456"
auth.mfa_code_provider = mfa_code_provider
def mfa_step(app, params, **kwargs):
    steps.append(app)
    if app.endswith("method_picker.navigation.async"):
        assert params["client_input_params"]["selected_method"] == "sms"
    return {}
with patch.object(Client, "bloks_caa_login", side_effect=password_response) as password_call, \
     patch.object(client, "bloks_app", side_effect=mfa_step), \
     patch.object(client, "bloks_async_action", side_effect=mfa_step), \
     patch.object(client, "bloks_apply_login_response", return_value=True):
    assert client.login("synthetic.owner", "synthetic-password")
    assert password_call.call_count == 1
assert steps[-2] == "private-mfa-prompt" and steps[-1].endswith("verify_code.async")
assert not auth.calls
client.password = None
print("PASS: pinned client, no retries/replay, default challenge stop, explicit one-code recovery, phone approval then code with one password, password-first requested MFA, swallowed precheck blocked, exact owner-only device registration; zero network requests")
