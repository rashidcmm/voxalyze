"""Day 5: combine the deterministic metrics (Day 3) and model scores (Day 4)
into 5 headline dimensions — Fluency, Vocabulary, Clarity, Relevance,
Argumentation — each on a 0-100 scale.

**Honesty note (same spirit as Day 3's metrics writeup):** these weights and
normalization curves are hand-picked, reasonable defaults, not fit against any
human-rated data — that calibration is exactly what the Day 5 evaluation
harness (`scripts/evaluate.py`, `EVALUATION.md`) is for. Treat every constant
in this file as a documented starting point, not a validated model.

Clarity and Argumentation depend on optional external scorers (Azure /
Anthropic). When those aren't configured yet, the dimension is `None` rather
than a fabricated number — the frontend shows "connect an API key" instead of
a fake score. `overall` is the mean of whatever dimensions ARE available.
"""
from dataclasses import dataclass

# --- topic-difficulty normalization -----------------------------------------
# A harder topic (difficulty 3) should not look like regression relative to an
# easy one (difficulty 1). Coarse additive bonus, applied uniformly across all
# five dimensions before clamping to [0, 100]. Not derived from data — a
# concrete Phase 7 target once real difficulty-stratified sessions exist.
DIFFICULTY_BONUS = {1: 0.0, 2: 4.0, 3: 8.0}


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _linear_score(value: float, worst: float, best: float) -> float:
    """Map value linearly onto [0, 100], where `best` -> 100 and `worst` -> 0.
    Works whether best > worst (higher-is-better) or best < worst (lower-is-better).
    """
    if best == worst:
        return 50.0
    frac = (value - worst) / (best - worst)
    return _clamp(frac * 100)


def _target_band_score(value: float, low: float, high: float, falloff: float) -> float:
    """100 anywhere inside [low, high]; degrades linearly outside the band at
    `falloff` points per unit distance. Used for metrics with a "sweet spot"
    (WPM) rather than a monotonic direction.
    """
    if low <= value <= high:
        return 100.0
    distance = (low - value) if value < low else (value - high)
    return _clamp(100 - distance * falloff)


# --- Fluency -----------------------------------------------------------------
# Documented weights: pace 40%, filler rate 30%, hesitation pauses 30%.
def _fluency_score(m) -> float:
    # 110-170 WPM is a comfortable conversational GD pace; degrade 2.5pts/wpm outside.
    pace = _target_band_score(m.wpm_overall, low=110, high=170, falloff=2.5)
    # 0 fillers/100 words = 100; 15+/100 words = 0.
    filler = _linear_score(m.filler_rate_per_100_words, worst=15.0, best=0.0)
    # 0 hesitation pauses (>2s) = 100; 6+ = 0.
    hesitation = _linear_score(m.hesitation_pause_count, worst=6.0, best=0.0)
    return _clamp(0.4 * pace + 0.3 * filler + 0.3 * hesitation)


# --- Vocabulary ----------------------------------------------------------------
# Documented weights: lexical diversity (MTLD) 70%, repetition 30%.
def _vocabulary_score(m) -> float:
    # MTLD: below ~40 is very repetitive speech, above ~100 is rich/varied.
    mtld = _linear_score(m.mtld_score, worst=40.0, best=100.0)
    repetition = _linear_score(m.repeated_trigram_rate, worst=0.3, best=0.0)
    return _clamp(0.7 * mtld + 0.3 * repetition)


# --- Clarity (pronunciation) --------------------------------------------------
# Framed as clarity/intelligibility, never "accent correctness" — see
# app/scoring/pronunciation.py's module docstring for the product/ethics call.
def _clarity_score(pronunciation: dict | None) -> float | None:
    if pronunciation is None:
        return None
    parts = [
        pronunciation.get("accuracy_score"),
        pronunciation.get("fluency_score"),
        pronunciation.get("completeness_score"),
    ]
    parts = [p for p in parts if p is not None]
    if not parts:
        return None
    return _clamp(sum(parts) / len(parts))


# --- Relevance -----------------------------------------------------------------
def _relevance_score(relevance: dict | None) -> float | None:
    if relevance is None or relevance.get("mean_relevance") is None:
        return None
    return _clamp(relevance["mean_relevance"] * 100)


# --- Argumentation ---------------------------------------------------------------
# Mean of the LLM's five 1-5 dimension scores, rescaled to 0-100.
def _argumentation_score(argument: dict | None) -> float | None:
    if argument is None:
        return None
    dims = [
        argument.get("argument_structure"),
        argument.get("evidence_use"),
        argument.get("persuasiveness"),
        argument.get("coherence"),
        argument.get("counter_argument_handling"),
    ]
    dims = [d for d in dims if d is not None]
    if not dims:
        return None
    mean_1_to_5 = sum(dims) / len(dims)
    return _clamp((mean_1_to_5 - 1) / 4 * 100)


@dataclass
class HeadlineScores:
    fluency: float
    vocabulary: float
    clarity: float | None
    relevance: float | None
    argumentation: float | None
    overall: float


def compute_headline_scores(
    metrics,
    pronunciation: dict | None,
    relevance: dict | None,
    argument: dict | None,
    topic_difficulty: int,
) -> HeadlineScores:
    bonus = DIFFICULTY_BONUS.get(topic_difficulty, 0.0)

    fluency = _clamp(_fluency_score(metrics) + bonus)
    vocabulary = _clamp(_vocabulary_score(metrics) + bonus)

    clarity_raw = _clarity_score(pronunciation)
    clarity = _clamp(clarity_raw + bonus) if clarity_raw is not None else None

    relevance_raw = _relevance_score(relevance)
    relevance_score = _clamp(relevance_raw + bonus) if relevance_raw is not None else None

    argumentation_raw = _argumentation_score(argument)
    argumentation = _clamp(argumentation_raw + bonus) if argumentation_raw is not None else None

    available = [v for v in (fluency, vocabulary, clarity, relevance_score, argumentation) if v is not None]
    overall = sum(available) / len(available) if available else 0.0

    return HeadlineScores(
        fluency=round(fluency, 1),
        vocabulary=round(vocabulary, 1),
        clarity=round(clarity, 1) if clarity is not None else None,
        relevance=round(relevance_score, 1) if relevance_score is not None else None,
        argumentation=round(argumentation, 1) if argumentation is not None else None,
        overall=round(overall, 1),
    )
