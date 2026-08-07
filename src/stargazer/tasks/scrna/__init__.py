"""
### scRNA-seq tasks for Stargazer.

spec: [docs/workflows/scrna.md](../workflows/scrna.md)
"""

from stargazer.tasks.scrna.cluster import cluster
from stargazer.tasks.scrna.find_markers import find_markers
from stargazer.tasks.scrna.normalize import normalize
from stargazer.tasks.scrna.qc_filter import qc_filter
from stargazer.tasks.scrna.reduce_dimensions import reduce_dimensions
from stargazer.tasks.scrna.select_features import select_features

__all__ = [
    "cluster",
    "find_markers",
    "normalize",
    "qc_filter",
    "reduce_dimensions",
    "select_features",
]
