"""
### Stargazer: Bioinformatics workflow orchestration with IPFS and Flyte v2.

Combines IPFS decentralized storage (via Pinata) with Flyte v2 for
computational genomics pipelines. The core innovation is using IPFS Content
Identifiers (CIDs) as universal file identifiers, with query-based dataclass
hydration for bioinformatics-specific types.

spec: [docs/architecture/overview.md](../architecture/overview.md)
"""

__version__ = "0.1.0"

# Storage
from stargazer.utils.local_storage import LocalStorageClient, default_client, get_client
from stargazer.utils.pinata import PinataClient

__all__ = [
    # Storage
    "LocalStorageClient",
    "PinataClient",
    # Version
    "__version__",
    "default_client",
    "get_client",
]
