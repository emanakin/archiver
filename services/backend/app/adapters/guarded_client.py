"""Pinned Instagrapi client with one guarded transport attempt per request."""

import hashlib
import json
import logging
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..execution_errors import (
    ExecutionError,
    Inaccessible,
    OperationBlocked,
    ProfileContinuationUnavailable,
    ProfileVerificationRequired,
    Restriction,
    SessionRequired,
    TransientRead,
    UncertainWrite,
)


def verify_installation(manifest_path="/app/instagram-build.json"):
    pin = json.loads((Path(__file__).resolve().parents[1] / "instagram_pin.json").read_text())
    try:
        manifest = json.loads(Path(manifest_path).read_text())
        installed = version("instagrapi")
    except (OSError, ValueError, PackageNotFoundError):
        raise OperationBlocked(
            "Install the reviewed Instagram image before connecting an account"
        ) from None
    if installed != pin["version"] or manifest.get("commit") != pin["commit"]:
        raise OperationBlocked("Installed Instagram library does not match the reviewed source pin")
    return pin


def translate(exc, mutation=False):
    if isinstance(exc, ExecutionError):
        return exc
    names = {kind.__name__ for kind in type(exc).__mro__}
    if names & {
        "ChallengeError",
        "ChallengeRequired",
        "FeedbackRequired",
        "RateLimitError",
        "ClientThrottledError",
        "PleaseWaitFewMinutes",
        "SentryBlock",
        "AccountSuspended",
    }:
        return Restriction("Instagram requires manual account recovery")
    if names & {
        "LoginRequired",
        "ClientLoginRequired",
        "ClientUnauthorizedError",
        "BadPassword",
        "BadCredentials",
        "TwoFactorRequired",
    }:
        return SessionRequired("Instagram session requires manual verification")
    if names & {
        "UserNotFound",
        "MediaNotFound",
        "PrivateAccount",
        "ClientNotFoundError",
        "MediaUnavailable",
    }:
        return Inaccessible("Source or target is inaccessible to this account")
    failure = (
        UncertainWrite("Instagram mutation outcome is uncertain")
        if mutation
        else TransientRead("Instagram read did not return a validated response")
    )
    failure.failure_kind = (
        "timeout"
        if names
        & {"Timeout", "TimeoutError", "ConnectTimeout", "ReadTimeout", "ClientRequestTimeout"}
        else "request_error"
    )
    return failure


def safe_settings(values):
    values = dict(values or {})
    values.update(
        session_retry_total=0,
        session_retry_statuses=[],
        public_request_retries_count=0,
        private_transport="requests",
        public_transport="requests",
        tls_verify=True,
        request_timeout=1,
    )
    return values


def request_form(request):
    body = request.body or ""
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    fields = parse_qs(body, max_num_fields=64) if len(body) <= 65536 else {}
    signed = fields.get("signed_body", [""])[0]
    values = (
        json.loads(signed.split(".", 1)[1])
        if signed
        else {key: value[0] for key, value in fields.items()}
    )
    if not isinstance(values, dict):
        raise ValueError("Invalid Instagram form")
    return values


def attach_transport_guard(session, guard):
    original_send = session.send

    def send(request, **kwargs):
        if getattr(guard, "fatal_error", None):
            raise guard.fatal_error
        url = urlsplit(request.url)
        if url.scheme != "https" or not (
            url.hostname == "instagram.com" or (url.hostname or "").endswith(".instagram.com")
        ):
            raise OperationBlocked("Unexpected Instagram transport destination")
        match = re.fullmatch(r"/api/v1/friendships/(create|destroy)/(\d+)/?", url.path)
        mutation = bool(match)
        media = None
        receipt = None
        if url.path in {
            "/api/v1/direct_v2/threads/broadcast/text/",
            "/api/v1/direct_v2/threads/broadcast/link/",
        }:
            if not (
                request.method == "POST"
                and url.hostname == "i.instagram.com"
                and getattr(guard, "allow_dm_receipt", False)
            ):
                raise OperationBlocked("Only a verified receipt job can send a direct message")
            try:
                values = request_form(request)
                if "recipient_users" in values or values.get("action") != "send_item":
                    raise ValueError("Invalid receipt destination")
                receipt = {
                    "text": values.get("link_text")
                    if url.path.endswith("/link/")
                    else values.get("text"),
                    "thread_ids": json.loads(values["thread_ids"]),
                    "links": json.loads(values.get("link_urls", "[]")),
                    "context": values["client_context"],
                }
            except (TypeError, ValueError, KeyError, UnicodeError, IndexError):
                raise OperationBlocked("Could not validate the DM receipt request") from None
        reel_settings = re.fullmatch(r"/upload_settings/([0-9a-f-]{36})", url.path)
        reel_video = re.fullmatch(
            r"/rupload_igvideo/([0-9a-f]{32})-0-(\d{1,9})-(\d+)-(\d+)", url.path
        )
        if reel_settings or reel_video:
            if url.hostname != "i.instagram.com" or not getattr(guard, "allow_media", False):
                raise OperationBlocked("Only an approved Reel may upload video")
            try:
                if reel_settings and request.method == "POST":
                    body = json.loads(request.body) if len(request.body) <= 4096 else {}
                    context = body["upload_setting_properties"]["context"]
                    if (
                        body["composer_session_id"] != reel_settings[1]
                        or context["source_type"] != "clips"
                    ):
                        raise ValueError
                    media = {
                        "operation": "reel_settings",
                        "session": reel_settings[1],
                        "target_id": str(context["target_id"]),
                    }
                elif reel_video and request.method in {"GET", "POST"}:
                    params = json.loads(request.headers["X-Instagram-Rupload-Params"])
                    if (
                        params["upload_id"] != reel_video[3]
                        or reel_video[3] != reel_video[4]
                        or params["share_type"] != "reels"
                        or params["is_clips_video"] != "1"
                        or params.get("xsharing_user_ids") != "[]"
                        or (request.method == "POST" and len(request.body) != int(reel_video[2]))
                    ):
                        raise ValueError
                    media = {
                        "operation": "reel_video" if request.method == "POST" else "reel_init",
                        "upload_id": reel_video[3],
                        "session": reel_video[1],
                        "size": int(reel_video[2]),
                    }
                    if request.method == "POST":
                        media["sha256"] = hashlib.sha256(request.body).hexdigest()
                else:
                    raise ValueError
            except (ValueError, KeyError, TypeError, AttributeError):
                raise OperationBlocked("Could not validate the Reel upload request") from None
        elif request.method == "POST" and getattr(guard, "allow_media", False):
            upload = re.fullmatch(r"/rupload_igphoto/([0-9]+)_0_[0-9]+", url.path)
            configure = url.path in {
                "/api/v1/media/configure/",
                "/api/v1/media/configure_to_story/",
                "/api/v1/media/configure_sidecar/",
                "/api/v1/media/configure_to_clips/",
            }
            change = re.fullmatch(
                r"/api/v1/media/([0-9]+(?:_[0-9]+)?)/(edit_media|delete|only_me)/", url.path
            )
            if upload and url.hostname == "i.instagram.com":
                try:
                    params = json.loads(request.headers.get("X-Instagram-Rupload-Params", "{}"))
                    if not isinstance(params, dict):
                        raise ValueError("Invalid upload parameters")
                except (TypeError, ValueError):
                    raise OperationBlocked("Could not validate the approved photo upload") from None
                media = {
                    "operation": "upload",
                    "upload_id": upload.group(1),
                    "sidecar": str(params.get("is_sidecar")) == "1",
                }
            elif (configure or change) and url.hostname == "i.instagram.com":
                try:
                    values = request_form(request)
                except (TypeError, ValueError, UnicodeError, IndexError):
                    raise OperationBlocked("Could not validate the approved media form") from None
                if configure:
                    music = values.get("music_params")
                    if music is not None and not isinstance(music, dict):
                        raise OperationBlocked("Invalid music configure form")
                    media = {
                        "operation": "configure_carousel"
                        if "configure_sidecar" in url.path
                        else "configure_reel"
                        if "configure_to_clips" in url.path
                        else "configure_story"
                        if "to_story" in url.path
                        else "configure_photo",
                        "upload_id": values.get("upload_id"),
                        "caption": values.get("caption", ""),
                        "music_canonical_id": str(music["music_canonical_id"])
                        if music and music.get("music_canonical_id")
                        else None,
                    }
                    if music is not None:
                        if getattr(guard, "feed_music", None):
                            media["playlist_music"] = {
                                "audio_asset_id": str(music.get("audio_asset_id")),
                                "audio_cluster_id": str(music.get("audio_cluster_id")),
                                "title": music.get("song_name"),
                                "artist": music.get("artist_name"),
                                "start_ms": music.get("audio_asset_start_time_in_ms"),
                            }
                            if music.get("product") != "music_in_feed" or not music.get(
                                "alacorn_session_id"
                            ):
                                raise OperationBlocked("Invalid feed music configure form")
                        elif not media["music_canonical_id"]:
                            raise OperationBlocked("The music configure form lacks an approved ID")
                    if media["operation"] == "configure_carousel":
                        children = values.get("children_metadata")
                        if not isinstance(children, list) or any(
                            not isinstance(child, dict) for child in children
                        ):
                            raise OperationBlocked("Could not validate the carousel child uploads")
                        media["client_sidecar_id"] = str(values.get("client_sidecar_id", ""))
                        media["child_upload_ids"] = [
                            str(child.get("upload_id", "")) for child in children
                        ]
                else:
                    endpoint = change.group(2)
                    remove_kind = getattr(guard, "media_removal_kind", "delete_story")
                    if endpoint == "delete" and remove_kind not in {"delete_post", "delete_story"}:
                        raise OperationBlocked("The approved action does not delete media")
                    if endpoint == "only_me" and str(values.get("media_id")) != change.group(1):
                        raise OperationBlocked(
                            "The archive form does not identify the original media"
                        )
                    media = {
                        "operation": "edit_caption"
                        if endpoint == "edit_media"
                        else "archive_post"
                        if endpoint == "only_me"
                        else remove_kind,
                        "media_id": change.group(1),
                        "caption": values.get("caption_text", ""),
                    }
                mutation = True
        if mutation and request.method != "POST":
            raise OperationBlocked("Unsupported mutation method")
        if (
            request.method not in {"GET", "HEAD"}
            and not mutation
            and media is None
            and receipt is None
        ):
            music_read = False
            if (
                request.method == "POST"
                and url.hostname == "i.instagram.com"
                and url.path == "/api/v1/music/search_v2/"
                and (queries := getattr(guard, "music_queries", ()))
            ):
                try:
                    values = request_form(request)
                except (TypeError, ValueError, UnicodeError, IndexError):
                    raise OperationBlocked("Could not validate the feed music search") from None
                music_read = (
                    values.get("product") == "music_in_feed"
                    and values.get("q") in queries
                    and values.get("from_typeahead") == "false"
                    and set(values)
                    == {
                        "q",
                        "product",
                        "from_typeahead",
                        "_uuid",
                        "search_session_id",
                        "browse_session_id",
                    }
                )
            if (
                request.method == "POST"
                and url.hostname == "i.instagram.com"
                and url.path
                in {"/api/v1/clips/music/", "/api/v1/music/music_in_feed_audio_browser/"}
            ):
                track_id = getattr(guard, "approved_music", None)
                feed_music = getattr(guard, "feed_music", None)
                try:
                    values = request_form(request)
                    music_read = (
                        bool(track_id) and str(values.get("music_canonical_id")) == track_id
                        if url.path == "/api/v1/clips/music/"
                        else bool(track_id or feed_music)
                        and values.get("product") == "music_in_feed"
                        and set(values) == {"product", "_uuid", "browse_session_id"}
                    )
                except (TypeError, ValueError, UnicodeError, IndexError):
                    raise OperationBlocked("Could not validate the approved music lookup") from None
            registration = False
            if (
                url.hostname == "b.i.instagram.com"
                and url.path == "/graphql_www"
                and request.method == "POST"
            ):
                # Exact registration document from the reviewed Android CAA
                # flow. Never permit arbitrary GraphQL mutations during login.
                body = getattr(request, "body", b"") or b""
                try:
                    if isinstance(body, bytes):
                        body = body.decode("utf-8")
                    form = parse_qs(body, max_num_fields=32) if len(body) <= 32768 else {}
                    registration = (
                        form.get("fb_api_req_friendly_name") == ["IGUSDIDRegistrationMutation"]
                        and form.get("client_doc_id") == ["124930351917786857261002920888"]
                        and request.headers.get("X-Root-Field-Name") == "usdid_registration"
                    )
                except (TypeError, ValueError, UnicodeError):
                    registration = False
            auth_path = (
                registration
                or url.path
                in {
                    "/api/v1/accounts/login/",
                    "/api/v1/accounts/two_factor_login/",
                    "/api/v1/launcher/sync/",
                    "/api/v1/qe/sync/",
                    "/api/v1/attestation/create_android_keystore/",
                }
                or bool(
                    re.fullmatch(
                        r"/api/v1/bloks/(apps|async_action)/com\.bloks\.www\.[a-z0-9._]*(login|two_step_verification)[a-z0-9._]*/",
                        url.path,
                    )
                )
            )
            if not music_read and not (
                getattr(guard, "allow_auth", False) and auth_path and request.method == "POST"
            ):
                raise OperationBlocked("This operation cannot send an unapproved POST")
        metadata = (
            {
                "operation": "follow" if match.group(1) == "create" else "unfollow",
                "target_id": match.group(2),
            }
            if match
            else {}
        )
        try:
            if media is not None:
                guard.before_request(media=media)
            elif receipt is not None:
                guard.before_request(receipt=receipt)
            else:
                guard.before_request(mutation=mutation, **metadata)
            kwargs["timeout"] = (10, 25)
            kwargs["allow_redirects"] = False
            response = original_send(request, **kwargs)
            if response.status_code in {403, 429}:
                raise Restriction("Instagram restricted this request")
            if response.status_code == 401:
                raise SessionRequired("Instagram session requires verification")
            try:
                data = response.json()
            except (ValueError, TypeError):
                data = {}
            message = data.get("message") if isinstance(data, dict) else None
            if message in {"challenge_required", "feedback_required", "sentry_block"}:
                raise Restriction("Instagram requires manual account recovery")
            if message == "login_required":
                raise SessionRequired("Instagram session requires verification")
            return response
        except Exception as exc:
            failure = translate(exc, mutation or receipt is not None)
            guard.fatal_error = failure
            raise failure from None

    session.send = send


SCREEN_NAME = re.compile(r"com\.bloks\.www(?:\.[A-Za-z0-9_]+)+")


def screen_names(value, limit=25):
    """Bloks app ids referenced by a response; never context values, cookies or text."""
    found = set()
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str):
            found.update(name[:100] for name in SCREEN_NAME.findall(item))
    return sorted(found)[:limit]


def record_screens(client, guard, stage, value):
    recorder = getattr(guard, "record_event", None)
    if not callable(recorder):
        return
    try:
        session = bool(client.bloks_extract_login_response(value))
    except Exception:
        session = False
    recorder(
        "verification_screens",
        {"stage": stage, "screens": screen_names(value), "session": session},
    )


def manual_profile_verification(client, guard, result, domain=None):
    """One explicit owner recovery/code submission; normal login never drives it."""
    if getattr(guard, "fatal_error", None):
        raise guard.fatal_error

    def stop(message, failure_type=ProfileVerificationRequired):
        failure = failure_type(message)
        guard.fatal_error = failure
        raise failure

    if not (
        getattr(guard, "allow_auth", False) and getattr(guard, "manual_profile_verification", False)
    ):
        stop("Complete profile verification in Instagram or choose explicit manual-code recovery")
    provider = getattr(guard, "profile_code_provider", None)
    if not callable(provider) or getattr(guard, "profile_verification_attempted", False):
        stop("Manual profile verification is unavailable or already attempted")
    guard.profile_verification_attempted = True
    entry = "com.bloks.www.ap.two_step_verification.entrypoint_async"
    code_entry = "com.bloks.www.ap.two_step_verification.code_entry"
    submit = "com.bloks.www.ap.two_step_verification.code_entry_async"
    record_screens(client, guard, "login", result)
    context = client.bloks_extract_context_data(result, entry)
    if not context:
        stop("Profile verification did not provide an entry context")
    entry_context = context
    step = client.bloks_ap_two_step_verification_entrypoint(entry_context, domain=domain)
    record_screens(client, guard, "entrypoint", step)
    if client.bloks_apply_login_response(step):
        return {"logged_in": True, "reason": "", "result": step}

    def open_code_screen(value, stage):
        submitted = client.bloks_extract_context_data(value, submit)
        if not submitted:
            input_context = client.bloks_extract_context_data(value, code_entry)
            if input_context:
                value = client.bloks_ap_two_step_verification_code_entry(
                    input_context, domain=domain
                )
                record_screens(client, guard, stage, value)
                submitted = client.bloks_extract_context_data(value, submit)
        return value, submitted

    step, context = open_code_screen(step, "code_entry")
    if not context:
        # A phone approval screen is not a code screen. Keep this same client
        # alive while the owner reviews Instagram, then refresh this exact
        # reviewed entrypoint once. Never resend the password or poll Instagram.
        approval = getattr(guard, "profile_approval_provider", None)
        if not callable(approval) or getattr(guard, "profile_approval_attempted", False):
            stop("Profile verification did not provide a code-entry context")
        guard.profile_approval_attempted = True
        # "approved": the owner approved on the phone. "code": Instagram showed
        # the owner a code prompt instead. Both get the same single refresh;
        # the choice only decides which outcome is expected and is journaled.
        choice = approval()
        if choice is True:
            choice = "approved"
        if choice not in {"approved", "code"}:
            stop("Phone approval was cancelled")
        recorder = getattr(guard, "record_event", None)
        if callable(recorder):
            recorder("owner_choice", {"choice": choice})
        if getattr(guard, "fatal_error", None):
            raise guard.fatal_error
        refreshed_context = client.bloks_extract_context_data(step, entry) or entry_context
        step = client.bloks_ap_two_step_verification_entrypoint(refreshed_context, domain=domain)
        record_screens(client, guard, "after_" + choice, step)
        if client.bloks_apply_login_response(step):
            return {"logged_in": True, "reason": "", "result": step}
        step, context = open_code_screen(step, "after_" + choice + "_code_entry")
        if not context:
            stop(
                "Instagram did not return a supported continuation after phone approval"
                if choice == "approved"
                else "Instagram did not return a code-entry screen after the owner reported a code",
                ProfileContinuationUnavailable,
            )
    code = provider().strip()
    if not re.fullmatch(r"[0-9]{6,8}", code):
        stop("Manual verification was cancelled or its code format was invalid")
    try:
        result = client.bloks_ap_two_step_verification_submit_code(context, code, domain=domain)
    finally:
        code = None
    if not client.bloks_apply_login_response(result):
        stop("Manual verification did not establish an authenticated session")
    return {"logged_in": True, "reason": "", "result": result}


def complete_requested_mfa(client, guard, outcome):
    """Continue a returned CAA MFA context without submitting the password again."""
    if getattr(guard, "fatal_error", None):
        raise guard.fatal_error
    provider = getattr(guard, "mfa_code_provider", None)
    if not getattr(guard, "allow_auth", False) or not callable(provider):
        raise SessionRequired("Instagram requested MFA; use the private owner terminal")
    if getattr(guard, "mfa_attempted", False):
        raise SessionRequired("The MFA code has already been attempted; no automatic retry")
    context = client._extract_two_step_verification_context(outcome)
    if not context:
        raise SessionRequired("Instagram did not supply a usable MFA context")
    guard.mfa_attempted = True
    method = client._infer_bloks_two_factor_challenge(outcome, "")
    if method not in {"sms", "totp"}:
        raise SessionRequired("This MFA method requires owner recovery in Instagram")
    # Selecting SMS can trigger delivery. Prompt only after this step, never
    # before the password request or before requesting delivery.
    client.bloks_two_step_verification_entrypoint(context)
    client.bloks_two_step_verification_method_picker(context)
    client.bloks_two_step_verification_select_method(context, selected_method=method)
    code = provider().strip()
    if not re.fullmatch(r"[0-9]{6}", code):
        raise SessionRequired("MFA was cancelled or the code format was invalid")
    try:
        result = client.bloks_two_step_verification_verify_code(context, code, challenge=method)
    finally:
        code = None
    if not client.bloks_apply_login_response(result):
        raise SessionRequired("MFA did not establish an authenticated session")
    return {"logged_in": True, "reason": "", "result": result}


def build_client(guard, values=None, check_pin=True):
    if check_pin:
        verify_installation()
    from instagrapi import Client

    logger = logging.getLogger("archive_desk.instagram_private")
    logger.disabled = True
    # The pinned library has response/body debug loggers. Disable every library
    # logger after importing its modules; never pass library exceptions to logs.
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("instagrapi") or name in {"private_request", "public_request"}:
            logging.getLogger(name).disabled = True
            logging.getLogger(name).propagate = False

    class SingleAttemptClient(Client):
        def bloks_caa_login(self, verification_code=""):
            outcome = super().bloks_caa_login(verification_code=verification_code)
            if (
                not outcome.get("logged_in")
                and not verification_code
                and self._extract_two_step_verification_context(outcome)
            ):
                return complete_requested_mfa(self, guard, outcome)
            return outcome

        def bloks_caa_resolve_two_step_verification(
            self, send_result, verification_code="", domain=None
        ):
            return manual_profile_verification(self, guard, send_result, domain)

        def user_friendship_v1(self, user_id):
            from instagrapi.types import Relationship

            try:
                result = self.private_request(
                    f"friendships/show/{user_id}/",
                    params={"is_external_deeplink_profile_view": "false"},
                )
                return Relationship(user_id=str(user_id), **result)
            except Exception as exc:
                failure = translate(exc)
                guard.fatal_error = failure
                raise failure from None

        def private_request(
            self,
            endpoint,
            data=None,
            params=None,
            login=False,
            with_signature=True,
            headers=None,
            extra_sig=None,
            domain=None,
        ):
            if getattr(guard, "fatal_error", None):
                raise guard.fatal_error
            headers = dict(headers or {})
            if self.authorization:
                headers.setdefault("Authorization", self.authorization)
            self.private_requests_count += 1
            mutation = bool(re.fullmatch(r"friendships/(create|destroy)/\d+/?", endpoint))
            try:
                return self._send_private_request(
                    endpoint,
                    data=data,
                    params=params,
                    login=login,
                    with_signature=with_signature,
                    headers=headers,
                    extra_sig=extra_sig,
                    domain=domain,
                )
            except Exception as exc:
                if (
                    getattr(guard, "allow_auth", False)
                    and type(exc).__name__ == "TwoFactorRequired"
                ):
                    # One explicitly supplied verification code may continue the
                    # same bounded authentication operation. Challenges still stop.
                    raise
                failure = translate(exc, mutation)
                guard.fatal_error = failure
                raise failure from None

        def login_flow(self):
            # Optional post-login feed/push emulation is outside connection intent.
            return True

    client = SingleAttemptClient(
        settings=safe_settings(values),
        with_challenge_flow=False,
        private_transport="requests",
        public_transport="requests",
        session_retry_total=0,
        public_request_retries_count=0,
        logger=logger,
    )
    client.with_challenge_flow = False
    client.handle_exception = None
    for name in ("private", "public", "graphql"):
        session = getattr(client, name, None)
        if session is not None:
            attach_transport_guard(session, guard)
    return client
