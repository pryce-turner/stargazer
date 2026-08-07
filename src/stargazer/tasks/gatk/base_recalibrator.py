"""
### base_recalibrator task for Stargazer.

Creates BQSR recalibration table using GATK BaseRecalibrator.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets import Alignment, BQSRReport, KnownSites, Reference
from stargazer.config import gatk_env, logger
from stargazer.utils import _run


@gatk_env.task
async def base_recalibrator(
    alignment: Alignment,
    ref: Reference,
    known_sites: list[KnownSites],
) -> BQSRReport:
    """
    Generate a Base Quality Score Recalibration report.

    Uses GATK BaseRecalibrator to analyze patterns of covariation in the
    sequence dataset and produce a recalibration table.

    Args:
        alignment: Input BAM asset (should be sorted and have duplicates marked)
        ref: Reference FASTA asset
        known_sites: List of KnownSites VCF assets (dbSNP, known indels, etc.)

    Returns:
        BQSRReport asset containing the recalibration table

    Reference:
        https://gatk.broadinstitute.org/hc/en-us/articles/360036898312-BaseRecalibrator
    """
    logger.info(alignment.to_dict())
    logger.info(ref.to_dict())
    logger.info([x.to_dict() for x in known_sites])
    if not known_sites:
        raise ValueError("known_sites list cannot be empty for BQSR")

    # fetch() auto-downloads companions (.fai, .dict, .bai, etc.)
    await alignment.fetch()
    await ref.fetch()
    for site in known_sites:
        await site.fetch()

    ref_path = ref.path
    bam_path = alignment.path
    output_dir = _storage.default_client.local_dir
    output_recal = output_dir / f"{alignment.sample_id}_bqsr.table"

    cmd = [
        "gatk",
        "BaseRecalibrator",
        "-R",
        str(ref_path),
        "-I",
        str(bam_path),
        "-O",
        str(output_recal),
    ]
    for site in known_sites:
        cmd.extend(["--known-sites", str(site.path)])

    await _run(cmd, cwd=str(output_dir))

    if not output_recal.exists():
        raise FileNotFoundError(
            f"BaseRecalibrator did not create recalibration report at {output_recal}"
        )

    report = BQSRReport()
    await report.update(
        output_recal,
        sample_id=alignment.sample_id,
        tool="gatk_base_recalibrator",
        alignment_cid=alignment.cid,
    )

    logger.info(report.to_dict())
    return report
