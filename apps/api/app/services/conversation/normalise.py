"""Turn a typed message into a form the router can reason about.

Two forms come out, and the distinction matters: ``raw`` is exactly what the
customer typed and is what goes in the transcript, while ``text`` is folded and
expanded for matching. Nothing that reaches storage or a reply ever uses
``text`` - "I've recorded your input as ..." must quote the person, not a
normalised echo of them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Contractions, expanded so the question detector can match one `what` rather
#: than `what|what's|whats`. Ordered longest-first within each group so
#: `doesn't` is not clipped by a `don't` rule.
CONTRACTIONS: dict[str, str] = {
    "what's": "what is",
    "whats": "what is",
    "how's": "how is",
    "hows": "how is",
    "where's": "where is",
    "wheres": "where is",
    "who's": "who is",
    "when's": "when is",
    "why's": "why is",
    "that's": "that is",
    "thats": "that is",
    "there's": "there is",
    "theres": "there is",
    "it's": "it is",
    "let's": "let us",
    "lets": "let us",
    "cannot": "cannot",
    "can't": "cannot",
    "cant": "cannot",
    "won't": "will not",
    "wont": "will not",
    "shan't": "shall not",
    "doesn't": "does not",
    "doesnt": "does not",
    "don't": "do not",
    "dont": "do not",
    "didn't": "did not",
    "didnt": "did not",
    "isn't": "is not",
    "isnt": "is not",
    "aren't": "are not",
    "arent": "are not",
    "wasn't": "was not",
    "weren't": "were not",
    "haven't": "have not",
    "havent": "have not",
    "hasn't": "has not",
    "hadn't": "had not",
    "couldn't": "could not",
    "couldnt": "could not",
    "shouldn't": "should not",
    "shouldnt": "should not",
    "wouldn't": "would not",
    "wouldnt": "would not",
    "i'm": "i am",
    "i've": "i have",
    "i'd": "i would",
    "i'll": "i will",
    "you're": "you are",
    "you've": "you have",
    "we're": "we are",
    "we've": "we have",
    "they're": "they are",
}

_CONTRACTION_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in CONTRACTIONS), key=len, reverse=True)) + r")\b"
)

#: Typographic characters that would otherwise defeat a plain-ASCII pattern.
#: The ambiguous characters are the whole point of this table, so RUF001 is
#: suppressed rather than the characters avoided.
_PUNCTUATION_FOLD = {
    "‘": "'",  # LEFT SINGLE QUOTATION MARK
    "’": "'",  # RIGHT SINGLE QUOTATION MARK - what a phone types for an apostrophe
    "‛": "'",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK
    "“": '"',  # LEFT DOUBLE QUOTATION MARK
    "”": '"',  # RIGHT DOUBLE QUOTATION MARK
    "–": "-",  # EN DASH
    "—": "-",  # EM DASH
    "−": "-",  # MINUS SIGN - pasted latitudes carry this instead of a hyphen
}


@dataclass(frozen=True)
class Normalised:
    """A message in both the form the user typed and the form we match on."""

    raw: str
    text: str

    @property
    def is_blank(self) -> bool:
        return not self.text.strip()


def _fold_punctuation(text: str) -> str:
    for source, replacement in _PUNCTUATION_FOLD.items():
        text = text.replace(source, replacement)
    return text


def expand_contractions(text: str) -> str:
    """`what's` -> `what is`, `can't` -> `cannot`.

    Applied for routing only. Without it the question detector has to carry
    three spellings of every interrogative, and `can't I give annual usage?`
    reads as a bare `I` rather than a question.
    """
    return _CONTRACTION_RE.sub(lambda m: CONTRACTIONS[m.group(0)], text)


def normalise(raw: str) -> Normalised:
    """Fold `raw` into a matchable form, keeping the original verbatim."""
    text = unicodedata.normalize("NFKC", raw)
    text = _fold_punctuation(text)
    text = text.lower().strip()
    text = expand_contractions(text)
    # "twenty-four" / "twenty four" -> "twentyfour", so one lookup covers both
    # and the colloquial-pair rule in `numbers` cannot read it as 2004.
    text = re.sub(
        r"\b(twenty)[\s-]+(one|two|three|four|five|six|seven|eight|nine)\b", r"\1\2", text
    )
    text = re.sub(r"\s+", " ", text).strip()
    return Normalised(raw=raw, text=text)


__all__ = ["CONTRACTIONS", "Normalised", "expand_contractions", "normalise"]
