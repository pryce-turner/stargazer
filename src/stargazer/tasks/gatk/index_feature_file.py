"""
### GATK IndexFeatureFile task for indexing VCF files.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets import KnownSites, KnownSitesIndex
from stargazer.config import gatk_env, logger
from stargazer.utils import _run


@gatk_env.task
async def index_feature_file(known_sites: KnownSites) -> KnownSitesIndex:
    """
    Index a VCF file using GATK IndexFeatureFile.

    Required by tools like BaseRecalibrator that need random-access queries
    over known sites VCFs.

    Args:
        known_sites: KnownSites VCF asset to index

    Returns:
        KnownSitesIndex asset pointing to the generated .idx file
    """
    logger.info(known_sites.to_dict())
    await known_sites.fetch()

    output_dir = _storage.default_client.local_dir
    vcf_path = known_sites.path
    idx_path = vcf_path.with_suffix(vcf_path.suffix + ".idx")

    if not idx_path.exists():
        await _run(
            ["gatk", "IndexFeatureFile", "-I", str(vcf_path)],
            cwd=str(output_dir),
        )

    if not idx_path.exists():
        raise FileNotFoundError(f"IndexFeatureFile did not create index at {idx_path}")

    index = KnownSitesIndex()
    await index.update(idx_path, known_sites_cid=known_sites.cid)

    logger.info(index.to_dict())
    return index
