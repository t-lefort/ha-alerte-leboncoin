"""Client-side keyword filtering.

Leboncoin's full-text search is loose: a search for "apple tv 4K" also returns
video projectors, HDMI dongles and connected bulbs. That is tolerable when you
are browsing, and unacceptable when every hit fires a critical notification —
a few false alarms and you turn the whole thing off. These filters run on the
results before anything is sent.
"""

import unicodedata


def normalise(text: str | None) -> str:
    """Lowercase and strip accents, so "Clé" matches "cle"."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def parse_keywords(raw: str | None) -> list[list[str]]:
    """Parse "apple tv, appletv" into [["apple", "tv"], ["appletv"]].

    Comma-separated alternatives; a term containing spaces requires every word
    to be present (in any order), which keeps "Apple TV" from matching an ad
    that merely says "Apple" somewhere.
    """
    if not raw or not raw.strip():
        return []
    groups = []
    for alternative in raw.split(","):
        words = [normalise(w) for w in alternative.split() if w.strip()]
        if words:
            groups.append(words)
    return groups


def matches_any(haystack: str, groups: list[list[str]]) -> bool:
    return any(all(word in haystack for word in group) for group in groups)


class KeywordFilter:
    def __init__(self, require: str | None, exclude: str | None, search_body: bool = False):
        self.require = parse_keywords(require)
        self.exclude = parse_keywords(exclude)
        self.search_body = search_body

    @property
    def active(self) -> bool:
        return bool(self.require or self.exclude)

    def _haystack(self, ad) -> str:
        text = ad.subject or ""
        if self.search_body:
            text = f"{text} {ad.body or ''}"
        return normalise(text)

    def reject_reason(self, ad) -> str | None:
        haystack = self._haystack(ad)
        if self.require and not matches_any(haystack, self.require):
            return "no required keyword"
        if self.exclude and matches_any(haystack, self.exclude):
            return "excluded keyword"
        return None

    def apply(self, ads: list) -> tuple[list, list[tuple]]:
        """Return (kept, [(ad, reason), ...])."""
        kept, dropped = [], []
        for ad in ads:
            reason = self.reject_reason(ad)
            if reason:
                dropped.append((ad, reason))
            else:
                kept.append(ad)
        return kept, dropped
