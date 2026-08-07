"""Tests for the resource bundle system."""

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import stargazer.utils.local_storage as _storage_mod
from stargazer.bundles import _load_manifest, fetch_bundle, list_bundles
from stargazer.utils.local_storage import LocalStorageClient


@pytest.fixture
def bundle_dir(tmp_path):
    """Create a temp bundle directory with a test YAML."""
    bundle_yaml = tmp_path / "test_demo.yaml"
    bundle_yaml.write_text(
        textwrap.dedent("""\
        name: test_demo
        description: Test bundle for unit tests
        files:
          - cid: QmTestCID1
            keyvalues:
              asset: anndata
              bundle: test_demo
              sample_id: s1d1
              stage: raw
              organism: mouse
          - cid: QmTestCID2
            keyvalues:
              asset: anndata
              bundle: test_demo
              sample_id: s1d3
              stage: raw
              organism: mouse
        """)
    )
    with patch("stargazer.bundles._BUNDLE_DIR", tmp_path):
        yield tmp_path


@pytest.fixture
def local_client(tmp_path):
    """Create a LocalStorageClient pointed at a temp dir (no remote)."""
    client = LocalStorageClient(local_dir=tmp_path / "storage")
    orig = _storage_mod.default_client
    _storage_mod.default_client = client
    yield client
    _storage_mod.default_client = orig


class TestListBundles:
    """Tests for list_bundles discovery."""

    def test_discovers_yaml_files(self, bundle_dir):
        """list_bundles returns metadata from YAML files."""
        bundles = list_bundles()
        assert len(bundles) == 1
        assert bundles[0]["name"] == "test_demo"
        assert bundles[0]["description"] == "Test bundle for unit tests"
        assert bundles[0]["file_count"] == 2

    def test_empty_directory(self, tmp_path):
        """list_bundles returns empty list when no YAML files exist."""
        with patch("stargazer.bundles._BUNDLE_DIR", tmp_path):
            assert list_bundles() == []


class TestLoadManifest:
    """Tests for manifest loading."""

    def test_loads_by_name(self, bundle_dir):
        """_load_manifest finds the right YAML by name field."""
        manifest = _load_manifest("test_demo")
        assert manifest["name"] == "test_demo"
        assert len(manifest["files"]) == 2

    def test_raises_for_unknown_name(self, bundle_dir):
        """_load_manifest raises ValueError for unknown bundle name."""
        with pytest.raises(ValueError, match="not found"):
            _load_manifest("nonexistent_bundle")


class TestFetchBundle:
    """Tests for bundle hydration."""

    @pytest.mark.asyncio
    async def test_local_mode_seeds_tinydb(self, bundle_dir, local_client):
        """Without remote, manifest keyvalues are seeded into TinyDB."""
        with patch.object(local_client, "download", new_callable=AsyncMock) as mock_dl:
            mock_dl.side_effect = lambda comp, **kw: setattr(
                comp, "path", Path("/fake/path")
            )
            results = await fetch_bundle("test_demo")

        assert len(results) == 2
        assert results[0]["cid"] == "QmTestCID1"
        assert results[0]["keyvalues"]["sample_id"] == "s1d1"
        assert results[0]["keyvalues"]["bundle"] == "test_demo"

        # Verify TinyDB was populated
        from tinydb import Query

        File = Query()
        record = local_client.db.get(File.cid == "QmTestCID1")
        assert record is not None
        assert record["keyvalues"]["asset"] == "anndata"
        assert record["keyvalues"]["sample_id"] == "s1d1"

    @pytest.mark.asyncio
    async def test_remote_mode_skips_tinydb(self, bundle_dir, local_client):
        """With remote, no TinyDB writes occur — only bytes are downloaded."""
        mock_remote = AsyncMock()
        local_client.remote = mock_remote

        with patch.object(local_client, "download", new_callable=AsyncMock) as mock_dl:
            mock_dl.side_effect = lambda comp, **kw: setattr(
                comp, "path", Path("/fake/path")
            )
            results = await fetch_bundle("test_demo")

        assert len(results) == 2
        # TinyDB should be empty — no metadata writes in remote mode
        assert len(local_client.db.all()) == 0

    @pytest.mark.asyncio
    async def test_idempotent_upsert(self, bundle_dir, local_client):
        """Fetching the same bundle twice doesn't create duplicate records."""
        with patch.object(local_client, "download", new_callable=AsyncMock) as mock_dl:
            mock_dl.side_effect = lambda comp, **kw: setattr(
                comp, "path", Path("/fake/path")
            )
            await fetch_bundle("test_demo")
            await fetch_bundle("test_demo")

        # Should have exactly 2 records, not 4
        assert len(local_client.db.all()) == 2

    @pytest.mark.asyncio
    async def test_download_called_for_each_cid(self, bundle_dir, local_client):
        """Download is called for every CID regardless of mode."""
        with patch.object(local_client, "download", new_callable=AsyncMock) as mock_dl:
            mock_dl.side_effect = lambda comp, **kw: setattr(
                comp, "path", Path("/fake/path")
            )
            await fetch_bundle("test_demo")

        assert mock_dl.call_count == 2
        cids = [call.args[0].cid for call in mock_dl.call_args_list]
        assert cids == ["QmTestCID1", "QmTestCID2"]
