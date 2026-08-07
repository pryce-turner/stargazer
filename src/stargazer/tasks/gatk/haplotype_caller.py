"""
### haplotype_caller task for Stargazer.

Calls germline SNPs and indels via local re-assembly of haplotypes using
GATK HaplotypeCaller in GVCF mode.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets import Alignment, Reference, Variants, VariantsIndex
from stargazer.config import gatk_env, logger
from stargazer.utils import _run


@gatk_env.task
async def haplotype_caller(
    alignment: Alignment,
    ref: Reference,
) -> Variants:
    """
    Call germline variants in GVCF mode using GATK HaplotypeCaller.

    Args:
        alignment: Sorted, duplicate-marked BAM asset (BQSR-recalibrated recommended)
        ref: Reference FASTA asset with sequence dictionary

    Returns:
        Variants asset containing the per-sample GVCF

    Reference:
        https://gatk.broadinstitute.org/hc/en-us/articles/360037225632-HaplotypeCaller
    """
    logger.info(alignment.to_dict())
    logger.info(ref.to_dict())
    # fetch() auto-downloads companions (.fai, .dict, .bai)
    await alignment.fetch()
    await ref.fetch()

    ref_path = ref.path
    bam_path = alignment.path
    output_dir = _storage.default_client.local_dir
    output_gvcf = output_dir / f"{alignment.sample_id}.g.vcf"

    cmd = [
        "gatk",
        "HaplotypeCaller",
        "-R",
        str(ref_path),
        "-I",
        str(bam_path),
        "-O",
        str(output_gvcf),
        "--emit-ref-confidence",
        "GVCF",
    ]

    await _run(cmd, cwd=str(output_dir))

    if not output_gvcf.exists():
        raise FileNotFoundError(
            f"HaplotypeCaller did not create output GVCF at {output_gvcf}"
        )

    gvcf = Variants()
    await gvcf.update(
        output_gvcf,
        sample_id=alignment.sample_id,
        caller="haplotype_caller",
        variant_type="gvcf",
        build=ref.build,
        sample_count=1,
        source_samples=alignment.sample_id,
    )

    # Upload implicit index file produced by GATK
    idx_path = output_dir / f"{output_gvcf.name}.idx"
    if idx_path.exists():
        vidx = VariantsIndex()
        await vidx.update(
            idx_path, sample_id=alignment.sample_id, variants_cid=gvcf.cid
        )

    logger.info(gvcf.to_dict())
    return gvcf
