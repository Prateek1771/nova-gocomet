"""07 M4: the staging realm (scripts/staging_realm.py) imports into real Keycloak with an OTP step for
privileged roles: the browser flow is bound, the role condition follows the composite, dev users and
password grants are gone."""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from testcontainers.community.keycloak import KeycloakContainer

from .conftest import ROOT

REALM = ROOT / "infra/keycloak-staging/realm-nova.json"


@pytest.fixture(scope="module")
def kc() -> Iterator[tuple[str, httpx.Client]]:
    c = (
        KeycloakContainer("quay.io/keycloak/keycloak:26.4")
        .with_realm_import_file(str(REALM))
        .with_env("NOVA_STAGING_SECRET", "staging-test-secret")
    )
    with c:
        base = c.get_url().rstrip("/")
        token = httpx.post(
            f"{base}/realms/master/protocol/openid-connect/token",
            data={"grant_type": "password", "client_id": "admin-cli", "username": "test", "password": "test"},
        ).json()["access_token"]
        with httpx.Client(
            base_url=f"{base}/admin/realms/nova", headers={"Authorization": f"Bearer {token}"}
        ) as a:
            yield base, a


def _user_with(a: httpx.Client, name: str, role: str) -> str:
    a.post("/users", json={"username": name, "enabled": True}).raise_for_status()
    uid: str = a.get("/users", params={"username": name, "exact": "true"}).json()[0]["id"]
    client = a.get("/clients", params={"clientId": "nova-api"}).json()[0]["id"]
    r: dict[str, Any] = a.get(f"/clients/{client}/roles/{role}").json()
    a.post(f"/users/{uid}/role-mappings/clients/{client}", json=[r]).raise_for_status()
    return uid


def test_browser_flow_has_role_conditioned_otp(kc: tuple[str, httpx.Client]) -> None:
    _, a = kc
    realm = a.get("").json()
    assert realm["browserFlow"] == "browser-mfa" and realm["otpPolicyType"] == "totp"
    steps = {
        (e.get("providerId"), e["requirement"])
        for e in a.get("/authentication/flows/browser-mfa/executions").json()
    }
    assert ("conditional-user-role", "REQUIRED") in steps and ("auth-otp-form", "REQUIRED") in steps
    assert ("organization", "ALTERNATIVE") in steps  # identity-first login kept


@pytest.mark.parametrize(
    ("role", "mfa"), [("finance", True), ("tenant_admin", True), ("controller", True), ("ops_exec", False)]
)
def test_privileged_roles_require_mfa(kc: tuple[str, httpx.Client], role: str, mfa: bool) -> None:
    _, a = kc
    uid = _user_with(a, f"u-{role}", role)
    effective = {r["name"] for r in a.get(f"/users/{uid}/role-mappings/realm/composite").json()}
    assert ("mfa_required" in effective) == mfa


def test_no_dev_users_and_no_password_grant(kc: tuple[str, httpx.Client]) -> None:
    base, a = kc
    assert not a.get("/users", params={"username": "ops@acme", "exact": "true"}).json()
    r = httpx.post(
        f"{base}/realms/nova/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "nova-web",
            "client_secret": "staging-test-secret",
            "username": "u-finance",
            "password": "x",
        },
    )
    assert r.status_code in (400, 401) and r.json()["error"] == "unauthorized_client"
