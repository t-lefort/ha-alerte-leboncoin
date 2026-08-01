"""Client-side filtering of search results.

Leboncoin's full-text search is loose: a search for "apple tv 4K" also returns
video projectors, HDMI dongles and connected bulbs — measured at 35 results for
9 relevant ones. That is tolerable when browsing and unacceptable when every
hit fires an alert, because a few false alarms are all it takes before the
whole thing gets switched off.

Two of the checks read structured attributes rather than guessing from wording:
`transaction_status` turns to "pending" as soon as a buyer has engaged, and
`condition` carries the declared state. An ad that is both already sold and
broken looks perfectly good in its title.
"""

from __future__ import annotations

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
        words = [normalise(word) for word in alternative.split() if word.strip()]
        if words:
            groups.append(words)
    return groups


def matches_any(haystack: str, groups: list[list[str]]) -> bool:
    return any(all(word in haystack for word in group) for group in groups)


# Declared states leboncoin exposes, worst first. Roughly 30 ads in 100 carry
# no condition attribute at all, so a missing value can never be a rejection.
CONDITIONS = [
    "pourpieces",
    "etatsatisfaisant",
    "bonetat",
    "tresbonetat",
    "reconditionne",
    "etatneuf",
    "neufavecetiquette",
]

DEFAULT_EXCLUDED_CONDITIONS = ["pourpieces"]


class AdFilter:
    """Filters the flat ad dicts produced by `api.serialise`."""

    def __init__(
        self,
        require: str | None = None,
        exclude: str | None = None,
        search_body: bool = False,
        excluded_conditions: list[str] | None = None,
        exclude_pending: bool = True,
    ) -> None:
        self.require = parse_keywords(require)
        self.exclude = parse_keywords(exclude)
        self.search_body = search_body
        self.excluded_conditions = set(
            DEFAULT_EXCLUDED_CONDITIONS if excluded_conditions is None else excluded_conditions
        )
        self.exclude_pending = exclude_pending

    @property
    def active(self) -> bool:
        return bool(self.require or self.exclude or self.excluded_conditions or self.exclude_pending)

    def reject_reason(self, ad: dict) -> str | None:
        # Relevance before condition: an ad for connected bulbs is not "already
        # sold", it is simply not what you are looking for, and the logged
        # reason should say so.
        text = ad.get("title") or ""
        if self.search_body:
            text = f"{text} {ad.get('body') or ''}"
        haystack = normalise(text)

        if self.require and not matches_any(haystack, self.require):
            return "no required keyword"
        if self.exclude and matches_any(haystack, self.exclude):
            return "excluded keyword"

        # Then the structured attributes: facts, not guesses about wording.
        if self.exclude_pending and ad.get("transaction_status") == "pending":
            return "purchase already in progress"

        condition = ad.get("condition")
        if condition and condition in self.excluded_conditions:
            return f"condition '{condition}'"
        return None

    def apply(self, ads: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
        """Return (kept, [(ad, reason), ...])."""
        kept: list[dict] = []
        dropped: list[tuple[dict, str]] = []
        for ad in ads:
            reason = self.reject_reason(ad)
            if reason:
                dropped.append((ad, reason))
            else:
                kept.append(ad)
        return kept, dropped
