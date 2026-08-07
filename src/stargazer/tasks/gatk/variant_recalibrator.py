"""
### VariantRecalibrator task for Stargazer.

Builds a recalibration model for VQSR using GATK VariantRecalibrator.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

import stargazer.utils.local_storage as _storage
from stargazer.assets import KnownSites, Reference, Variants, VQSRModel
from stargazer.config import gatk_env, logger
from stargazer.utils import _run

_SNP_ANNOTATIONS = ["QD", "MQ", "MQRankSum", "ReadPosRankSum", "FS", "SOR"]
_INDEL_ANNOTATIONS = ["QD", "FS", "SOR"]


@gatk_env.task
async def variant_recalibrator(
    vcf: Variants,
    ref: Reference,
    resources: list[KnownSites],
    mode: str = "SNP",
) -> VQSRModel:
    """
    Build a VQSR recalibration model using GATK VariantRecalibrator.

    Each KnownSites in ``resources`` must carry the following keyvalues:
        resource_name: e.g. "hapmap", "omni", "1000G", "dbsnp", "mills"
        known:         "true" or "false"
        training:      "true" or "false"
        truth:         "true" or "false"
        prior:         numeric string, e.g. "15"

    Args:
        vcf: Raw genotyped VCF Variants asset
        ref: Reference FASTA asset
        resources: Training/truth VCF resources for the recalibrator
        mode: Variant type to recalibrate — "SNP" or "INDEL"

    Returns:
        VQSRModel asset (recal file) with tranches_path stored in keyvalues

    Reference:
        https://gatk.broadinstitute.org/hc/en-us/articles/360035531612-Variant-Quality-Score-Recalibration-VQSR
    """
    logger.info(vcf.to_dict())
    logger.info(ref.to_dict())
    logger.info([x.to_dict() for x in resources])
    if mode not in ("SNP", "INDEL"):
        raise ValueError(f"mode must be 'SNP' or 'INDEL', got {mode!r}")
    if not resources:
        raise ValueError(f"At least one resource VCF is required for mode={mode!r}")

    await vcf.fetch()
    await ref.fetch()
    for r in resources:
        await r.fetch()

    output_dir = _storage.default_client.local_dir
    sample_id = vcf.sample_id or "cohort"
    output_recal = output_dir / f"{sample_id}_{mode.lower()}.recal"
    output_tranches = output_dir / f"{sample_id}_{mode.lower()}.tranches"

    annotations = _SNP_ANNOTATIONS if mode == "SNP" else _INDEL_ANNOTATIONS

    cmd = [
        "gatk",
        "VariantRecalibrator",
        "-R",
        str(ref.path),
        "-V",
        str(vcf.path),
        "-mode",
        mode,
        "-O",
        str(output_recal),
        "--tranches-file",
        str(output_tranches),
    ]

    for r in resources:
        name = r.resource_name or "unknown"
        known = r.known or "false"
        training = r.training or "false"
        truth = r.truth or "false"
        prior = r.prior or "10"
        cmd.extend(
            [
                f"--resource:{name},known={known},training={training},truth={truth},prior={prior}",
                str(r.path),
            ]
        )

    for ann in annotations:
        cmd.extend(["-an", ann])

    _, stderr = await _run(cmd, cwd=str(output_dir))

    if not output_recal.exists():
        raise FileNotFoundError(
            f"VariantRecalibrator did not create recal file at {output_recal}. stderr: {stderr}"
        )

    model = VQSRModel()
    await model.update(
        output_recal,
        sample_id=sample_id,
        mode=mode,
        tranches_path=str(output_tranches),
        build=ref.build,
        variants_cid=vcf.cid,
    )

    logger.info(model.to_dict())
    return model
