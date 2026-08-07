"""
### mark_duplicates task for Stargazer.

Marks duplicate reads in BAM files using GATK MarkDuplicates.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets import Alignment, AlignmentIndex, DuplicateMetrics
from stargazer.config import gatk_env, logger
from stargazer.utils import _run


@gatk_env.task
async def mark_duplicates(alignment: Alignment) -> Alignment:
    """
    Mark duplicate reads in a BAM file.

    Uses GATK MarkDuplicates to identify and tag duplicate reads that
    originated from the same DNA fragment (PCR or optical duplicates).
    Duplicates are marked with the 0x0400 SAM flag.

    Args:
        alignment: Input BAM asset (should be coordinate sorted)

    Returns:
        Alignment asset with duplicates marked

    Reference:
        https://gatk.broadinstitute.org/hc/en-us/articles/360037052812-MarkDuplicates-Picard
    """
    logger.info(alignment.to_dict())
    await alignment.fetch()
    bam_path = alignment.path
    output_dir = _storage.default_client.local_dir

    output_bam = output_dir / f"{alignment.sample_id}_marked_duplicates.bam"
    metrics_file = output_dir / f"{alignment.sample_id}_duplicate_metrics.txt"

    cmd = [
        "gatk",
        "MarkDuplicates",
        "-I",
        str(bam_path),
        "-O",
        str(output_bam),
        "-M",
        str(metrics_file),
        "--CREATE_INDEX",
        "true",
    ]

    await _run(cmd, cwd=str(output_dir))

    if not output_bam.exists():
        raise FileNotFoundError(
            f"MarkDuplicates did not create output BAM at {output_bam}"
        )

    marked_bam = Alignment()
    await marked_bam.update(
        output_bam,
        sample_id=alignment.sample_id,
        format="bam",
        sorted="coordinate",
        duplicates_marked=True,
        bqsr_applied=alignment.bqsr_applied,
        tool="gatk_mark_duplicates",
    )

    bam_index = output_dir / f"{output_bam.name}.bai"
    if bam_index.exists():
        idx = AlignmentIndex()
        await idx.update(
            bam_index,
            sample_id=alignment.sample_id,
            alignment_cid=marked_bam.cid,
        )

    if metrics_file.exists():
        metrics = DuplicateMetrics()
        await metrics.update(
            metrics_file,
            sample_id=alignment.sample_id,
            tool="gatk_mark_duplicates",
            alignment_cid=marked_bam.cid,
        )

    logger.info(marked_bam.to_dict())
    return marked_bam
