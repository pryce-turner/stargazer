"""Shared test helpers for creating mock data via hydration."""

from stargazer.utils.hydrate import hydrate

from stargazer.assets import Alignment, Reads, Reference, Variants
from stargazer.utils.local_storage import default_client


async def create_test_reference(build: str = "GRCh38") -> Reference:
    """Create and hydrate a mock reference.

    Args:
        build: Reference genome build (default: "GRCh38")

    Returns:
        Reference object with mock FASTA and FAIDX files
    """
    local_dir = default_client.local_dir
    local_dir.mkdir(parents=True, exist_ok=True)

    # Create FASTA
    fasta_path = local_dir / f"{build}.fa"
    fasta_path.write_text(">chr1\nGATCGATCGATC\n")
    await default_client.upload_file(
        fasta_path,
        keyvalues={"type": "reference", "component": "fasta", "build": build},
    )

    # Create FAIDX
    faidx_path = local_dir / f"{build}.fa.fai"
    faidx_path.write_text("chr1\t12\t6\t12\t13\n")
    await default_client.upload_file(
        faidx_path,
        keyvalues={"type": "reference", "component": "faidx", "build": build},
    )

    refs = await hydrate({"type": "reference", "build": build})
    return refs[0]


async def create_test_alignment(sample_id: str) -> Alignment:
    """Create and hydrate a mock alignment.

    Args:
        sample_id: Sample identifier

    Returns:
        Alignment object with mock BAM and BAI files
    """
    local_dir = default_client.local_dir
    local_dir.mkdir(parents=True, exist_ok=True)

    # Create BAM
    bam_path = local_dir / f"{sample_id}.bam"
    bam_path.write_bytes(b"BAM\x01mock_content")
    await default_client.upload_file(
        bam_path,
        keyvalues={
            "type": "alignment",
            "component": "alignment",
            "sample_id": sample_id,
        },
    )

    # Create BAI
    bai_path = local_dir / f"{sample_id}.bam.bai"
    bai_path.write_bytes(b"BAI\x01mock_index")
    await default_client.upload_file(
        bai_path,
        keyvalues={"type": "alignment", "component": "index", "sample_id": sample_id},
    )

    alignments = await hydrate({"type": "alignment", "sample_id": sample_id})
    return alignments[0]


async def create_test_reads(sample_id: str, paired: bool = True) -> Reads:
    """Create and hydrate mock reads.

    Args:
        sample_id: Sample identifier
        paired: Whether to create paired-end reads (default: True)

    Returns:
        Reads object with mock FASTQ files
    """
    local_dir = default_client.local_dir
    local_dir.mkdir(parents=True, exist_ok=True)

    # Create R1
    r1_path = local_dir / f"{sample_id}_R1.fastq"
    r1_path.write_text("@read1\nACGT\n+\nIIII\n")
    await default_client.upload_file(
        r1_path, keyvalues={"type": "reads", "component": "r1", "sample_id": sample_id}
    )

    if paired:
        # Create R2
        r2_path = local_dir / f"{sample_id}_R2.fastq"
        r2_path.write_text("@read1\nTGCA\n+\nIIII\n")
        await default_client.upload_file(
            r2_path,
            keyvalues={"type": "reads", "component": "r2", "sample_id": sample_id},
        )

    reads_list = await hydrate({"type": "reads", "sample_id": sample_id})
    return reads_list[0]


async def create_test_variants(sample_id: str) -> Variants:
    """Create and hydrate mock variants.

    Args:
        sample_id: Sample identifier

    Returns:
        Variants object with mock VCF and TBI files
    """
    local_dir = default_client.local_dir
    local_dir.mkdir(parents=True, exist_ok=True)

    # Create VCF
    vcf_path = local_dir / f"{sample_id}.vcf.gz"
    vcf_path.write_bytes(b"\x1f\x8b")  # gzip magic bytes
    await default_client.upload_file(
        vcf_path,
        keyvalues={"type": "variants", "component": "vcf", "sample_id": sample_id},
    )

    # Create TBI
    tbi_path = local_dir / f"{sample_id}.vcf.gz.tbi"
    tbi_path.write_bytes(b"TBI\x01")
    await default_client.upload_file(
        tbi_path,
        keyvalues={"type": "variants", "component": "index", "sample_id": sample_id},
    )

    variants_list = await hydrate({"type": "variants", "sample_id": sample_id})
    return variants_list[0]
