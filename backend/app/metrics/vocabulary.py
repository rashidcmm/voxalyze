"""Vocabulary metrics (Phase 4). MTLD, not raw type-token ratio, because TTR is
length-dependent — a 2-minute and a 5-minute speech aren't comparable on TTR
alone. MTLD (McCarthy & Jarvis 2010) partitions the text into "factors" of
roughly-constant lexical diversity and is length-independent by construction.
"""
from collections import Counter
from dataclasses import dataclass

from app.metrics.text_utils import tokenize_words

MTLD_TTR_THRESHOLD = 0.72


@dataclass
class VocabularyMetrics:
    token_count: int
    mtld_score: float
    repeated_trigram_rate: float


def _mtld_single_pass(tokens: list[str], threshold: float) -> float:
    factor_count = 0.0
    types: set[str] = set()
    token_count_in_factor = 0

    for token in tokens:
        types.add(token)
        token_count_in_factor += 1
        ttr = len(types) / token_count_in_factor
        if ttr <= threshold:
            factor_count += 1
            types = set()
            token_count_in_factor = 0

    # Leftover tokens that never dropped to threshold count as a partial factor.
    if token_count_in_factor > 0:
        ttr = len(types) / token_count_in_factor
        # linear interpolation between "no factor" (ttr=1.0) and "full factor" (ttr=threshold)
        factor_count += (1 - ttr) / (1 - threshold)

    if factor_count == 0:
        return float(len(tokens))
    return len(tokens) / factor_count


def mtld(tokens: list[str], threshold: float = MTLD_TTR_THRESHOLD) -> float:
    if len(tokens) < 10:
        # MTLD is unstable/meaningless on very short samples — the roadmap's
        # own "2-5 minute" speeches won't hit this, but guard it anyway.
        return 0.0
    forward = _mtld_single_pass(tokens, threshold)
    backward = _mtld_single_pass(list(reversed(tokens)), threshold)
    return (forward + backward) / 2


def repeated_trigram_rate(tokens: list[str]) -> float:
    """Fraction of trigram occurrences that are repeats of an earlier trigram
    (i.e. sum(count - 1) over trigrams seen more than once, / total trigrams).
    0.0 = no repeated 3-grams; higher = more repetitive phrasing.
    """
    if len(tokens) < 3:
        return 0.0
    trigrams = [tuple(tokens[i : i + 3]) for i in range(len(tokens) - 2)]
    counts = Counter(trigrams)
    total = len(trigrams)
    repeated_extra = sum(c - 1 for c in counts.values() if c > 1)
    return repeated_extra / total


def compute_vocabulary_metrics(text: str) -> VocabularyMetrics:
    tokens = tokenize_words(text)
    return VocabularyMetrics(
        token_count=len(tokens),
        mtld_score=round(mtld(tokens), 1),
        repeated_trigram_rate=round(repeated_trigram_rate(tokens), 3),
    )
