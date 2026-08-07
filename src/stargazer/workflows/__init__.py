"""
### Flyte workflows for genomics pipelines.

spec: [docs/architecture/workflows.md](../architecture/workflows.md)
"""

from stargazer.workflows.gatk_data_preprocessing import (
    prepare_reference,
    preprocess_sample,
)
from stargazer.workflows.germline_short_variant_discovery import (
    germline_short_variant_discovery,
)
from stargazer.workflows.scrna_clustering import scrna_clustering_pipeline

__all__ = [
    # GATK Best Practices germline workflows
    "germline_short_variant_discovery",
    # GATK Best Practices data preprocessing workflows
    "prepare_reference",
    "preprocess_sample",
    # scRNA-seq clustering
    "scrna_clustering_pipeline",
]
