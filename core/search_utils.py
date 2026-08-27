from __future__ import annotations

from typing import Any, Callable, List, Tuple


def fuzzy_score(query: str, target: str) -> float:
    """
    Calculates a fuzzy match score between query and target.
    Returns a score where higher is better. 0 means no match.

    The algorithm:
    1. Check if characters of query appear in target in order.
    2. If not, return 0.
    3. Calculate score based on:
       - Contiguous matches (bonus)
       - Word boundary matches (bonus)
       - Start of string matches (bonus)
       - Total coverage (query length / target length)
    """
    if not query:
        return 100.0

    query_len = len(query)
    target_len = len(target)

    if query_len > target_len:
        return 0.0

    query_lower = query.lower().strip()
    target_lower = target.lower().strip()

    # Handle multiple terms
    query_terms = query_lower.split()
    if len(query_terms) > 1:
        term_scores = [fuzzy_score(term, target) for term in query_terms]
        if 0.0 in term_scores:
            return 0.0
        return sum(term_scores) / len(term_scores)

    # 1. Sequence match check
    curr_target_idx = 0
    match_indices = []
    for char in query_lower:
        idx = target_lower.find(char, curr_target_idx)
        if idx == -1:
            return 0.0
        match_indices.append(idx)
        curr_target_idx = idx + 1

    # Base score for sequence match
    score = 10.0

    # 2. Case-sensitive bonus
    if query in target:
        score += 5.0

    # 3. Exact match bonus
    if query_lower == target_lower:
        score += 100.0
        return score

    # 4. Substring bonus
    if query_lower in target_lower:
        score += 50.0
        if target_lower.startswith(query_lower):
            score += 20.0

    # 5. Contiguous matches bonus
    contiguous_count = 0
    for i in range(1, len(match_indices)):
        if match_indices[i] == match_indices[i - 1] + 1:
            contiguous_count += 1

    score += contiguous_count * 10.0

    # 6. Word boundary bonus
    # Word boundaries are start of string, after space, underscore, hyphen, or camelCase
    word_boundaries = [0]
    for i in range(1, target_len):
        # After separator
        if target[i - 1] in " _-./\\":
            word_boundaries.append(i)
        # CamelCase boundary
        elif target[i].isupper() and not target[i - 1].isupper():
            word_boundaries.append(i)

    for idx in match_indices:
        if idx in word_boundaries:
            score += 15.0

    # 7. Coverage factor (to prefer shorter strings for same query)
    score += (query_len / target_len) * 10.0

    return score


def filter_and_sort(
    query: str,
    items: List[Any],
    key_func: Callable[[Any], str] = lambda x: x,
) -> List[Tuple[float, Any]]:
    """
    Filters and sorts a list of items based on fuzzy score.
    Returns a list of (score, item) tuples, sorted by score descending.
    """
    scored_items = []
    for item in items:
        target = key_func(item)
        score = fuzzy_score(query, target)
        if score > 0:
            scored_items.append((score, item))

    # Sort by score descending
    scored_items.sort(key=lambda x: x[0], reverse=True)
    return scored_items

