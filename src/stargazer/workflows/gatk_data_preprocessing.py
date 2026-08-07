"""
### GATK Best Practices: Data Pre-processing for Variant Discovery

Implements:
1. Reference preparation — FASTA index, sequence dictionary, BWA index
2. Sample preprocessing — align, sort, mark duplicates, BQSR

References:
    - https://gatk.broadinstitute.org/hc/en-us/articles/360035535912-Data-pre-processing-for-variant-discovery

spec: [docs/architecture/workflows.md](../architecture/workflows.md)
"""

from stargazer.assets import R1, R2, Alignment, Reference
from stargazer.assets.asset import assemble
from stargazer.config import gatk_env, log_execution
from stargazer.tasks import (
    bwa_mem2_index,
    bwa_mem2_mem,
    create_sequence_dictionary,
    mark_duplicates,
    samtools_faidx,
    sort_sam,
)


@gatk_env.task
async def prepare_reference(build: str) -> Reference:
    """
    Prepare reference genome for alignment and variant calling.

    Assembles the reference FASTA from storage and creates necessary indices:
    1. FASTA index (samtools faidx)
    2. BWA index (bwa index)

    All indices are uploaded to storage as side-effects.

    Args:
        build: Reference genome build identifier (e.g. "GRCh38")

    Returns:
        Reference asset (FASTA file)
    """
    log_execution()
    assets = await assemble(build=build, asset="reference")
    refs = [a for a in assets if isinstance(a, Reference)]
    if not refs:
        raise ValueError(f"No reference found for build={build!r}")
    ref = refs[0]

    await samtools_faidx(ref)
    await create_sequence_dictionary(ref)
    await bwa_mem2_index(ref)

    return ref


@gatk_env.task
async def preprocess_sample(
    build: str,
    sample_id: str,
) -> Alignment:
    """
    Pre-process a single sample's reads for variant calling.

    Assembles reference and reads from storage, then runs:
    1. BWA-MEM alignment
    2. Coordinate sort (GATK SortSam)
    3. Mark duplicates (GATK MarkDuplicates)

    Args:
        build: Reference genome build identifier
        sample_id: Sample identifier used to query reads

    Returns:
        Alignment asset with the preprocessed BAM file
    """
    log_execution()
    # Assemble reference
    ref_assets = await assemble(build=build, asset="reference")
    refs = [a for a in ref_assets if isinstance(a, Reference)]
    if not refs:
        raise ValueError(f"No reference found for build={build!r}")
    ref = refs[0]

    # Assemble reads
    read_assets = await assemble(sample_id=sample_id, asset=["r1", "r2"])
    r1_list = [a for a in read_assets if isinstance(a, R1)]
    if not r1_list:
        raise ValueError(f"No R1 reads found for sample_id={sample_id!r}")
    r1 = r1_list[0]
    r2_list = [a for a in read_assets if isinstance(a, R2)]
    r2 = r2_list[0] if r2_list else None

    # Alignment pipeline — tasks call fetch() internally
    alignment = await bwa_mem2_mem(ref=ref, r1=r1, r2=r2)
    alignment = await sort_sam(alignment=alignment, sort_order="coordinate")
    alignment = await mark_duplicates(alignment=alignment)

    return alignment
