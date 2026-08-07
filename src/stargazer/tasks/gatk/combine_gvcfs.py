"""
### CombineGVCFs task for Stargazer.

Combines multiple per-sample GVCFs into a single multi-sample GVCF
for joint genotyping using GATK CombineGVCFs.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

from pathlib import Path

import stargazer.utils.local_storage as _storage
from stargazer.assets import Reference, Variants, VariantsIndex
from stargazer.config import gatk_env, logger
from stargazer.utils import _run


@gatk_env.task
async def combine_gvcfs(
    gvcfs: list[Variants],
    ref: Reference,
    cohort_id: str = "cohort",
) -> Variants:
    """
    Combine multiple per-sample GVCFs into a single multi-sample GVCF.

    Args:
        gvcfs: List of Variants assets, each containing a GVCF from a single sample
        ref: Reference FASTA asset
        cohort_id: Identifier for the combined cohort (default: "cohort")

    Returns:
        Variants asset with combined multi-sample GVCF

    Reference:
        https://gatk.broadinstitute.org/hc/en-us/articles/360037053272-CombineGVCFs
    """
    logger.info([x.to_dict() for x in gvcfs])
    logger.info(ref.to_dict())
    if not gvcfs:
        raise ValueError("gvcfs list cannot be empty")

    for i, gvcf in enumerate(gvcfs):
        if gvcf.variant_type != "gvcf":
            raise ValueError(
                f"combine_gvcfs requires GVCF files, but gvcfs[{i}] is not a GVCF. "
                f"sample_id={gvcf.sample_id}"
            )

    # fetch() auto-downloads companions (.fai, .dict for ref; .idx for each gvcf)
    await ref.fetch()

    gvcf_paths: list[Path] = []
    sample_ids: list[str] = []

    for gvcf in gvcfs:
        await gvcf.fetch()
        gvcf_paths.append(gvcf.path)
        sample_ids.append(gvcf.sample_id)

    ref_path = ref.path
    output_dir = _storage.default_client.local_dir
    output_gvcf = output_dir / f"{cohort_id}_combined.g.vcf"

    cmd = [
        "gatk",
        "CombineGVCFs",
        "-R",
        str(ref_path),
        "-O",
        str(output_gvcf),
    ]

    for gvcf_path in gvcf_paths:
        cmd.extend(["-V", str(gvcf_path)])

    _stdout, stderr = await _run(cmd, cwd=str(output_dir))

    if not output_gvcf.exists():
        raise FileNotFoundError(
            f"CombineGVCFs did not create output GVCF at {output_gvcf}. "
            f"stderr: {stderr}"
        )

    combined_vcf = Variants()
    await combined_vcf.update(
        output_gvcf,
        sample_id=cohort_id,
        caller="combine_gvcfs",
        variant_type="gvcf",
        build=ref.build,
        sample_count=len(sample_ids),
        source_samples=sample_ids,
    )

    # Upload implicit index file produced by GATK
    idx_path = output_dir / f"{output_gvcf.name}.idx"
    if idx_path.exists():
        vidx = VariantsIndex()
        await vidx.update(idx_path, sample_id=cohort_id, variants_cid=combined_vcf.cid)

    logger.info(combined_vcf.to_dict())
    return combined_vcf
