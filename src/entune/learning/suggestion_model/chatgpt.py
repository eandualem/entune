"""Sign in with a ChatGPT plan, the way the Codex CLI does, for dictionary suggestions.

OpenAI's device sign-in: Entune asks for a one-time code, the person enters it on
VERIFICATION_URL, and Entune collects the login once they approve. The login is kept
with the settings and renewed here before it runs out. Requests then go to the plan's
own endpoint, BACKEND, with the account named in a header. Adapted from
assistant-runtime's OAuth service (MIT).
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import httpx

# The Codex CLI's public OAuth client (Apache-2.0): a PKCE client with no secret.
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH = "https://auth.openai.com"
VERIFICATION_URL = f"{AUTH}/codex/device"
BACKEND = "https://chatgpt.com/backend-api/codex"
RENEW_WITHIN = 24 * 3600  # seconds; a login lasts days, a dictionary build hours
TIMEOUT = httpx.Timeout(30.0, connect=5.0)


@dataclass(frozen=True)
class DeviceCode:
    device_auth_id: str
    user_code: str
    interval: int  # seconds between checks, as OpenAI asks
    expires_at: float  # epoch seconds; after it the code cannot be approved


@dataclass(frozen=True)
class Login:
    access_token: str
    refresh_token: str
    email: str | None
    expires_at: float  # epoch seconds

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Login:
        return cls(**json.loads(text))


def start(transport: httpx.BaseTransport | None = None) -> DeviceCode:
    """Ask OpenAI for a one-time code the person enters on VERIFICATION_URL."""
    with _session(transport) as http:
        data = _checked(
            http.post(f"{AUTH}/api/accounts/deviceauth/usercode", json={"client_id": CLIENT_ID})
        ).json()
        interval = data.get("interval")  # sometimes sent as a string
        try:
            expires = datetime.fromisoformat(data["expires_at"]).timestamp()
        except (KeyError, TypeError, ValueError):
            expires = time.time() + 15 * 60  # OpenAI's codes last 15 minutes
        return DeviceCode(
            data["device_auth_id"],
            data["user_code"],
            int(interval) if str(interval).strip().isdigit() else 5,
            expires,
        )


def check(
    code: DeviceCode, transport: httpx.BaseTransport | None = None, now: float | None = None
) -> Login | None:
    """The login once the person has approved `code`; None while OpenAI still waits."""
    if (time.time() if now is None else now) > code.expires_at:
        raise ValueError("The sign-in code expired before it was approved; sign in again")
    with _session(transport) as http:
        reply = http.post(
            f"{AUTH}/api/accounts/deviceauth/token",
            json={"device_auth_id": code.device_auth_id, "user_code": code.user_code},
        )
        if reply.status_code in (403, 404):  # not approved yet
            return None
        approved = _checked(reply).json()
        tokens = _checked(
            http.post(
                f"{AUTH}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": approved["authorization_code"],
                    "redirect_uri": f"{AUTH}/deviceauth/callback",
                    "client_id": CLIENT_ID,
                    "code_verifier": approved["code_verifier"],
                },
            )
        )
        return _login(tokens.json(), None)


def renewed(
    login: Login, transport: httpx.BaseTransport | None = None, now: float | None = None
) -> Login | None:
    """A renewed login when `login` runs out within RENEW_WITHIN, else None. A refresh
    token works once, so the caller must keep the login this returns."""
    if login.expires_at - (time.time() if now is None else now) > RENEW_WITHIN:
        return None
    with _session(transport) as http:
        reply = _checked(
            http.post(
                f"{AUTH}/oauth/token",
                json={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": login.refresh_token,
                },
            )
        )
        return _login(reply.json(), login)


def account_id(access_token: str) -> str | None:
    """The ChatGPT account the token belongs to, which the plan's endpoint needs."""
    auth = _claims(access_token).get("https://api.openai.com/auth")
    value = auth.get("chatgpt_account_id") if isinstance(auth, dict) else None
    return value if isinstance(value, str) and value else None


def _login(tokens: dict[str, Any], before: Login | None) -> Login:
    access = tokens["access_token"]
    identity = _claims(tokens.get("id_token"))
    profile = identity.get("https://api.openai.com/profile")
    email = identity.get("email") or (profile.get("email") if isinstance(profile, dict) else None)
    expires = _claims(access).get("exp")
    return Login(
        access,
        tokens.get("refresh_token") or (before.refresh_token if before else ""),
        email if isinstance(email, str) else (before.email if before else None),
        float(expires) if isinstance(expires, int | float) else time.time() + RENEW_WITHIN,
    )


def _claims(token: object) -> dict[str, Any]:
    """A JWT's claims, unverified: only to read the account, email and expiry."""
    if not isinstance(token, str) or token.count(".") < 2:
        return {}
    payload = token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, binascii.Error):
        return {}
    return claims if isinstance(claims, dict) else {}


def _checked(reply: httpx.Response) -> httpx.Response:
    if reply.is_success:
        return reply
    raise ValueError(f"OpenAI sign-in failed ({reply.status_code}): {reply.text.strip()}")


@contextmanager
def _session(transport: httpx.BaseTransport | None) -> Iterator[httpx.Client]:
    """A client whose failures all read as the sign-in's own: an unreachable service and
    a reply missing a documented field raise ValueError. `transport` replaces the network
    in tests."""
    try:
        with httpx.Client(timeout=TIMEOUT, trust_env=False, transport=transport) as http:
            yield http
    except httpx.HTTPError as exc:
        raise ValueError(f"Could not reach OpenAI to sign in: {exc}") from exc
    except KeyError as exc:
        raise ValueError(f"OpenAI's sign-in reply had no {exc}") from exc
