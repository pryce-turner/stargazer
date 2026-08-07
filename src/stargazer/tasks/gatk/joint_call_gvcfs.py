"""
### joint_call_gvcfs task for Stargazer.

Consolidates per-sample GVCFs into a GenomicsDB datastore and performs joint
genotyping in a single task, avoiding the need to persist the GenomicsDB
workspace between tasks.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import tempfile
from pathlib import Path

import stargazer.utils.local_storage as _storage
from stargazer.assets import Reference, Variants, VariantsIndex
from stargazer.config import gatk_env, logger
from stargazer.utils import _run


@gatk_env.task
async def joint_call_gvcfs(
    gvcfs: list[Variants],
    ref: Reference,
    intervals: list[str],
    cohort_id: str = "cohort",
) -> Variants:
    """
    Consolidate GVCFs into GenomicsDB and joint-genotype in a single task.

    Runs GenomicsDBImport followed immediately by GenotypeGVCFs within the same
    execution context, so the workspace never needs to leave the pod.

    Args:
        gvcfs: Per-sample GVCF Variants assets from HaplotypeCaller
        ref: Reference FASTA asset
        intervals: Genomic intervals to process (required by GenomicsDBImport)
        cohort_id: Sample ID label for the output VCF (default: "cohort")

    Returns:
        Joint-genotyped Variants asset (VCF)

    Reference:
        https://gatk.broadinstitute.org/hc/en-us/articles/360035535932
    """
    logger.info([x.to_dict() for x in gvcfs])
    logger.info(ref.to_dict())
    if not gvcfs:
        raise ValueError("At least one GVCF must be provided")
    for gvcf in gvcfs:
        if gvcf.variant_type != "gvcf":
            raise ValueError(
                f"All inputs must be GVCFs, got variant_type={gvcf.variant_type!r}: "
                f"sample_id={gvcf.sample_id}"
            )

    await ref.fetch()
    for gvcf in gvcfs:
        await gvcf.fetch()

    output_dir = _storage.default_client.local_dir

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / f"{cohort_id}_genomicsdb"

        # Write sample map
        sample_map = Path(tmpdir) / "sample_map.txt"
        with open(sample_map, "w") as f:
            for gvcf in gvcfs:
                f.write(f"{gvcf.sample_id}\t{gvcf.path}\n")

        # GenomicsDBImport
        import_cmd = [
            "gatk",
            "GenomicsDBImport",
            "--genomicsdb-workspace-path",
            str(workspace),
            "--sample-name-map",
            str(sample_map),
        ]
        for interval in intervals:
            import_cmd.extend(["-L", interval])

        await _run(import_cmd, cwd=tmpdir)

        if not workspace.exists():
            raise FileNotFoundError(
                f"GenomicsDBImport did not create workspace at {workspace}"
            )

        # GenotypeGVCFs
        output_vcf = output_dir / f"{cohort_id}_genotyped.vcf"
        genotype_cmd = [
            "gatk",
            "GenotypeGVCFs",
            "-R",
            str(ref.path),
            "-V",
            f"gendb://{workspace}",
            "-O",
            str(output_vcf),
        ]

        _, stderr = await _run(genotype_cmd, cwd=tmpdir)

    if not output_vcf.exists():
        raise FileNotFoundError(
            f"GenotypeGVCFs did not create output VCF at {output_vcf}. stderr: {stderr}"
        )

    source_samples = [g.sample_id for g in gvcfs]
    vcf = Variants()
    await vcf.update(
        output_vcf,
        sample_id=cohort_id,
        caller="joint_call_gvcfs",
        variant_type="vcf",
        build=ref.build,
        sample_count=len(source_samples),
        source_samples=source_samples,
    )

    idx_path = output_dir / f"{output_vcf.name}.idx"
    if idx_path.exists():
        vidx = VariantsIndex()
        await vidx.update(idx_path, sample_id=cohort_id, variants_cid=vcf.cid)

    logger.info(vcf.to_dict())
    return vcf
