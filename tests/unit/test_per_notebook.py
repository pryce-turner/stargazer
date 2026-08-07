"""Tests for resource wiring in `per_notebook_env`."""

from app.notebook_meta import NotebookResources
from app.per_notebook import per_notebook_env


def _env(**overrides):
    """Build a per-notebook env with sensible defaults for the keyword args."""
    kwargs = {
        "slug": "demo",
        "mode": "edit",
        "notebook_path": "/workspace/x.py",
        "fork_full_name": "octocat/stargazer",
        "pod_capability": "signed-cap",
        "session_secret": "sek",
        "admin_url": "http://admin",
    }
    kwargs.update(overrides)
    return per_notebook_env(**kwargs)


def test_default_resources_preserve_legacy_request_limit():
    """With no resources, the env keeps the legacy ('2Gi','6Gi') tuple."""
    env = _env()
    assert env.resources.memory == ("2Gi", "6Gi")
    assert env.resources.cpu is None


def test_custom_resources_applied_to_env():
    """A NotebookResources spec maps onto the env's flyte.Resources."""
    env = _env(resources=NotebookResources(cpu=2, memory="4Gi"))
    assert env.resources.cpu == 2
    assert env.resources.memory == "4Gi"


def test_pod_gets_capability_not_github_token():
    """No GitHub credential reaches the pod — only the signed capability."""
    env = _env()
    assert "GITHUB_TOKEN" not in env.env_vars
    assert env.env_vars["SG_POD_TOKEN"] == "signed-cap"
    # The fork identity + admin callback URL the launch script needs are present.
    assert env.env_vars["FORK_FULL_NAME"] == "octocat/stargazer"
    assert env.env_vars["STARGAZER_ADMIN_URL"] == "http://admin"
