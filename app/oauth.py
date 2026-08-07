"""
### GitHub OAuth helpers.

Handles the authorization URL construction, code-for-token exchange,
and authenticated user profile fetch against the GitHub API. All calls
ride the shared pooled client (`app.http_client`).

spec: [docs/architecture/app.md](../docs/architecture/app.md)
"""

import urllib.parse

from app import http_client

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def github_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Build the GitHub OAuth authorization URL.

    Requests `read:user` for profile access and `public_repo` so the token
    can fork the upstream stargazer repo into the user's account and push
    workspace edits back to it.
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "read:user public_repo",
        "state": state,
    }
    return f"{GITHUB_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> str:
    """Exchange an authorization code for an access token.

    Returns the access token string. Raises ValueError on failure.
    """
    resp = await http_client.client().post(
        GITHUB_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Accept": "application/json"},
    )
    data = resp.json()

    if "access_token" not in data:
        error = data.get("error_description", data.get("error", "unknown error"))
        raise ValueError(f"GitHub token exchange failed: {error}")

    return data["access_token"]


async def get_github_user(access_token: str) -> dict:
    """Fetch the authenticated GitHub user's profile.

    Returns a dict with at least 'login' (username) and 'id' (numeric).
    """
    resp = await http_client.client().get(
        GITHUB_USER_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    resp.raise_for_status()
    return resp.json()
