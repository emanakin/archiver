"""Google identity, one-time login flows and private workspace admission."""

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache, partial
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.id_token import verify_oauth2_token
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session

from .config import settings
from .models import OWNER_WORKSPACE_ID, GoogleLoginFlow, User, WebSession, Workspace, now
from .schemas import Strict

router = APIRouter()
SESSION = "__Host-archive_session"
FLOW = "__Host-archive_oauth"
FLOW_SECONDS = 600
SESSION_SECONDS = 7 * 24 * 3600
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


@dataclass(frozen=True)
class Principal:
    user_id: str
    workspace_id: str
    email: str


class IdentityResponse(Strict):
    auth_mode: str
    email: str | None = None
    workspace_id: str | None = None


def digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def token():
    return secrets.token_urlsafe(32)


def local_path(value):
    if not value or len(value) > 512 or not value.startswith("/") or value.startswith("//"):
        return "/"
    decoded = unquote(value)
    if decoded.startswith("//") or "\\" in decoded or any(ord(ch) < 32 for ch in decoded):
        return "/"
    try:
        url = urlsplit(value)
    except ValueError:
        return "/"
    if url.netloc or url.scheme or url.path.startswith(("/auth/", "/api/", "/login")):
        return "/"
    return value


@lru_cache
def identity_engine(url):
    if not url:
        raise RuntimeError("The identity database is required")
    return create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)


def identity_db():
    return Session(identity_engine(settings().auth_database_url))


@lru_cache
def oauth_client(path):
    client = json.loads(Path(path).read_text(encoding="utf-8-sig"))["web"]
    client_id, secret = client["client_id"], client["client_secret"]
    if not isinstance(client_id, str) or not client_id.endswith(".apps.googleusercontent.com"):
        raise ValueError("Expected a Google web client")
    if not isinstance(secret, str) or not secret:
        raise ValueError("Expected a Google client secret")
    return client_id, secret


def enforce_role(connection, group):
    valid = connection.scalar(
        text("""
        SELECT NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole
            AND pg_has_role(current_user, :group, 'USAGE')
            AND NOT EXISTS (
                SELECT 1 FROM pg_roles AS other
                WHERE other.rolname IN (
                    'archive_tenant_request', 'archive_identity_request', 'archive_worker_data'
                ) AND other.rolname <> :group
                AND pg_has_role(current_user, other.oid, 'MEMBER')
            )
        FROM pg_roles WHERE rolname = current_user
    """),
        {"group": group},
    )
    if valid is not True:
        raise RuntimeError("Invalid database role for Google sign-in")


def validate_runtime(api_connection):
    cfg = settings()
    enforce_role(api_connection, "archive_tenant_request")
    if api_connection.scalar(text("SELECT has_table_privilege(current_user, 'users','SELECT')")):
        raise RuntimeError("Workspace API role can read identity records")
    if not api_connection.scalar(text("SELECT row_security_active('accounts'::regclass)")):
        raise RuntimeError("Workspace row policies are inactive")
    if not cfg.app_origin.startswith("https://") or not cfg.bootstrap_owner_email:
        raise RuntimeError("Google sign-in needs an HTTPS origin and an owner")
    oauth_client(cfg.google_oauth_client_file)
    with identity_engine(cfg.auth_database_url).connect() as conn:
        enforce_role(conn, "archive_identity_request")
        if conn.scalar(text("SELECT has_table_privilege(current_user, 'accounts','SELECT')")):
            raise RuntimeError("Identity role can read workspace accounts")


def post_google(code, verifier, nonce):
    cfg = settings()
    client_id, client_secret = oauth_client(cfg.google_oauth_client_file)
    with requests.Session() as http:
        http.trust_env = False
        response = http.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": cfg.app_origin + "/auth/google/callback",
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
            timeout=(5, 10),
            allow_redirects=False,
        )
        response.raise_for_status()
        jwt = response.json()["id_token"]
        claims = verify_oauth2_token(
            jwt,
            partial(GoogleRequest(http), timeout=8),
            audience=client_id,
            clock_skew_in_seconds=30,
        )
    if claims.get("azp", client_id) != client_id:
        raise ValueError("Invalid authorized party")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise ValueError("Invalid nonce")
    subject, email = claims.get("sub"), claims.get("email")
    if (
        not isinstance(subject, str)
        or not 0 < len(subject) <= 255
        or not isinstance(email, str)
        or not 0 < len(email) <= 320
        or claims.get("email_verified") is not True
    ):
        raise ValueError("Missing verified identity")
    return subject, email.casefold()


def admit(db, subject, email):
    cfg = settings()
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
        {
            "subject": "google:" + subject,
        },
    )
    user = db.scalar(select(User).where(User.google_subject == subject))
    if user:
        user.email = email
        return user
    is_owner = email == cfg.bootstrap_owner_email.strip().casefold()
    if is_owner:
        workspace = db.scalar(
            select(Workspace).where(Workspace.id == OWNER_WORKSPACE_ID).with_for_update()
        )
        if not workspace or workspace.owner_user_id:
            return None
    else:
        pilot = {
            item.strip().casefold() for item in cfg.signup_test_emails.split(",") if item.strip()
        }
        if not cfg.signup_open and email not in pilot:
            return None
        workspace = Workspace()
        db.add(workspace)
    user = User(google_subject=subject, email=email)
    db.add(user)
    db.flush()
    workspace.owner_user_id = user.id
    return user


def current_principal(raw):
    if not raw or not TOKEN.fullmatch(raw):
        return None
    with identity_db() as db:
        row = db.execute(
            select(User.id, Workspace.id, User.email)
            .join(WebSession, WebSession.user_id == User.id)
            .join(Workspace, Workspace.owner_user_id == User.id)
            .where(WebSession.digest == digest(raw), WebSession.expires_at > now())
        ).one_or_none()
        return Principal(*row) if row else None


def cookie(response, name, value, lifetime):
    response.set_cookie(
        name, value, max_age=lifetime, path="/", secure=True, httponly=True, samesite="lax"
    )


def clear_cookie(response, name):
    response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="lax")


def login_failure(reason):
    response = RedirectResponse("/login?" + urlencode({"error": reason}), status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    clear_cookie(response, FLOW)
    return response


@router.get("/auth/google/start")
def start(return_to: str = "/"):
    cfg = settings()
    if cfg.auth_mode != "google":
        raise HTTPException(404, "Google sign-in is unavailable")
    client_id, _ = oauth_client(cfg.google_oauth_client_file)
    state, browser, verifier, nonce = token(), token(), token(), token()
    with identity_db() as db, db.begin():
        db.execute(delete(GoogleLoginFlow).where(GoogleLoginFlow.expires_at <= now()))
        db.add(
            GoogleLoginFlow(
                digest=digest(state),
                browser_digest=digest(browser),
                verifier=verifier,
                nonce=nonce,
                return_to=local_path(return_to),
                expires_at=now() + timedelta(seconds=FLOW_SECONDS),
            )
        )
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": cfg.app_origin + "/auth/google/callback",
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
            "code_challenge": base64.urlsafe_b64encode(bytes.fromhex(digest(verifier)))
            .rstrip(b"=")
            .decode(),
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(
        "https://accounts.google.com/o/oauth2/v2/auth?" + query, status_code=303
    )
    response.headers["Cache-Control"] = "no-store"
    cookie(response, FLOW, browser, FLOW_SECONDS)
    return response


@router.get("/auth/google/callback")
def callback(request: Request, state: str = "", code: str = ""):
    if settings().auth_mode != "google":
        raise HTTPException(404, "Google sign-in is unavailable")
    browser = request.cookies.get(FLOW, "")
    if not TOKEN.fullmatch(state) or not TOKEN.fullmatch(browser) or not 0 < len(code) <= 4096:
        return login_failure("google")
    with identity_db() as db, db.begin():
        flow = db.execute(
            delete(GoogleLoginFlow)
            .where(
                GoogleLoginFlow.digest == digest(state),
                GoogleLoginFlow.browser_digest == digest(browser),
                GoogleLoginFlow.expires_at > now(),
            )
            .returning(GoogleLoginFlow.verifier, GoogleLoginFlow.nonce, GoogleLoginFlow.return_to)
        ).one_or_none()
    if not flow:
        return login_failure("google")
    try:
        subject, email = post_google(code, flow.verifier, flow.nonce)
    except (ValueError, KeyError, requests.RequestException, GoogleAuthError):
        return login_failure("google")
    with identity_db() as db, db.begin():
        user = admit(db, subject, email)
        if not user:
            return login_failure("signup_closed")
        old = request.cookies.get(SESSION, "")
        if TOKEN.fullmatch(old):
            db.execute(delete(WebSession).where(WebSession.digest == digest(old)))
        db.execute(delete(WebSession).where(WebSession.expires_at <= now()))
        raw = token()
        db.add(
            WebSession(
                digest=digest(raw),
                user_id=user.id,
                expires_at=now() + timedelta(seconds=SESSION_SECONDS),
            )
        )
    response = RedirectResponse(local_path(flow.return_to), status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    clear_cookie(response, FLOW)
    cookie(response, SESSION, raw, SESSION_SECONDS)
    return response


@router.post("/auth/logout", status_code=204)
def logout(request: Request):
    cfg = settings()
    if cfg.auth_mode != "google" or request.headers.get("origin") != cfg.app_origin:
        raise HTTPException(403, "Origin rejected")
    raw = request.cookies.get(SESSION, "")
    if TOKEN.fullmatch(raw):
        with identity_db() as db, db.begin():
            db.execute(delete(WebSession).where(WebSession.digest == digest(raw)))
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    clear_cookie(response, SESSION)
    return response


@router.get("/api/v1/me", response_model=IdentityResponse)
def me(request: Request):
    principal = getattr(request.state, "principal", None)
    return IdentityResponse(
        auth_mode=settings().auth_mode,
        email=principal.email if principal else None,
        workspace_id=principal.workspace_id if principal else None,
    )
