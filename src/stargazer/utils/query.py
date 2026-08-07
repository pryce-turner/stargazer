"""
### Query generation utilities for Stargazer.

Utilities for generating metadata queries, including support for
cartesian product queries across multiple dimensions.

spec: [docs/architecture/types.md](../architecture/types.md)
"""

from itertools import product
from typing import Any


def generate_query_combinations(
    base_query: dict[str, Any],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Generate query combinations from filters using cartesian product.

    Takes a base query dict and filters dict, where filters can contain
    scalar values or lists. For any list-valued filter, generates all
    combinations using cartesian product, while preserving scalar filters
    and the base query in all combinations.

    Args:
        base_query: Base query dict to include in all combinations
        filters: Filter dict with scalar or list values

    Returns:
        List of query dicts representing all combinations

    Example:
        >>> base = {"type": "reference"}
        >>> filters = {"build": "GRCh38", "tool": ["fasta", "bwa"]}
        >>> generate_query_combinations(base, filters)
        [
            {"type": "reference", "build": "GRCh38", "tool": "fasta"},
            {"type": "reference", "build": "GRCh38", "tool": "bwa"}
        ]

        >>> base = {"type": "reference"}
        >>> filters = {"build": ["GRCh38", "GRCh37"], "tool": ["fasta", "bwa"]}
        >>> generate_query_combinations(base, filters)
        [
            {"type": "reference", "build": "GRCh38", "tool": "fasta"},
            {"type": "reference", "build": "GRCh38", "tool": "bwa"},
            {"type": "reference", "build": "GRCh37", "tool": "fasta"},
            {"type": "reference", "build": "GRCh37", "tool": "bwa"}
        ]
    """
    # Separate list-valued and scalar-valued filters
    list_filters = {}
    scalar_filters = {}

    for key, value in filters.items():
        if isinstance(value, list):
            list_filters[key] = value
        else:
            scalar_filters[key] = value

    # Generate cartesian product of list-valued filters
    if list_filters:
        # Get keys and values for cartesian product
        keys = list(list_filters.keys())
        value_lists = [list_filters[k] for k in keys]

        # Generate all combinations
        query_combinations = []
        for combo in product(*value_lists):
            query = {**base_query, **scalar_filters}
            query.update(dict(zip(keys, combo)))
            query_combinations.append(query)
    else:
        # No list filters, just one query
        query_combinations = [{**base_query, **scalar_filters}]

    return query_combinations
