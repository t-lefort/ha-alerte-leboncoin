"""Poll one leboncoin search and alert on genuinely new ads."""

import logging
import random
import signal
import sys
import threading
from datetime import datetime, timedelta

from .config import Config, ConfigError
from .filters import KeywordFilter
from .notify import Notifier
from .source import Blocked, LeboncoinSource
from .store import Store

log = logging.getLogger("watcher")

# Even a healthy setup gets challenged now and then — measured at roughly 1 poll
# in 6 on an otherwise perfect run. So the first couple of 403s are treated as
# noise: new session, short pause, try again. Only a *run* of them means we have
# actually been flagged, and that is when the long ladder kicks in.
TRANSIENT_BLOCKS = 2
TRANSIENT_PAUSE = (20, 45)
BACKOFF_START = 300
BACKOFF_MAX = 3600

PRUNE_EVERY = 500

# Quiet polls log at DEBUG, which leaves `docker compose logs` empty for hours
# and makes a perfectly healthy watcher look hung. A periodic INFO line is the
# difference between "it works" and "I think it crashed".
HEARTBEAT_EVERY = 20

_stop = threading.Event()


def _handle_signal(signum, _frame):
    log.info("Received signal %s, shutting down", signum)
    _stop.set()


def in_quiet_hours(now: datetime, start: int, end: int) -> bool:
    hour = now.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # window wraps past midnight


def seconds_until(now: datetime, hour: int) -> float:
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def run(config: Config) -> int:
    store = Store(config.db_path)
    source = LeboncoinSource(config.search_url, config.impersonate)
    notifier = Notifier(config)
    keyword_filter = KeywordFilter(
        config.require_keywords, config.exclude_keywords, config.search_body
    )

    log.info("Watching: %s", config.search_url)
    if keyword_filter.active:
        log.info(
            "Keyword filter — require=%s exclude=%s (on %s)",
            config.require_keywords or "-",
            config.exclude_keywords or "-",
            "title+body" if config.search_body else "title",
        )
    log.info(
        "Poll every ~%ds (±%d%%), quiet hours %02dh-%02dh %s, %d ad(s) known",
        config.poll_seconds,
        int(config.jitter_ratio * 100),
        config.quiet_start,
        config.quiet_end,
        config.timezone.key,
        store.count(),
    )

    backoff = 0.0
    consecutive_blocks = 0
    polls = 0
    was_quiet = False

    while not _stop.is_set():
        now = datetime.now(config.timezone)

        if config.has_quiet_hours and in_quiet_hours(now, config.quiet_start, config.quiet_end):
            nap = seconds_until(now, config.quiet_end)
            log.info("Quiet hours, sleeping %.1fh until %02dh", nap / 3600, config.quiet_end)
            # Going dark overnight is not just about your sleep: a search that
            # pauses when humans do looks a lot more like a human.
            was_quiet = True
            _stop.wait(nap)
            continue

        try:
            ads = source.fetch()
        except Blocked as exc:
            consecutive_blocks += 1
            # A fresh session is what actually clears a challenge; the pause is
            # what keeps the retry from looking like hammering.
            source.reset()
            if consecutive_blocks <= TRANSIENT_BLOCKS:
                pause = random.uniform(*TRANSIENT_PAUSE)
                log.info("Challenged (%d), new session in %.0fs", consecutive_blocks, pause)
            else:
                step = consecutive_blocks - TRANSIENT_BLOCKS - 1
                pause = min(BACKOFF_MAX, BACKOFF_START * (2**step))
                log.warning(
                    "Blocked %d times in a row (%s) — backing off %.0fs",
                    consecutive_blocks,
                    exc,
                    pause,
                )
            _stop.wait(pause)
            continue
        except Exception:
            log.exception("Unexpected error during poll, retrying after one interval")
            _stop.wait(config.poll_seconds)
            continue

        if consecutive_blocks:
            log.info("Recovered after %d block(s)", consecutive_blocks)
        consecutive_blocks = 0
        polls += 1

        new_ads = store.filter_new(ads)

        if not store.is_bootstrapped():
            # First ever run: everything currently listed is "old news".
            store.record(ads)
            store.mark_bootstrapped()
            log.info("Bootstrapped with %d existing ad(s), no alert sent", len(ads))
        elif new_ads:
            kept, dropped = keyword_filter.apply(new_ads)
            for ad, reason in dropped:
                log.info("SKIP  %s — %s (%s)", ad.id, ad.subject, reason)

            if kept:
                batch = kept[: config.max_ads_per_batch]
                if len(kept) > len(batch):
                    log.warning(
                        "%d new ads, alerting on the %d most recent only", len(kept), len(batch)
                    )
                kind = "catchup" if was_quiet else "live"
                for ad in batch:
                    log.info("NEW [%s] %s — %s — %s", kind, ad.id, ad.subject, ad.url)
                notifier.send(batch, kind)

            # Everything new is recorded, filtered-out ads included, so they are
            # never re-examined. Recorded after sending: if delivery crashes the
            # ads stay unseen and get another chance next poll.
            store.record(new_ads)
        else:
            log.debug("No new ads (%d in listing)", len(ads))

        was_quiet = False

        if polls % HEARTBEAT_EVERY == 0:
            log.info(
                "Alive — %d polls, %d ad(s) in listing, %d known", polls, len(ads), store.count()
            )

        if polls % PRUNE_EVERY == 0:
            removed = store.prune()
            if removed:
                log.info("Pruned %d old ad record(s)", removed)

        jitter = config.poll_seconds * config.jitter_ratio
        delay = max(30.0, random.uniform(config.poll_seconds - jitter, config.poll_seconds + jitter))
        _stop.wait(delay)

    store.close()
    log.info("Stopped")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    return run(config)


if __name__ == "__main__":
    sys.exit(main())
