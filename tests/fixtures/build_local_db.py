#!/usr/bin/env python3
"""
Build a TinyDB JSON file from the fixtures directory.

Uses LocalStorageClient.upload() to compute proper local_{md5} CIDs so the
fixtures DB stays in sync with live storage behaviour.

Run this script whenever fixtures are added or changed:
    uv run python tests/fixtures/build_local_db.py
"""

import asyncio
from pathlib import Path

from stargazer.assets.alignment import (
    Alignment,
    AlignmentIndex,
    BQSRReport,
    DuplicateMetrics,
)
from stargazer.assets.asset import Asset
from stargazer.assets.reads import R1, R2
from stargazer.assets.reference import (
    AlignerIndex,
    Reference,
    ReferenceIndex,
    SequenceDict,
)
from stargazer.assets.variants import (
    KnownSites,
    KnownSitesIndex,
    Variants,
    VariantsIndex,
)
from stargazer.utils.local_storage import LocalStorageClient

FIXTURES_DIR = Path(__file__).parent
GENERAL_DIR = FIXTURES_DIR / "general"
GATK_DIR = FIXTURES_DIR / "gatk"


async def build_db() -> None:
    """Build the TinyDB JSON file using native LocalStorageClient.upload()."""
    db_path = FIXTURES_DIR / "stargazer_local.json"

    if db_path.exists():
        db_path.unlink()

    client = LocalStorageClient(local_dir=FIXTURES_DIR)

    async def upload(asset: Asset) -> str:
        await client.upload(asset)
        name = asset.path.name if asset.path else "?"
        print(f"  added: {name} → {asset.cid}")
        return asset.cid

    # ── Reference ──────────────────────────────────────────────────────────
    ref = Reference(path=GENERAL_DIR / "GRCh38_TP53.fa", build="GRCh38")
    reference_cid = await upload(ref)

    await upload(
        ReferenceIndex(
            path=GENERAL_DIR / "GRCh38_TP53.fa.fai",
            build="GRCh38",
            reference_cid=reference_cid,
        )
    )
    await upload(
        SequenceDict(
            path=GENERAL_DIR / "GRCh38_TP53.dict",
            build="GRCh38",
            reference_cid=reference_cid,
        )
    )

    for ext in ("amb", "ann", "bwt", "pac", "sa"):
        await upload(
            AlignerIndex(
                path=GENERAL_DIR / f"GRCh38_TP53.fa.{ext}",
                build="GRCh38",
                aligner="bwa",
                reference_cid=reference_cid,
            )
        )

    # ── Reads ───────────────────────────────────────────────────────────────
    r1 = R1(path=GENERAL_DIR / "NA12829_TP53_R1.fq.gz", sample_id="NA12829")
    r1_cid = await upload(r1)

    r2 = R2(path=GENERAL_DIR / "NA12829_TP53_R2.fq.gz", sample_id="NA12829")
    r2_cid = await upload(r2)

    # Back-fill mate CIDs now that both CIDs are known
    r1.mate_cid = r2_cid
    r2.mate_cid = r1_cid
    await upload(r1)
    await upload(r2)

    # ── Known sites ─────────────────────────────────────────────────────────
    mills = KnownSites(
        path=GATK_DIR / "Mills_and_1000G_gold_standard.indels.TP53.hg38.vcf",
        build="GRCh38",
        resource_name="mills",
        training="true",
        truth="true",
        prior="12",
        vqsr_mode="INDEL",
    )
    mills_cid = await upload(mills)

    await upload(
        KnownSitesIndex(
            path=GATK_DIR / "Mills_and_1000G_gold_standard.indels.TP53.hg38.vcf.idx",
            known_sites_cid=mills_cid,
        )
    )

    # ── Alignments ──────────────────────────────────────────────────────────
    unmapped = Alignment(
        path=GATK_DIR / "NA12829_TP53_unmapped.bam",
        sample_id="NA12829",
        format="bam",
        r1_cid=r1_cid,
    )
    await upload(unmapped)

    bwa_aligned = Alignment(
        path=GATK_DIR / "NA12829_TP53_bwa_aligned.bam",
        sample_id="NA12829",
        format="bam",
        tool="bwa",
        reference_cid=reference_cid,
        r1_cid=r1_cid,
    )
    await upload(bwa_aligned)

    merged = Alignment(
        path=GATK_DIR / "NA12829_TP53_merged.bam",
        sample_id="NA12829",
        format="bam",
        tool="bwa",
        reference_cid=reference_cid,
        r1_cid=r1_cid,
    )
    merged_cid = await upload(merged)

    await upload(
        AlignmentIndex(
            path=GATK_DIR / "NA12829_TP53_merged.bai",
            sample_id="NA12829",
            alignment_cid=merged_cid,
        )
    )

    paired = Alignment(
        path=GATK_DIR / "NA12829_TP53_paired.bam",
        sample_id="NA12829",
        format="bam",
        tool="bwa",
        reference_cid=reference_cid,
        r1_cid=r1_cid,
    )
    paired_cid = await upload(paired)

    await upload(
        AlignmentIndex(
            path=GATK_DIR / "NA12829_TP53_paired.bam.bai",
            sample_id="NA12829",
            alignment_cid=paired_cid,
        )
    )

    sorted_coord = Alignment(
        path=GATK_DIR / "NA12829_TP53_sorted_coordinate.bam",
        sample_id="NA12829",
        format="bam",
        sorted="coordinate",
        tool="samtools",
        reference_cid=reference_cid,
        r1_cid=r1_cid,
    )
    sorted_cid = await upload(sorted_coord)

    await upload(
        AlignmentIndex(
            path=GATK_DIR / "NA12829_TP53_sorted_coordinate.bai",
            sample_id="NA12829",
            alignment_cid=sorted_cid,
        )
    )

    markdup = Alignment(
        path=GATK_DIR / "NA12829_TP53_markdup.bam",
        sample_id="NA12829",
        format="bam",
        sorted="coordinate",
        duplicates_marked=True,
        tool="gatk",
        reference_cid=reference_cid,
        r1_cid=r1_cid,
    )
    markdup_cid = await upload(markdup)

    await upload(
        AlignmentIndex(
            path=GATK_DIR / "NA12829_TP53_markdup.bai",
            sample_id="NA12829",
            alignment_cid=markdup_cid,
        )
    )

    await upload(
        DuplicateMetrics(
            path=GATK_DIR / "NA12829_TP53_markdup_metrics.txt",
            sample_id="NA12829",
            tool="gatk",
            alignment_cid=markdup_cid,
        )
    )

    recalibrated = Alignment(
        path=GATK_DIR / "NA12829_TP53_recalibrated.bam",
        sample_id="NA12829",
        format="bam",
        sorted="coordinate",
        duplicates_marked=True,
        bqsr_applied=True,
        tool="gatk",
        reference_cid=reference_cid,
        r1_cid=r1_cid,
    )
    recalibrated_cid = await upload(recalibrated)

    await upload(
        AlignmentIndex(
            path=GATK_DIR / "NA12829_TP53_recalibrated.bai",
            sample_id="NA12829",
            alignment_cid=recalibrated_cid,
        )
    )

    await upload(
        BQSRReport(
            path=GATK_DIR / "NA12829_TP53_bqsr.table",
            sample_id="NA12829",
            tool="gatk",
            alignment_cid=markdup_cid,
        )
    )

    # ── Variants (GVCFs) ────────────────────────────────────────────────────
    for sample_id in ("NA12829", "NA12891", "NA12892"):
        vcf = Variants(
            path=GATK_DIR / f"{sample_id}_TP53.g.vcf",
            sample_id=sample_id,
            caller="haplotypecaller",
            variant_type="gvcf",
            build="GRCh38",
        )
        vcf_cid = await upload(vcf)

        idx_path = GATK_DIR / f"{sample_id}_TP53.g.vcf.idx"
        if idx_path.exists():
            await upload(
                VariantsIndex(
                    path=idx_path,
                    sample_id=sample_id,
                    variants_cid=vcf_cid,
                )
            )

    client.db.close()
    print(f"\nWrote {db_path}")


if __name__ == "__main__":
    asyncio.run(build_db())
