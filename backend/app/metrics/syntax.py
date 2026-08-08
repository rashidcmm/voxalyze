"""Syntax metrics (Phase 4), via spaCy dependency parsing.

en_core_web_sm uses the ClearNLP-style dependency scheme, where passive voice
shows up as nsubjpass/auxpass (not the newer UD nsubj:pass/aux:pass) — verified
against the installed model rather than assumed.
"""
from dataclasses import dataclass
from functools import lru_cache

import spacy

CLAUSE_DEPS = {"ROOT", "ccomp", "xcomp", "advcl", "relcl", "acl", "conj"}
SUBORDINATE_DEPS = {"ccomp", "xcomp", "advcl", "relcl", "acl"}
PASSIVE_DEPS = {"nsubjpass", "auxpass"}

DISCOURSE_MARKERS = {
    "however",
    "therefore",
    "furthermore",
    "moreover",
    "additionally",
    "consequently",
    "nevertheless",
    "thus",
    "hence",
    "meanwhile",
    "alternatively",
}


@lru_cache
def _get_nlp():
    # Disable components we don't use — parser+tagger are what we need, NER isn't.
    return spacy.load("en_core_web_sm", disable=["ner", "lemmatizer"])


@dataclass
class SyntaxMetrics:
    sentence_count: int
    mean_length_utterance: float
    clauses_per_sentence: float
    subordination_ratio: float
    passive_voice_pct: float
    discourse_marker_rate_per_100_words: float


def compute_syntax_metrics(text: str) -> SyntaxMetrics:
    text = text.strip()
    if not text:
        return SyntaxMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0)

    nlp = _get_nlp()
    doc = nlp(text)
    sentences = list(doc.sents)
    sentence_count = len(sentences)
    if sentence_count == 0:
        return SyntaxMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0)

    word_tokens = [t for t in doc if not t.is_punct and not t.is_space]
    word_count = len(word_tokens)
    mean_length_utterance = word_count / sentence_count

    total_clauses = 0
    total_subordinate_clauses = 0
    sentences_with_passive = 0

    for sent in sentences:
        clause_tokens = [
            t for t in sent if t.dep_ in CLAUSE_DEPS and t.pos_ in ("VERB", "AUX")
        ]
        total_clauses += len(clause_tokens)
        total_subordinate_clauses += sum(1 for t in clause_tokens if t.dep_ in SUBORDINATE_DEPS)
        if any(t.dep_ in PASSIVE_DEPS for t in sent):
            sentences_with_passive += 1

    clauses_per_sentence = total_clauses / sentence_count
    subordination_ratio = (total_subordinate_clauses / total_clauses) if total_clauses else 0.0
    passive_voice_pct = (sentences_with_passive / sentence_count) * 100

    discourse_count = sum(1 for t in word_tokens if t.text.lower() in DISCOURSE_MARKERS)
    discourse_marker_rate = (discourse_count / word_count * 100) if word_count else 0.0

    return SyntaxMetrics(
        sentence_count=sentence_count,
        mean_length_utterance=round(mean_length_utterance, 2),
        clauses_per_sentence=round(clauses_per_sentence, 2),
        subordination_ratio=round(subordination_ratio, 3),
        passive_voice_pct=round(passive_voice_pct, 1),
        discourse_marker_rate_per_100_words=round(discourse_marker_rate, 2),
    )
