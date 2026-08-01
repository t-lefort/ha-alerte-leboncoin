"""Leboncoin polling, wrapped so the session stays stable across polls."""

import logging

import lbc
from lbc.exceptions import DatadomeError, RequestError

log = logging.getLogger(__name__)


class Blocked(Exception):
    """DataDome turned us away."""


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
#
# `lbc` always performs the warm-up GET when it opens a session, so the web
# story is the only coherent one available through the library — hence the
# chrome_android default. Changing IMPERSONATE without changing that warm-up is
# what produces an instant, permanent-looking 403.
COHERENT_WITH_WARMUP = {"chrome_android", "chrome", "firefox", "edge"}


class LeboncoinSource:
    """Thin wrapper over `lbc.Client`.

    Two deliberate deviations from the library defaults:

    * A pinned `impersonate` value and a long-lived client. `lbc` picks a random
      browser and a random device id per session; rotating those every poll is
      the opposite of what a real user looks like, and three of its four random
      choices are incoherent with its own warm-up (see the table above).
    * `max_retries=0`. The library's retry re-opens a session and fires again
      immediately, with no pause. Retries are handled by the caller, which knows
      how to wait.
    """

    def __init__(self, search_url: str, impersonate: str, limit: int = 35):
        self.search_url = search_url
        self.impersonate = impersonate
        self.limit = limit
        self._client: lbc.Client | None = None
        if impersonate not in COHERENT_WITH_WARMUP:
            log.warning(
                "IMPERSONATE=%s is incoherent with the warm-up request lbc always sends; "
                "expect every poll to be challenged. Known-good: %s",
                impersonate,
                ", ".join(sorted(COHERENT_WITH_WARMUP)),
            )

    @property
    def client(self) -> lbc.Client:
        if self._client is None:
            log.info("Opening leboncoin session (impersonate=%s)", self.impersonate)
            self._client = lbc.Client(impersonate=self.impersonate, max_retries=0)
        return self._client

    def reset(self) -> None:
        """Drop the session so the next poll negotiates fresh cookies."""
        self._client = None

    def fetch(self) -> list:
        try:
            result = self.client.search(url=self.search_url, limit=self.limit)
        except DatadomeError as exc:
            raise Blocked(str(exc)) from exc
        except RequestError as exc:
            # 429 and friends: same remedy as a block, just less severe.
            raise Blocked(f"request rejected: {exc}") from exc
        return result.ads
