"""Derive the staging realm from the dev realm (07 M4: Keycloak MFA policy for privileged roles).

Same clients, roles and organizations as infra/keycloak/realm-nova.json, plus:
- OTP for platform_admin, tenant_admin, finance and controller: those client roles are composites of the
  realm role `mfa_required`, and the browser flow runs an OTP step when the user has it (first login
  enrolls the authenticator). Everyone else logs in as in dev (identity-first via the organization).
- no dev users or passwords; direct (password) grants off; a password policy; explicit event types.
The dev realm stays MFA-off. Writes infra/keycloak/staging/realm-nova.json (separate dir: dev imports
everything in infra/keycloak, so staging lives beside it).

Run: uv run python scripts/staging_realm.py
"""

import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "infra/keycloak/realm-nova.json"
OUT = ROOT / "infra/keycloak-staging/realm-nova.json"
PRIVILEGED = ("platform_admin", "tenant_admin", "finance", "controller")
EVENTS = [
    "LOGIN", "LOGIN_ERROR", "LOGOUT", "LOGOUT_ERROR", "CODE_TO_TOKEN", "CODE_TO_TOKEN_ERROR",
    "REFRESH_TOKEN", "REFRESH_TOKEN_ERROR", "UPDATE_TOTP", "REMOVE_TOTP", "UPDATE_PASSWORD",
    "UPDATE_CREDENTIAL", "REMOVE_CREDENTIAL", "USER_DISABLED_BY_PERMANENT_LOCKOUT",
    "USER_DISABLED_BY_TEMPORARY_LOCKOUT", "CLIENT_LOGIN", "CLIENT_LOGIN_ERROR",
]  # fmt: skip


def _exec(authenticator: str, requirement: str, priority: int, **kw: Any) -> dict[str, Any]:
    return {
        "authenticator": authenticator,
        "requirement": requirement,
        "priority": priority,
        "authenticatorFlow": False,
        "userSetupAllowed": False,
        **kw,
    }


def _sub(alias: str, requirement: str, priority: int) -> dict[str, Any]:
    return {
        "flowAlias": alias,
        "requirement": requirement,
        "priority": priority,
        "authenticatorFlow": True,
        "userSetupAllowed": False,
    }


def _flow(alias: str, executions: list[dict[str, Any]], top: bool = False) -> dict[str, Any]:
    return {
        "alias": alias,
        "providerId": "basic-flow",
        "topLevel": top,
        "builtIn": False,
        "authenticationExecutions": executions,
    }


# Keycloak 26's default browser flow (identity-first via organizations) + a role-conditioned OTP step
FLOWS = [
    _flow(
        "browser-mfa",
        [
            _exec("auth-cookie", "ALTERNATIVE", 10),
            _exec("identity-provider-redirector", "ALTERNATIVE", 25),
            _sub("browser-mfa organization", "ALTERNATIVE", 26),
            _sub("browser-mfa forms", "ALTERNATIVE", 30),
        ],
        top=True,
    ),
    _flow("browser-mfa organization", [_sub("browser-mfa conditional organization", "CONDITIONAL", 10)]),
    _flow(
        "browser-mfa conditional organization",
        [
            _exec("conditional-user-configured", "REQUIRED", 10),
            _exec("organization", "ALTERNATIVE", 20),
        ],
    ),
    _flow(
        "browser-mfa forms",
        [
            _exec("auth-username-password-form", "REQUIRED", 10),
            _sub("browser-mfa privileged otp", "CONDITIONAL", 20),
        ],
    ),
    _flow(
        "browser-mfa privileged otp",
        [
            _exec("conditional-user-role", "REQUIRED", 10, authenticatorConfig="mfa required role"),
            _exec("auth-otp-form", "REQUIRED", 20),
        ],
    ),
]


def staging(dev: dict[str, Any]) -> dict[str, Any]:
    r = copy.deepcopy(dev)
    r["users"] = [u for u in r["users"] if u["username"].startswith("service-account-")]
    for org in r.get("organizations", []):
        org["members"] = []
    r["roles"].setdefault("realm", []).append(
        {"name": "mfa_required", "description": "Privileged: OTP at every browser login (M4)"}
    )
    for role in r["roles"]["client"]["nova-api"]:
        if role["name"] in PRIVILEGED:
            role["composite"] = True
            role["composites"] = {"realm": ["mfa_required"]}
    for client in r["clients"]:
        client["directAccessGrantsEnabled"] = False  # password grants would skip the browser flow's OTP
        if client.get("redirectUris"):
            client["redirectUris"] = ["https://nova-staging.example/*"]
            client["webOrigins"] = ["https://nova-staging.example"]
        if client.get("attributes", {}).get("post.logout.redirect.uris"):
            client["attributes"]["post.logout.redirect.uris"] = "https://nova-staging.example/*"
        if client.get("secret"):
            client["secret"] = "${NOVA_STAGING_SECRET}"  # noqa: S105 (placeholder, set at import)
    r["sslRequired"] = "external"
    r["passwordPolicy"] = (
        "length(12) and upperCase(1) and lowerCase(1) and digits(1) and notUsername and passwordHistory(5)"
    )
    r.update(
        otpPolicyType="totp",
        otpPolicyAlgorithm="HmacSHA1",
        otpPolicyDigits=6,
        otpPolicyPeriod=30,
        otpPolicyLookAheadWindow=1,
        enabledEventTypes=EVENTS,
        authenticationFlows=FLOWS,
        authenticatorConfig=[
            {"alias": "mfa required role", "config": {"condUserRole": "mfa_required", "negate": "false"}}
        ],
        browserFlow="browser-mfa",
    )
    return r


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    realm = staging(json.loads(SRC.read_text(encoding="utf-8")))
    OUT.write_text(json.dumps(realm, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
