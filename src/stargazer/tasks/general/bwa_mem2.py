"""
### BWA-MEM2 tasks for reference genome indexing and alignment.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import asyncio
import shlex

import stargazer.utils.local_storage as _storage
from stargazer.assets import R1, R2, AlignerIndex, Alignment, Reference
from stargazer.config import gatk_env, logger
from stargazer.utils import _run


@gatk_env.task
async def bwa_mem2_index(ref: Reference) -> list[AlignerIndex]:
    """
    Create BWA-MEM2 index files for a reference genome.

    Creates the following index files:
    - .amb, .ann, .bwt.2bit.64, .pac, .sa

    Args:
        ref: Reference FASTA asset

    Returns:
        List of AlignerIndex assets, one per index file

    Reference:
        https://github.com/bwa-mem2/bwa-mem2
    """
    logger.info(ref.to_dict())
    await ref.fetch()
    ref_path = ref.path

    if not ref_path or not ref_path.exists():
        raise FileNotFoundError(f"Reference file not found at {ref_path}")

    index_extensions = [".0123", ".amb", ".ann", ".bwt.2bit.64", ".pac"]
    output_dir = _storage.default_client.local_dir
    base_name = ref_path.name

    prefix = output_dir / base_name
    cmd = ["bwa-mem2", "index", "-p", str(prefix), str(ref_path)]
    await _run(cmd, cwd=str(output_dir))

    indices = []
    for ext in index_extensions:
        index_path = output_dir / f"{base_name}{ext}"
        if not index_path.exists():
            raise FileNotFoundError(
                f"BWA-MEM2 index file {index_path.name} was not created"
            )

        idx = AlignerIndex()
        await idx.update(
            index_path,
            aligner="bwa-mem2",
            build=ref.build,
            reference_cid=ref.cid,
        )
        indices.append(idx)

    logger.info([x.to_dict() for x in indices])
    return indices


@gatk_env.task
async def bwa_mem2_mem(
    ref: Reference,
    r1: R1,
    r2: R2 | None = None,
    read_group: dict[str, str] | None = None,
) -> Alignment:
    """
    Align FASTQ reads to reference genome using BWA-MEM2.

    Produces an unsorted BAM file that typically needs to be sorted
    before downstream processing (e.g., with sort_sam).

    Args:
        ref: Reference FASTA asset
        r1: R1 FASTQ read asset
        r2: R2 FASTQ read asset (None for single-end)
        read_group: Optional read group override (ID, SM, LB, PL, PU)

    Returns:
        Alignment asset containing the unsorted BAM file

    Reference:
        https://github.com/bwa-mem2/bwa-mem2
    """
    logger.info(ref.to_dict())
    logger.info(r1.to_dict())
    if r2:
        logger.info(r2.to_dict())

    await ref.fetch()
    await r1.fetch()
    if r2:
        await r2.fetch()

    ref_path = ref.path
    r1_path = r1.path
    r2_path = r2.path if r2 else None
    sample_id = r1.sample_id

    if read_group:
        rg = {"ID": sample_id, "SM": sample_id}
        rg.update(read_group)
        rg_parts = [f"{k}:{v}" for k, v in rg.items()]
        rg_string = r"@RG\t" + r"\t".join(rg_parts)
    else:
        rg_string = rf"@RG\tID:{sample_id}\tSM:{sample_id}\tLB:lib\tPL:ILLUMINA"

    output_dir = _storage.default_client.local_dir
    output_bam = output_dir / f"{sample_id}_aligned.bam"

    cmd = [
        "bwa-mem2",
        "mem",
        "-R",
        rg_string,
        "-t",
        "4",
        str(ref_path),
    ]

    if r2_path:
        cmd.extend([str(r1_path), str(r2_path)])
    else:
        cmd.append(str(r1_path))

    bwa_cmd = " ".join(shlex.quote(str(c)) for c in cmd)
    samtools_cmd = f"samtools view -bS -o {output_bam} -"
    full_cmd = f"{bwa_cmd} | {samtools_cmd}"

    logger.info(f"Running: {full_cmd}")
    proc = await asyncio.create_subprocess_shell(
        full_cmd,
        cwd=str(output_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        stderr_text = stderr.decode() if stderr else ""
        stdout_text = stdout.decode() if stdout else ""
        raise RuntimeError(
            f"BWA-MEM2 failed with return code {proc.returncode}.\n"
            f"stdout: {stdout_text}\nstderr: {stderr_text}"
        )

    if not output_bam.exists():
        raise FileNotFoundError(f"BWA-MEM2 did not create output BAM at {output_bam}")

    bam = Alignment()
    await bam.update(
        output_bam,
        sample_id=sample_id,
        format="bam",
        duplicates_marked=False,
        bqsr_applied=False,
        reference_cid=ref.cid,
        r1_cid=r1.cid,
    )

    logger.info(bam.to_dict())
    return bam
