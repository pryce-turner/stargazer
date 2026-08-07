"""
### Stargazer tasks for bioinformatics workflows.

spec: [docs/architecture/tasks.md](../architecture/tasks.md)
"""

from stargazer.tasks.gatk.apply_bqsr import apply_bqsr
from stargazer.tasks.gatk.apply_vqsr import apply_vqsr

# GATK tasks
from stargazer.tasks.gatk.base_recalibrator import base_recalibrator
from stargazer.tasks.gatk.combine_gvcfs import combine_gvcfs
from stargazer.tasks.gatk.create_sequence_dictionary import create_sequence_dictionary
from stargazer.tasks.gatk.haplotype_caller import haplotype_caller
from stargazer.tasks.gatk.index_feature_file import index_feature_file
from stargazer.tasks.gatk.joint_call_gvcfs import joint_call_gvcfs
from stargazer.tasks.gatk.mark_duplicates import mark_duplicates
from stargazer.tasks.gatk.merge_bam_alignment import merge_bam_alignment
from stargazer.tasks.gatk.sort_sam import sort_sam
from stargazer.tasks.gatk.variant_recalibrator import variant_recalibrator
from stargazer.tasks.general.bwa import bwa_index, bwa_mem
from stargazer.tasks.general.bwa_mem2 import bwa_mem2_index, bwa_mem2_mem
from stargazer.tasks.general.samtools import samtools_faidx

__all__ = [
    "apply_bqsr",
    "apply_vqsr",
    "base_recalibrator",
    "bwa_index",
    "bwa_mem",
    "bwa_mem2_index",
    "bwa_mem2_mem",
    "combine_gvcfs",
    "create_sequence_dictionary",
    # GVCF processing
    "haplotype_caller",
    # BQSR (Base Quality Score Recalibration)
    "index_feature_file",
    "joint_call_gvcfs",
    "mark_duplicates",
    "merge_bam_alignment",
    # Reference indexing
    "samtools_faidx",
    # Data preprocessing (GATK)
    "sort_sam",
    # VQSR filtering
    "variant_recalibrator",
]
