"""Tests for the asset-manager API routes (plan 20, piece 2).

Route tests use `TestClient` without the context manager so the admin
lifespan (Flyte init) never runs. Session cookies are minted with the real
`app.session` helpers. The Pinata client is swapped for a fake via the
`app.assets._pinata_client` module attribute — sign returns a canned URL,
query returns canned records, and both capture their call args.

Auth model: public-network browsing (schema, public list, public download)
is anonymous — public bytes are world-readable on IPFS anyway, and the
public listing is served from an in-process TTL cache so the admin is a
semi-static mirror, not an open proxy to the Pinata API. Private routes and
sign minting require a session.
"""

import pytest
from fastapi.testclient import TestClient

from app.admin_app import asgi_app
from app.session import SESSION_COOKIE, SessionData, create_session_cookie

SECRET = "test-session-secret"
JWT = "test-pinata-jwt"

CANNED_RECORDS = [
    {
        "cid": "bafybam",
        "name": "x.bam",
        "keyvalues": {"asset": "alignment", "_owner": "octocat"},
        "network": "public",
    },
    {
        "cid": "bafyr1",
        "name": "r1.fq",
        "keyvalues": {"asset": "r1"},
        "network": "public",
    },
]


class FakePinata:
    """Captures calls; returns canned values."""

    def __init__(self):
        self.query_calls = []
        self.sign_calls = []
        self.update_calls = []

    async def query(self, keyvalues, network=None):
        self.query_calls.append({"keyvalues": dict(keyvalues), "network": network})
        return list(CANNED_RECORDS)

    async def create_signed_upload_url(
        self, filename, keyvalues, network, max_file_size=None
    ):
        self.sign_calls.append(
            {
                "filename": filename,
                "keyvalues": dict(keyvalues),
                "network": network,
                "max_file_size": max_file_size,
            }
        )
        return "https://uploads.example/signed"

    async def update_metadata(self, cid, keyvalues, network=None):
        self.update_calls.append(
            {"cid": cid, "keyvalues": dict(keyvalues), "network": network}
        )
        return {
            "cid": cid,
            "name": "x",
            "keyvalues": dict(keyvalues),
            "network": network,
        }

    async def _get_signed_url(self, cid, expires=300):
        return f"https://gw.example/signed/{cid}"


@pytest.fixture
def fake_pinata(monkeypatch):
    """Swap the route-level Pinata client, reset the public cache, set env."""
    import app.assets as assets_mod

    fake = FakePinata()
    monkeypatch.setattr(assets_mod, "_pinata_client", fake)
    monkeypatch.setattr(assets_mod, "_public_cache", None)
    monkeypatch.setenv("PINATA_JWT", JWT)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    return fake


@pytest.fixture
def client():
    return TestClient(asgi_app, follow_redirects=False)


def _auth(client: TestClient, username: str = "octocat") -> None:
    data = SessionData(username, 123)
    client.cookies.set(SESSION_COOKIE, create_session_cookie(data, SECRET))


class TestAuth:
    """Anonymous: public browsing OK; private + sign require a session."""

    def test_anonymous_can_read_schema(self, fake_pinata, client):
        assert client.get("/assets/schema").status_code == 200

    def test_anonymous_can_list_public(self, fake_pinata, client):
        resp = client.get("/assets/list", params={"network": "public"})
        assert resp.status_code == 200

    def test_anonymous_can_download_public(self, fake_pinata, client, monkeypatch):
        monkeypatch.setenv("PINATA_GATEWAY", "https://gw.example")
        resp = client.get("/assets/download/bafy123", params={"network": "public"})
        assert resp.status_code == 302

    def test_anonymous_private_list_401(self, fake_pinata, client):
        assert client.get("/assets/list").status_code == 401
        assert (
            client.get("/assets/list", params={"network": "private"}).status_code == 401
        )

    def test_anonymous_private_download_401(self, fake_pinata, client):
        assert client.get("/assets/download/bafy123").status_code == 401

    def test_anonymous_sign_401(self, fake_pinata, client):
        resp = client.post(
            "/assets/sign",
            json={"filename": "x", "keyvalues": {"asset": "alignment"}},
        )
        assert resp.status_code == 401


class TestSchema:
    def test_schema_lists_registered_assets_with_fields(self, fake_pinata, client):
        resp = client.get("/assets/schema")
        assert resp.status_code == 200
        schema = resp.json()
        fields = {f["name"]: f for f in schema["alignment"]}
        assert fields["sample_id"]["type"] == "str"
        assert fields["duplicates_marked"]["type"] == "bool"
        # Base plumbing fields never reach the form.
        assert "cid" not in fields
        assert "path" not in fields
        assert "keyvalues" not in fields


class TestPrivateList:
    def test_injects_owner_filter(self, fake_pinata, client):
        _auth(client)
        resp = client.get("/assets/list", params={"network": "private", "build": "X"})
        assert resp.status_code == 200
        assert resp.json() == CANNED_RECORDS
        call = fake_pinata.query_calls[0]
        assert call["network"] == "private"
        assert call["keyvalues"] == {"build": "X", "_owner": "octocat"}

    def test_overrides_client_supplied_owner(self, fake_pinata, client):
        """Fail closed: the session, never the query string, decides _owner."""
        _auth(client)
        client.get("/assets/list", params={"network": "private", "_owner": "mallory"})
        assert fake_pinata.query_calls[0]["keyvalues"]["_owner"] == "octocat"

    def test_network_defaults_to_private(self, fake_pinata, client):
        _auth(client)
        client.get("/assets/list")
        call = fake_pinata.query_calls[0]
        assert call["network"] == "private"
        assert call["keyvalues"] == {"_owner": "octocat"}


class TestPublicList:
    def test_serves_full_listing_from_cache(self, fake_pinata, client):
        """One unfiltered Pinata query feeds repeated public list calls."""
        first = client.get("/assets/list", params={"network": "public"})
        second = client.get("/assets/list", params={"network": "public"})
        assert first.json() == CANNED_RECORDS
        assert second.json() == CANNED_RECORDS
        assert len(fake_pinata.query_calls) == 1
        call = fake_pinata.query_calls[0]
        assert call["network"] == "public"
        assert call["keyvalues"] == {}

    def test_filters_apply_in_memory(self, fake_pinata, client):
        resp = client.get("/assets/list", params={"network": "public", "asset": "r1"})
        assert [r["cid"] for r in resp.json()] == ["bafyr1"]
        # Filtering happened app-side, not via Pinata params.
        assert fake_pinata.query_calls[0]["keyvalues"] == {}


class TestSign:
    def test_registered_key_returns_url_and_stamped_keyvalues(
        self, fake_pinata, client
    ):
        _auth(client)
        resp = client.post(
            "/assets/sign",
            json={
                "filename": "x.bam",
                "keyvalues": {"asset": "alignment", "sample_id": "S1"},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["url"] == "https://uploads.example/signed"
        assert body["keyvalues"]["_owner"] == "octocat"
        assert body["keyvalues"]["asset"] == "alignment"
        call = fake_pinata.sign_calls[0]
        assert call["filename"] == "x.bam"
        assert call["network"] == "private"
        assert call["keyvalues"]["_owner"] == "octocat"
        assert call["max_file_size"] is not None

    def test_unregistered_key_signs_verbatim(self, fake_pinata, client):
        _auth(client)
        resp = client.post(
            "/assets/sign",
            json={
                "filename": "sheet.tsv",
                "keyvalues": {"asset": "never_registered_key", "lanes": "4"},
                "network": "public",
            },
        )
        assert resp.status_code == 200
        call = fake_pinata.sign_calls[0]
        assert call["network"] == "public"
        assert call["keyvalues"]["lanes"] == "4"
        assert call["keyvalues"]["_owner"] == "octocat"

    def test_validation_errors_are_400(self, fake_pinata, client):
        _auth(client)
        no_asset = client.post(
            "/assets/sign", json={"filename": "x", "keyvalues": {"a": "1"}}
        )
        assert no_asset.status_code == 400
        assert "asset" in no_asset.json()["detail"]

        unknown = client.post(
            "/assets/sign",
            json={
                "filename": "x.bam",
                "keyvalues": {"asset": "alignment", "flowcell": "X"},
            },
        )
        assert unknown.status_code == 400
        assert "Unknown keys" in unknown.json()["detail"]

        reserved = client.post(
            "/assets/sign",
            json={
                "filename": "x.bam",
                "keyvalues": {"asset": "alignment", "_owner": "me"},
            },
        )
        assert reserved.status_code == 400
        assert fake_pinata.sign_calls == []

    def test_missing_filename_400(self, fake_pinata, client):
        _auth(client)
        resp = client.post("/assets/sign", json={"keyvalues": {"asset": "alignment"}})
        assert resp.status_code == 400


class TestUpdate:
    """`POST /assets/update` — owner-only metadata merge (plan 21 follow-up)."""

    def _post(self, client, **body):
        return client.post("/assets/update", json=body)

    def test_owner_can_update(self, fake_pinata, client):
        _auth(client, "octocat")  # owns CANNED bafybam
        resp = self._post(
            client,
            cid="bafybam",
            network="public",
            keyvalues={"asset": "alignment", "sample_id": "S2"},
        )
        assert resp.status_code == 200
        call = fake_pinata.update_calls[0]
        assert call["cid"] == "bafybam"
        # _owner re-stamped from the session, after validation.
        assert call["keyvalues"]["_owner"] == "octocat"
        assert call["keyvalues"]["sample_id"] == "S2"

    def test_non_owner_403(self, fake_pinata, client):
        _auth(client, "mallory")  # bafybam is owned by octocat
        resp = self._post(
            client, cid="bafybam", network="public", keyvalues={"asset": "alignment"}
        )
        assert resp.status_code == 403
        assert fake_pinata.update_calls == []

    def test_unowned_record_403(self, fake_pinata, client):
        """Fail closed: an unowned record (CANNED bafyr1) edits for nobody."""
        _auth(client, "octocat")
        resp = self._post(
            client, cid="bafyr1", network="public", keyvalues={"asset": "r1"}
        )
        assert resp.status_code == 403
        assert fake_pinata.update_calls == []

    def test_unknown_cid_403(self, fake_pinata, client):
        _auth(client, "octocat")
        resp = self._post(
            client, cid="nope", network="public", keyvalues={"asset": "alignment"}
        )
        assert resp.status_code == 403

    def test_anonymous_401(self, fake_pinata, client):
        resp = self._post(
            client, cid="bafybam", network="public", keyvalues={"asset": "alignment"}
        )
        assert resp.status_code == 401

    def test_validation_400(self, fake_pinata, client):
        _auth(client, "octocat")
        # Owns the record, but the patch is invalid → 400 (ownership checked first).
        missing = self._post(
            client, cid="bafybam", network="public", keyvalues={"x": "1"}
        )
        assert missing.status_code == 400
        reserved = self._post(
            client,
            cid="bafybam",
            network="public",
            keyvalues={"asset": "alignment", "_owner": "me"},
        )
        assert reserved.status_code == 400
        assert fake_pinata.update_calls == []

    def test_missing_cid_400(self, fake_pinata, client):
        _auth(client, "octocat")
        assert self._post(client, keyvalues={"asset": "alignment"}).status_code == 400

    def test_not_configured_503(self, fake_pinata, client, monkeypatch):
        monkeypatch.delenv("PINATA_JWT", raising=False)
        _auth(client, "octocat")
        resp = self._post(
            client, cid="bafybam", network="public", keyvalues={"asset": "alignment"}
        )
        assert resp.status_code == 503


class TestDownload:
    def test_private_redirects_to_signed_url(self, fake_pinata, client):
        _auth(client)
        resp = client.get("/assets/download/bafy123", params={"network": "private"})
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://gw.example/signed/bafy123"

    def test_public_authed_redirects_to_dedicated_gateway(
        self, fake_pinata, client, monkeypatch
    ):
        monkeypatch.setenv("PINATA_GATEWAY", "https://gw.example")
        _auth(client)
        resp = client.get("/assets/download/bafy123", params={"network": "public"})
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://gw.example/ipfs/bafy123"

    def test_public_anonymous_redirects_to_public_gateway(
        self, fake_pinata, client, monkeypatch
    ):
        """Split-gateway policy: anonymous downloads never spend metered
        dedicated-gateway bandwidth, whatever PINATA_GATEWAY is set to."""
        monkeypatch.setenv("PINATA_GATEWAY", "https://gw.example")
        resp = client.get("/assets/download/bafy123", params={"network": "public"})
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://dweb.link/ipfs/bafy123"


class TestNotConfigured:
    def test_routes_503_without_jwt(self, fake_pinata, client, monkeypatch):
        monkeypatch.delenv("PINATA_JWT", raising=False)
        _auth(client)
        assert client.get("/assets/list").status_code == 503
        assert (
            client.get("/assets/list", params={"network": "public"}).status_code == 503
        )
        resp = client.post(
            "/assets/sign",
            json={"filename": "x.bam", "keyvalues": {"asset": "alignment"}},
        )
        assert resp.status_code == 503


class TestPage:
    """`GET /assets` renders assets.html (plan 21, piece 3)."""

    def test_authed_gets_private_tab_and_upload(self, fake_pinata, client):
        _auth(client)
        resp = client.get("/assets")
        assert resp.status_code == 200
        html = resp.text
        # Private tab + upload panel only show for a session.
        assert 'data-network="private"' in html
        assert 'id="upload-panel"' in html
        # The page knows who it's for (drives the "Mine" filter + optimistic row).
        assert 'USERNAME = "octocat"' in html
        assert "AUTHED = true" in html

    def test_anonymous_gets_public_only(self, fake_pinata, client):
        resp = client.get("/assets")
        assert resp.status_code == 200
        html = resp.text
        # No private tab, no upload panel, a sign-in link instead.
        assert 'data-network="private"' not in html
        assert 'id="upload-panel"' not in html
        assert "/auth/login" in html
        assert "AUTHED = false" in html

    def test_not_configured_renders_notice(self, fake_pinata, client, monkeypatch):
        monkeypatch.delenv("PINATA_JWT", raising=False)
        _auth(client)
        resp = client.get("/assets")
        assert resp.status_code == 200
        assert "Pinata not configured" in resp.text
        # Controls/upload are suppressed without a backing store.
        assert 'id="upload-panel"' not in resp.text
