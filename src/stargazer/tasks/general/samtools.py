"""
### Samtools tasks for reference genome indexing.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets import Reference, ReferenceIndex
from stargazer.config import gatk_env, logger
from stargazer.utils import _run


@gatk_env.task
async def samtools_faidx(ref: Reference) -> ReferenceIndex:
    """
    Create a FASTA index (.fai file) using samtools faidx.

    Args:
        ref: Reference FASTA asset

    Returns:
        ReferenceIndex asset containing the .fai file
    """
    logger.info(ref.to_dict())
    await ref.fetch()
    ref_path = ref.path

    if not ref_path or not ref_path.exists():
        raise FileNotFoundError(f"Reference file not found at {ref_path}")

    output_dir = _storage.default_client.local_dir
    fai_path = output_dir / f"{ref_path.name}.fai"
    cmd = ["samtools", "faidx", str(ref_path), "--fai-idx", str(fai_path)]
    await _run(cmd, cwd=str(output_dir))

    if not fai_path.exists():
        raise FileNotFoundError(f"FASTA index file {fai_path.name} was not created")

    faidx = ReferenceIndex()
    await faidx.update(
        fai_path,
        build=ref.build,
        tool="samtools_faidx",
        reference_cid=ref.cid,
    )

    logger.info(faidx.to_dict())
    return faidx
