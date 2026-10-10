"""Token validation against a local RSA key served as JWKS (no Keycloak needed)."""

import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from nova_core.auth import AuthError, TokenVerifier

ISS = "http://kc.test/realms/nova"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch) -> TokenVerifier:
    jwk = json.loads(RSAAlgorithm.to_jwk(KEY.public_key()))
    jwk.update(kid="k1", use="sig", alg="RS256")
    v = TokenVerifier("http://kc.test/certs", ISS, "nova-api", "nova-web")
    monkeypatch.setattr(v._jwks, "fetch_data", lambda: {"keys": [jwk]})
    return v


def token(key: Any = KEY, **over: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": ISS,
        "sub": "5b0c7a7e-0000-4000-8000-000000000001",
        "aud": ["nova-api", "account"],
        "azp": "nova-web",
        "iat": now,
        "exp": now + 300,
        "email": "ops@acme.test",
        "organization": {"acme": {"id": "org-acme"}},
        "resource_access": {"nova-api": {"roles": ["ops_exec"]}},
    }
    claims.update(over)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, "RS256", headers={"kid": "k1"})


def test_valid_token(verifier: TokenVerifier) -> None:
    p = verifier.verify(token())
    assert (p.org_alias, p.org_id, p.roles) == ("acme", "org-acme", frozenset({"ops_exec"}))


def test_keycloak_mixed_org_claim(verifier: TokenVerifier) -> None:
    # real Keycloak 26.4 shape: organization scope + nova-web id mapper
    p = verifier.verify(token(organization=["acme", {"acme": {"id": "org-acme"}}]))
    assert (p.org_alias, p.org_id) == ("acme", "org-acme")


def test_alias_only_org_claim(verifier: TokenVerifier) -> None:
    p = verifier.verify(token(organization=["bolt"]))
    assert (p.org_alias, p.org_id) == ("bolt", None)


@pytest.mark.parametrize(
    "bad",
    [
        {"aud": "account"},
        {"exp": int(time.time()) - 10},
        {"iss": "http://evil/realms/nova"},
        {"azp": "some-other-client"},
        {"organization": None},
        {"organization": {"acme": {"id": "a"}, "bolt": {"id": "b"}}},
    ],
)
def test_rejected(verifier: TokenVerifier, bad: dict[str, Any]) -> None:
    with pytest.raises(AuthError):
        verifier.verify(token(**bad))


def test_wrong_signature(verifier: TokenVerifier) -> None:
    with pytest.raises(AuthError):
        verifier.verify(token(key=OTHER_KEY))


def test_platform_admin_without_org(verifier: TokenVerifier) -> None:
    p = verifier.verify(token(organization=None, resource_access={"nova-api": {"roles": ["platform_admin"]}}))
    assert p.org_alias is None and "platform_admin" in p.roles


@pytest.mark.parametrize(
    "bad",
    [
        {"aud": "account"},
        {"iss": "http://evil/realms/nova"},
        {"exp": int(time.time()) - 10},
        {"organization": None},
        "tampered",
    ],
)
def test_bad_tokens_are_401_over_http(
    verifier: TokenVerifier, monkeypatch: pytest.MonkeyPatch, bad: Any
) -> None:
    """07 M4: every rejected token is a generic 401 at the API, before any tenant lookup or authz."""
    from fastapi.testclient import TestClient

    from nova_api import deps
    from nova_api.main import app

    monkeypatch.setattr(deps, "verifier", lambda: verifier)
    if bad == "tampered":  # payload edited after signing: the signature no longer matches
        head, body, sig = token().split(".")
        claims = json.loads(jwt.utils.base64url_decode(body))
        claims["resource_access"]["nova-api"]["roles"] = ["tenant_admin"]
        body = jwt.utils.base64url_encode(json.dumps(claims).encode()).decode()
        t = f"{head}.{body}.{sig}"
    else:
        t = token(**bad)
    r = TestClient(app).get("/api/v1/me", headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 401 and r.json()["error"]["message"] == "invalid token"
