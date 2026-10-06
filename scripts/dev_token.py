"""Print a dev access token for Swagger (http://localhost:8100/api/docs → Authorize) or curl.

    uv run python scripts/dev_token.py ops@acme        # password defaults to "dev"

Dev only: uses the password grant that realm-nova.json enables on nova-web for local testing.
"""

import json
import sys
import urllib.parse
import urllib.request

ISSUER = "http://localhost:8180/realms/nova"


def token(user: str, password: str = "dev") -> str:
    body = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "nova-web",
            "client_secret": "nova-web-dev-secret",
            "username": user,
            "password": password,
            "scope": "openid organization",
        }
    ).encode()
    with urllib.request.urlopen(f"{ISSUER}/protocol/openid-connect/token", body) as r:  # noqa: S310 (fixed URL)
        access: str = json.load(r)["access_token"]
        return access


if __name__ == "__main__":
    print(token(*sys.argv[1:3]) if len(sys.argv) > 1 else sys.exit(__doc__))
