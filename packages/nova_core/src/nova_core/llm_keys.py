"""Per-tenant LiteLLM virtual keys (ADR-028). The key is derived, not stored: HMAC(secret, tenant_id).
The API provisions it in LiteLLM with the tenant's budget; agents derive the same key per call, so the
gateway attributes spend and enforces `max_budget` per tenant. LiteLLM keeps only a hash of it.

ponytail: rotating `llm_key_secret` re-keys every tenant at once (re-run provisioning); add per-tenant
rotation with a stored key generation only if one tenant's key ever needs revoking alone.
"""

import hashlib
import hmac

from nova_core.settings import get_settings


def tenant_key(tenant_id: str) -> str | None:
    secret = get_settings().llm_key_secret
    if not secret:
        return None  # keys off (tests, local scripts): calls use the gateway key
    return "sk-nova-t-" + hmac.new(secret.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:40]
