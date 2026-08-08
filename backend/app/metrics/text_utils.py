import re

_WORD_RE = re.compile(r"[a-z']+")


def tokenize_words(text: str) -> list[str]:
    """Lowercase word tokens, punctuation stripped. Shared by vocabulary metrics
    (which need clean tokens, not timestamps) — deliberately separate from
    fluency.py's per-word normalization, which operates on WordTiming objects.
    """
    return _WORD_RE.findall(text.lower())
