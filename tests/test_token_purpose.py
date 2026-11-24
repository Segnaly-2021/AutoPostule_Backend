"""Only a real login token may authenticate an API request.

Password-reset and unsubscribe tokens are signed with the same key as login
tokens and differ only by their `purpose` claim. An unsubscribe link lives in an
inbox for a year, so it must never work as a login.
"""
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importing the auth dependency loads the DB engine module, which requires a URL.
# The engine connects lazily and these tests never query it; port 1 is unreachable.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:1/test")

from auto_apply_app.application.use_cases.user_use_cases import UNSUBSCRIBE_TOKEN_PURPOSE
from auto_apply_app.infrastructures.api.dependencies.auth_deps import (
    CurrentUserId,
    OptionalUserId,
)
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.authentication.token_provider import JwtTokenProvider

TEST_SECRET = "test-only-secret-that-is-at-least-32-bytes"
NON_LOGIN_PURPOSES = ["password_reset", UNSUBSCRIBE_TOKEN_PURPOSE, "anything_else", "", None]


class _Blacklist:
    def __init__(self):
        self.ids = set()

    async def is_blacklisted(self, token_id):
        return token_id in self.ids


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    return JwtTokenProvider()


@pytest.fixture
def blacklist():
    return _Blacklist()


@pytest.fixture
def client(provider, blacklist):
    app = FastAPI()

    @app.get("/private")
    async def private(user_id: CurrentUserId):
        return {"user_id": user_id}

    @app.get("/public")
    async def public(user_id: OptionalUserId):
        return {"user_id": user_id}

    app.dependency_overrides[get_container] = lambda: SimpleNamespace(
        token_provider=provider, token_repo=blacklist
    )
    return TestClient(app)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _login_token(provider, user_id):
    # Same claims as the login and email-verification use cases.
    return provider.encode_token(user_id=user_id, claims={"email": "user@example.test"})


def test_login_token_is_accepted(client, provider):
    user_id = uuid4()
    response = client.get("/private", headers=_bearer(_login_token(provider, user_id)))
    assert response.status_code == 200
    assert response.json() == {"user_id": str(user_id)}


@pytest.mark.parametrize("purpose", NON_LOGIN_PURPOSES)
def test_purpose_token_cannot_authenticate(client, provider, purpose):
    token = provider.encode_token(user_id=uuid4(), claims={"purpose": purpose})
    assert client.get("/private", headers=_bearer(token)).status_code == 401


@pytest.mark.parametrize("purpose", NON_LOGIN_PURPOSES)
def test_purpose_token_is_anonymous_on_public_routes(client, provider, purpose):
    token = provider.encode_token(user_id=uuid4(), claims={"purpose": purpose})
    response = client.get("/public", headers=_bearer(token))
    assert response.status_code == 200
    assert response.json() == {"user_id": None}


def test_expired_login_token_is_rejected(client, provider):
    token = provider.encode_token(user_id=uuid4(), expires_delta=timedelta(minutes=-5))
    assert client.get("/private", headers=_bearer(token)).status_code == 401


def test_forged_token_is_rejected(client):
    forged = jwt.encode(
        {"sub": str(uuid4()), "iss": "autopostule-api", "aud": "autopostule-frontend"},
        "a-different-secret-that-is-at-least-32-bytes",
        algorithm="HS256",
    )
    assert client.get("/private", headers=_bearer(forged)).status_code == 401


def test_logged_out_token_is_rejected(client, provider, blacklist):
    token = _login_token(provider, uuid4())
    blacklist.ids.add(provider.get_token_id(token))
    assert client.get("/private", headers=_bearer(token)).status_code == 401


def test_missing_token_is_rejected(client):
    assert client.get("/private").status_code in (401, 403)


def test_public_route_keeps_working(client, provider):
    user_id = uuid4()
    assert client.get("/public").json() == {"user_id": None}
    assert client.get("/public", headers=_bearer("not-a-token")).json() == {"user_id": None}
    response = client.get("/public", headers=_bearer(_login_token(provider, user_id)))
    assert response.json() == {"user_id": str(user_id)}


@pytest.mark.parametrize("purpose", ["password_reset", UNSUBSCRIBE_TOKEN_PURPOSE])
def test_purpose_tokens_still_decode_for_their_own_flows(provider, purpose):
    user_id = uuid4()
    payload = provider.decode_token(provider.encode_token(user_id=user_id, claims={"purpose": purpose}))
    assert payload["purpose"] == purpose
    assert payload["sub"] == str(user_id)
