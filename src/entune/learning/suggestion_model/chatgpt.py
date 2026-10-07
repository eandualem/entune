"""Sign in with ChatGPT, for dictionary suggestions on the person's ChatGPT plan.

OpenAI's documented route for open-source, locally hosted apps
(https://developers.openai.com/siwc/token-sharing-open-source): the browser opens
OpenAI's sign-in, where the person approves Entune's use of their plan, and comes back
to Entune's own server on 127.0.0.1 with a code that is exchanged here for tokens. The
first sign-in registers Entune for that account (client ID `dynamic_agent_client`); the
ID OpenAI issues (`oaiapp_…`) is kept for every later one. Requests then go to the
public Responses API on the plan, within the limits the person sets for Entune in
ChatGPT Settings > Usage. No client secret or API key is involved.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

AUTH = "https://auth.openai.com"
AUTHORIZE = f"{AUTH}/api/accounts/authorize"
TOKEN = f"{AUTH}/api/accounts/oauth/token"
REVOKE = f"{AUTH}/api/accounts/oauth/revoke"
JWKS = f"{AUTH}/.well-known/jwks.json"
API = "https://api.openai.com/v1"  # the public Responses API, never ChatGPT's backend
DYNAMIC_CLIENT = "dynamic_agent_client"  # registers Entune for the account, once
NAME = "Entune"  # how the app is named on OpenAI's consent page and in ChatGPT's settings
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"  # consent to use the plan; sign-in alone lacks it
CALLBACK_PATH = "/auth/callback"  # on Entune's own server; only the port may differ
RENEW_WITHIN = 10 * 60  # seconds; an access token lasts an hour
ATTEMPT_SECONDS = 15 * 60  # how long a started sign-in waits for the browser
TIMEOUT = httpx.Timeout(30.0, connect=5.0)


@dataclass(frozen=True)
class Attempt:
    """A sign-in waiting for the browser to come back."""

    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    client_id: str
    expires_at: float  # epoch seconds

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Attempt | None:
        """None for a sign-in saved by an older Entune, which this flow cannot finish."""
        try:
            return cls(**json.loads(text))
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class Login:
    access_token: str
    refresh_token: str
    id_token: str
    client_id: str  # the oaiapp_ ID OpenAI issued for this account
    subject: str  # the account, from the validated ID token
    email: str | None
    expires_at: float  # epoch seconds
    earliest_refresh_at: float  # epoch seconds; OpenAI asks not to refresh before it

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Login | None:
        """None for a login saved by an older Entune (the Codex sign-in): it is not valid
        on this route, so the person signs in once more."""
        try:
            return cls(**json.loads(text))
        except (TypeError, ValueError):
            return None


def start(client_id: str | None, port: int, now: float | None = None) -> tuple[Attempt, str]:
    """A new sign-in and the OpenAI page to open for it. `client_id` is the ID issued at an
    earlier sign-in, if any; without one, this sign-in registers Entune."""
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    attempt = Attempt(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        verifier=verifier,
        redirect_uri=f"http://127.0.0.1:{port}{CALLBACK_PATH}",
        client_id=client_id or DYNAMIC_CLIENT,
        expires_at=(time.time() if now is None else now) + ATTEMPT_SECONDS,
    )
    query = {
        "client_id": attempt.client_id,
        "agent_name_hint": NAME,
        "response_type": "code",
        "redirect_uri": attempt.redirect_uri,
        "scope": SCOPES,
        "resource": API,
        "state": attempt.state,
        "nonce": attempt.nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return attempt, f"{AUTHORIZE}?{urlencode(query)}"


def finish(
    attempt: Attempt,
    query: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
    now: float | None = None,
) -> Login:
    """The login from the browser's return to the callback, once it is checked."""
    if query.get("state") != attempt.state:
        raise ValueError("This sign-in reply is not from the sign-in Entune started; sign in again")
    if (time.time() if now is None else now) > attempt.expires_at:
        raise ValueError("The sign-in took too long; sign in again")
    error = query.get("error")
    if error == "access_denied":
        raise ValueError("You declined the sign-in, so Entune cannot use your ChatGPT plan")
    if error:
        raise ValueError(
            f"OpenAI sign-in failed: {error} {query.get('error_description', '')}".strip()
        )
    code = query.get("code")
    client_id = query.get("client_id") or attempt.client_id
    if not code or client_id == DYNAMIC_CLIENT:
        raise ValueError("OpenAI's sign-in reply had no code or no issued client ID; sign in again")
    with _session(transport) as http:
        keys = _published_keys(http)
        tokens = _checked(
            http.post(
                TOKEN,
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": code,
                    "code_verifier": attempt.verifier,
                    "redirect_uri": attempt.redirect_uri,
                    "resource": API,
                },
            )
        ).json()
        return _login(tokens, client_id, attempt.nonce, None, keys, now)


def renewed(
    login: Login, transport: httpx.BaseTransport | None = None, now: float | None = None
) -> Login | None:
    """A renewed login when `login` runs out within RENEW_WITHIN, else None. A refresh
    token works once, so the caller must keep the login this returns."""
    moment = time.time() if now is None else now
    if login.expires_at - moment > RENEW_WITHIN or moment < login.earliest_refresh_at:
        return None
    with _session(transport) as http:
        # OpenAI's keys are read first: once the single-use refresh token is spent, its
        # reply must not be lost to a failure reading them.
        keys = _published_keys(http)
        tokens = _checked(
            http.post(
                TOKEN,
                data={
                    "grant_type": "refresh_token",
                    "client_id": login.client_id,
                    "refresh_token": login.refresh_token,
                    "resource": API,
                },
            )
        ).json()
        return _login(tokens, login.client_id, None, login, keys, now)


def revoke(login: Login, transport: httpx.BaseTransport | None = None) -> bool:
    """Ends the renewable session at OpenAI; False when that could not be confirmed (the
    person can still disconnect Entune in ChatGPT Settings)."""
    try:
        with _session(transport) as http:
            reply = http.post(
                REVOKE,
                data={
                    "token": login.refresh_token,
                    "token_type_hint": "refresh_token",
                    "client_id": login.client_id,
                },
            )
            return reply.status_code == 200
    except ValueError:
        return False


def models(
    access_token: str, transport: httpx.BaseTransport | None = None
) -> list[tuple[str, str]]:
    """The signed-in account's models: (slug to send, name to show), as its catalog lists."""
    with _session(transport) as http:
        body = _checked(
            http.get(f"{API}/models", headers={"Authorization": f"Bearer {access_token}"})
        ).json()
    entries = body.get("models") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        raise ValueError("OpenAI's model catalog had no models list")
    found: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("visibility", "list") != "list":
            continue
        slug = entry.get("slug")
        if isinstance(slug, str) and slug:
            name = entry.get("display_name")
            found.append((slug, name if isinstance(name, str) and name else slug))
    return found


def _login(
    tokens: dict[str, Any],
    client_id: str,
    nonce: str | None,
    before: Login | None,
    keys: list[dict[str, Any]],
    now: float | None,
) -> Login:
    moment = time.time() if now is None else now
    id_token = tokens.get("id_token")
    if isinstance(id_token, str) and id_token:
        claims = verify_id_token(id_token, client_id, nonce, keys, moment)
    elif before is not None:  # a refresh need not send one: the account stays the same
        id_token, claims = before.id_token, {"sub": before.subject, "email": before.email}
    else:
        raise ValueError("OpenAI's sign-in reply had no ID token")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ValueError("OpenAI's ID token named no account")
    if before is not None and subject != before.subject:
        raise ValueError("The renewed sign-in is for another account; sign in again")
    scope = tokens.get("scope")
    granted = scope.split() if isinstance(scope, str) else ([] if before is None else [PLAN_SCOPE])
    if PLAN_SCOPE not in granted:
        raise ValueError(
            "You signed in but did not allow Entune to use your ChatGPT plan; sign in again"
            " and approve it"
        )
    expires_in = tokens.get("expires_in")
    earliest = tokens.get("earliest_refresh_at")
    email = claims.get("email")
    return Login(
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token") or (before.refresh_token if before else ""),
        id_token=id_token,
        client_id=client_id,
        subject=subject,
        email=email if isinstance(email, str) else (before.email if before else None),
        expires_at=moment + (float(expires_in) if isinstance(expires_in, int | float) else 3600.0),
        earliest_refresh_at=float(earliest) if isinstance(earliest, int | float) else 0.0,
    )


def _published_keys(http: httpx.Client) -> list[dict[str, Any]]:
    """OpenAI's published signing keys (its JWKS)."""
    keys = _checked(http.get(JWKS)).json().get("keys", [])
    return [k for k in keys if isinstance(k, dict)] if isinstance(keys, list) else []


def verify_id_token(
    token: str, audience: str, nonce: str | None, keys: list[dict[str, Any]], now: float
) -> dict[str, Any]:
    """The ID token's claims once its RS256 signature matches one of OpenAI's published
    `keys` and its issuer, audience, expiry and (on sign-in) nonce are the expected ones."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    try:
        head, body, signature = token.split(".")
        header = json.loads(_unb64url(head))
        claims = json.loads(_unb64url(body))
        signed = _unb64url(signature)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("OpenAI's ID token could not be read") from exc
    if header.get("alg") != "RS256" or not isinstance(claims, dict):
        raise ValueError("OpenAI's ID token is not signed the documented way")
    key = next(
        (k for k in keys if k.get("kid") == header.get("kid") and k.get("kty") == "RSA"), None
    )
    if key is None:
        raise ValueError("OpenAI's ID token is signed with a key it does not publish")
    public = rsa.RSAPublicNumbers(
        int.from_bytes(_unb64url(key["e"]), "big"), int.from_bytes(_unb64url(key["n"]), "big")
    ).public_key()
    try:
        public.verify(signed, f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise ValueError("OpenAI's ID token signature does not match") from exc
    audiences = claims.get("aud")
    if claims.get("iss") != AUTH:
        raise ValueError("OpenAI's ID token has another issuer")
    if audience not in (audiences if isinstance(audiences, list) else [audiences]):
        raise ValueError("OpenAI's ID token is for another app")
    expiry = claims.get("exp")
    if not isinstance(expiry, int | float) or expiry < now:
        raise ValueError("OpenAI's ID token has expired")
    if nonce is not None and claims.get("nonce") != nonce:
        raise ValueError("OpenAI's ID token is not from this sign-in")
    return claims


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


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
