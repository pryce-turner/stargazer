"""
### sort_sam task for Stargazer.

Sorts BAM files using GATK SortSam.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets import Alignment, AlignmentIndex
from stargazer.config import gatk_env, logger
from stargazer.utils import _run


@gatk_env.task
async def sort_sam(
    alignment: Alignment,
    sort_order: str = "coordinate",
) -> Alignment:
    """
    Sort a SAM/BAM file.

    Args:
        alignment: Input BAM asset to sort
        sort_order: Sort order - one of "coordinate", "queryname", "duplicate"

    Returns:
        Alignment asset with sorted BAM file

    Reference:
        https://gatk.broadinstitute.org/hc/en-us/articles/360037056932-SortSam-Picard
    """
    logger.info(alignment.to_dict())
    valid_sort_orders = ["coordinate", "queryname", "duplicate"]
    if sort_order not in valid_sort_orders:
        raise ValueError(
            f"Invalid sort_order: {sort_order}. Must be one of {valid_sort_orders}"
        )

    await alignment.fetch()
    bam_path = alignment.path
    output_dir = _storage.default_client.local_dir
    output_bam = output_dir / f"{alignment.sample_id}_sorted_{sort_order}.bam"

    cmd = [
        "gatk",
        "SortSam",
        "-I",
        str(bam_path),
        "-O",
        str(output_bam),
        "--SORT_ORDER",
        sort_order,
    ]

    if sort_order == "coordinate":
        cmd.extend(["--CREATE_INDEX", "true"])

    await _run(cmd, cwd=str(output_dir))

    if not output_bam.exists():
        raise FileNotFoundError(f"SortSam did not create output BAM at {output_bam}")

    sorted_bam = Alignment()
    await sorted_bam.update(
        output_bam,
        sample_id=alignment.sample_id,
        format="bam",
        sorted=sort_order,
        duplicates_marked=alignment.duplicates_marked,
        bqsr_applied=alignment.bqsr_applied,
        tool="gatk_sort_sam",
    )

    if sort_order == "coordinate":
        bam_index = output_dir / f"{output_bam.name}.bai"
        if bam_index.exists():
            idx = AlignmentIndex()
            await idx.update(
                bam_index,
                sample_id=alignment.sample_id,
                alignment_cid=sorted_bam.cid,
            )

    logger.info(sorted_bam.to_dict())
    return sorted_bam
