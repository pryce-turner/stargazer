"""
### apply_bqsr task for Stargazer.

Applies BQSR recalibration to BAM files using GATK ApplyBQSR.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets import Alignment, BQSRReport, Reference
from stargazer.config import gatk_env, logger
from stargazer.utils import _run


@gatk_env.task
async def apply_bqsr(
    alignment: Alignment,
    ref: Reference,
    bqsr_report: BQSRReport,
) -> Alignment:
    """
    Apply Base Quality Score Recalibration to a BAM file.

    Args:
        alignment: Input BAM asset
        ref: Reference FASTA asset
        bqsr_report: Recalibration table from base_recalibrator

    Returns:
        Alignment asset with recalibrated BAM file

    Reference:
        https://gatk.broadinstitute.org/hc/en-us/articles/360037055712-ApplyBQSR
    """
    logger.info(alignment.to_dict())
    logger.info(ref.to_dict())
    logger.info(bqsr_report.to_dict())
    # fetch() auto-downloads companions (.fai, .dict, .bai, etc.)
    await alignment.fetch()
    await ref.fetch()
    await bqsr_report.fetch()

    ref_path = ref.path
    bam_path = alignment.path
    recal_path = bqsr_report.path

    if not recal_path or not recal_path.exists():
        raise FileNotFoundError("BQSR recalibration report not found in cache.")

    output_dir = _storage.default_client.local_dir
    output_bam = output_dir / f"{alignment.sample_id}_recalibrated.bam"

    cmd = [
        "gatk",
        "ApplyBQSR",
        "-R",
        str(ref_path),
        "-I",
        str(bam_path),
        "--bqsr-recal-file",
        str(recal_path),
        "-O",
        str(output_bam),
    ]

    await _run(cmd, cwd=str(output_dir))

    if not output_bam.exists():
        raise FileNotFoundError(f"ApplyBQSR did not create output BAM at {output_bam}")

    recal_bam = Alignment()
    await recal_bam.update(
        output_bam,
        sample_id=alignment.sample_id,
        format="bam",
        sorted="coordinate",
        duplicates_marked=alignment.duplicates_marked,
        bqsr_applied=True,
        tool="gatk_apply_bqsr",
        reference_cid=ref.cid,
    )

    logger.info(recal_bam.to_dict())
    return recal_bam
