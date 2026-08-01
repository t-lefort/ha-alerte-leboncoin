"""Blocking leboncoin access, kept away from the event loop.

Everything in here runs in an executor thread: `lbc` sits on curl_cffi, which
is synchronous.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl, unquote_plus, urlsplit

import lbc
from lbc.exceptions import DatadomeError, NotFoundError, RequestError

from .const import IMPERSONATE

_LOGGER = logging.getLogger(__name__)

# Breadcrumbs leboncoin appends to URLs copied from the browser. `lbc` turns any
# parameter it does not recognise into an enum filter, so `from=ms` and the
# `sa=<timestamp>` marker would be sent as bogus filters and skew the results.
IGNORED_PARAMS = {"from", "sa", "page", "shippable_only"}

# `lbc` walks the query string in order and writes `shippable` *into* the
# location filter that `locations` creates, so the two must stay in this order
# or the payload builder raises KeyError.
PARAM_ORDER = ["text", "category", "locations", "shippable", "owner_type", "sort", "order"]

# Measured against the live API, 6 consecutive polls per combination:
#
#   TLS profile      warm-up GET on www.leboncoin.fr   result
#   chrome_android   yes                               6/6 OK
#   safari_ios       no                                6/6 OK
#   safari_ios       yes                               0/6 (captcha challenge)
#   chrome_android   no                                0/6 (captcha challenge)
#
# DataDome is not sampling at random: it checks that the TLS fingerprint agrees
# with the browsing context. A native app never loads the website first, a web
# browser always does. Either story passes; telling both at once never does.
# `lbc` always performs the warm-up GET, so the web story is the only coherent
# one available through the library — hence IMPERSONATE = chrome_android.


class InvalidSearchUrl(Exception):
    """The URL cannot be turned into a search."""


class Blocked(Exception):
    """DataDome turned us away."""


def clean_search_url(url: str) -> str:
    """Strip tracking params and re-order the query string for `lbc`.

    Values are percent/plus-decoded because `lbc` feeds them straight into the
    API payload: left encoded, `text=apple+tv+4K` would be searched literally,
    plus signs included.
    """
    parts = urlsplit(url.strip())
    if parts.netloc not in ("www.leboncoin.fr", "leboncoin.fr"):
        raise InvalidSearchUrl("not_leboncoin")
    if not parts.query:
        raise InvalidSearchUrl("no_query")

    params: dict[str, str] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        if key in IGNORED_PARAMS:
            continue
        decoded = unquote_plus(value)
        # `lbc` parses the query with a naive split on & and =, so a decoded
        # value containing either would silently corrupt every later parameter.
        if "&" in decoded or "=" in decoded:
            raise InvalidSearchUrl("bad_param")
        params[key] = decoded

    if not params:
        raise InvalidSearchUrl("no_query")

    ordered = [k for k in PARAM_ORDER if k in params]
    ordered += [k for k in params if k not in PARAM_ORDER]
    query = "&".join(f"{k}={params[k]}" for k in ordered)
    return f"https://{parts.netloc}{parts.path}?{query}"


def serialise(ad) -> dict:
    """Flatten an `lbc` ad into something JSON-safe for events and attributes."""
    images = ad.images or []
    location = ad.location
    body = ad.body or ""
    return {
        "id": ad.id,
        "title": ad.subject,
        # Truncated: this rides along in state attributes and event payloads,
        # and full ad bodies are unbounded.
        "body": body[:500],
        "price": ad.price,
        "price_label": f"{ad.price:g} €" if ad.price is not None else "Prix non précisé",
        "url": ad.url,
        "image": images[0] if images else None,
        "city": getattr(location, "city_label", None) or getattr(location, "city", None),
        "zipcode": getattr(location, "zipcode", None),
        "published": ad.first_publication_date,
        "category": ad.category_name,
    }


class LeboncoinApi:
    """Long-lived `lbc` session for one search.

    Two deliberate deviations from the library defaults: a pinned `impersonate`
    (its random pick is incoherent with its own warm-up three times out of
    four) and `max_retries=0`, because the library's retry re-opens a session
    and fires again with no pause. Retries belong to the coordinator, which
    knows how to wait.
    """

    def __init__(self, search_url: str) -> None:
        self.search_url = search_url
        self._client: lbc.Client | None = None

    def reset(self) -> None:
        """Drop the session so the next poll negotiates fresh cookies."""
        self._client = None

    def fetch(self) -> list[dict]:
        if self._client is None:
            _LOGGER.debug("Opening leboncoin session (impersonate=%s)", IMPERSONATE)
            self._client = lbc.Client(impersonate=IMPERSONATE, max_retries=0)
        try:
            result = self._client.search(url=self.search_url, limit=35)
        except DatadomeError as err:
            raise Blocked(str(err)) from err
        except (RequestError, NotFoundError) as err:
            # 429 and friends: same remedy as a block, just less severe.
            raise Blocked(f"request rejected: {err}") from err
        return [serialise(ad) for ad in result.ads]

    def fetch_raw_ads(self) -> list:
        """Used by the config flow to preview a search before saving it."""
        if self._client is None:
            self._client = lbc.Client(impersonate=IMPERSONATE, max_retries=0)
        try:
            return self._client.search(url=self.search_url, limit=35).ads
        except DatadomeError as err:
            raise Blocked(str(err)) from err
        except (RequestError, NotFoundError) as err:
            raise Blocked(f"request rejected: {err}") from err
