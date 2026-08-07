"""
### ApplyVQSR task for Stargazer.

Applies VQSR recalibration to a VCF using GATK ApplyVQSR.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

from pathlib import Path

import stargazer.utils.local_storage as _storage
from stargazer.assets import Reference, Variants, VariantsIndex, VQSRModel
from stargazer.config import gatk_env, logger
from stargazer.utils import _run

_DEFAULT_FILTER_LEVEL = {"SNP": 99.5, "INDEL": 99.0}


@gatk_env.task
async def apply_vqsr(
    vcf: Variants,
    ref: Reference,
    vqsr_model: VQSRModel,
    truth_sensitivity_filter_level: float | None = None,
) -> Variants:
    """
    Apply VQSR recalibration to a VCF using GATK ApplyVQSR.

    The recalibration mode (SNP or INDEL) is read from vqsr_model.keyvalues["mode"].
    If truth_sensitivity_filter_level is not provided, defaults to 99.5 for SNP
    and 99.0 for INDEL.

    Args:
        vcf: Raw (or SNP-filtered) VCF Variants asset
        ref: Reference FASTA asset
        vqsr_model: Recalibration model from variant_recalibrator
        truth_sensitivity_filter_level: VQSLOD filter threshold (optional)

    Returns:
        Variants asset with VQSR-filtered VCF

    Reference:
        https://gatk.broadinstitute.org/hc/en-us/articles/360035531612-Variant-Quality-Score-Recalibration-VQSR
    """
    logger.info(vcf.to_dict())
    logger.info(ref.to_dict())
    logger.info(vqsr_model.to_dict())
    mode = vqsr_model.mode or "SNP"
    if mode not in ("SNP", "INDEL"):
        raise ValueError(f"VQSRModel mode must be 'SNP' or 'INDEL', got {mode!r}")

    if not vqsr_model.tranches_path:
        raise ValueError("VQSRModel is missing tranches_path")
    tranches_path = Path(vqsr_model.tranches_path)

    filter_level = truth_sensitivity_filter_level or _DEFAULT_FILTER_LEVEL[mode]

    await vcf.fetch()
    await ref.fetch()
    await vqsr_model.fetch()

    output_dir = _storage.default_client.local_dir
    sample_id = vcf.sample_id or "cohort"
    output_vcf = output_dir / f"{sample_id}_vqsr_{mode.lower()}.vcf"

    cmd = [
        "gatk",
        "ApplyVQSR",
        "-R",
        str(ref.path),
        "-V",
        str(vcf.path),
        "--recal-file",
        str(vqsr_model.path),
        "--tranches-file",
        str(tranches_path),
        "--truth-sensitivity-filter-level",
        str(filter_level),
        "--create-output-variant-index",
        "true",
        "-mode",
        mode,
        "-O",
        str(output_vcf),
    ]

    _, stderr = await _run(cmd, cwd=str(output_dir))

    if not output_vcf.exists():
        raise FileNotFoundError(
            f"ApplyVQSR did not create output VCF at {output_vcf}. stderr: {stderr}"
        )

    source_samples = vcf.source_samples or [sample_id]
    filtered_vcf = Variants()
    await filtered_vcf.update(
        output_vcf,
        sample_id=sample_id,
        caller="apply_vqsr",
        variant_type="vcf",
        vqsr_mode=mode,
        build=ref.build,
        sample_count=len(source_samples),
        source_samples=source_samples,
    )

    idx_path = output_dir / f"{output_vcf.name}.idx"
    if idx_path.exists():
        vidx = VariantsIndex()
        await vidx.update(idx_path, sample_id=sample_id, variants_cid=filtered_vcf.cid)

    logger.info(filtered_vcf.to_dict())
    return filtered_vcf
