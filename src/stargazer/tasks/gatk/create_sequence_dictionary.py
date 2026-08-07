"""
### GATK CreateSequenceDictionary task for reference genome.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets import Reference, SequenceDict
from stargazer.config import gatk_env, logger
from stargazer.utils import _run


@gatk_env.task
async def create_sequence_dictionary(ref: Reference) -> SequenceDict:
    """
    Create a sequence dictionary (.dict file) using GATK CreateSequenceDictionary.

    Args:
        ref: Reference FASTA asset

    Returns:
        SequenceDict asset containing the .dict file
    """
    logger.info(ref.to_dict())
    await ref.fetch()
    ref_path = ref.path

    if not ref_path or not ref_path.exists():
        raise FileNotFoundError(f"Reference file not found at {ref_path}")

    output_dir = _storage.default_client.local_dir
    dict_path = output_dir / f"{ref_path.stem}.dict"
    dict_path.unlink(missing_ok=True)
    cmd = [
        "gatk",
        "CreateSequenceDictionary",
        "-R",
        str(ref_path),
        "-O",
        str(dict_path),
    ]
    await _run(cmd, cwd=str(output_dir))

    if not dict_path.exists():
        raise FileNotFoundError(
            f"Sequence dictionary file {dict_path.name} was not created"
        )

    seq_dict = SequenceDict()
    await seq_dict.update(
        dict_path,
        build=ref.build,
        tool="gatk_CreateSequenceDictionary",
        reference_cid=ref.cid,
    )

    logger.info(seq_dict.to_dict())
    return seq_dict
