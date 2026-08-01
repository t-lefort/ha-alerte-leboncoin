"""Environment-driven configuration and search-URL normalisation."""

import os
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote_plus, urlsplit
from zoneinfo import ZoneInfo

# Breadcrumbs leboncoin appends to URLs copied from the browser. `lbc` turns any
# parameter it does not recognise into an enum filter, so `from=ms` and the
# `sa=<timestamp>` marker would be sent as bogus filters and skew the results.
IGNORED_PARAMS = {"from", "sa", "page", "shippable_only"}

# `lbc` walks the query string in order and writes `shippable` *into* the
# location filter that `locations` creates, so the two must stay in this order
# or the payload builder raises KeyError.
PARAM_ORDER = [
    "text",
    "category",
    "locations",
    "shippable",
    "owner_type",
    "sort",
    "order",
]


class ConfigError(Exception):
    pass


def clean_search_url(url: str) -> str:
    """Strip tracking params and re-order the query string for `lbc`.

    Values are percent/plus-decoded because `lbc` feeds them straight into the
    API payload: left encoded, `text=apple+tv+4K` would be searched literally,
    plus signs included.
    """
    parts = urlsplit(url)
    if not parts.query:
        raise ConfigError(f"Search URL has no query string: {url}")

    params: dict[str, str] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        if key in IGNORED_PARAMS:
            continue
        decoded = unquote_plus(value)
        # `lbc` parses the query with a naive split on & and =, so a decoded
        # value containing either would silently corrupt every later parameter.
        if "&" in decoded or "=" in decoded:
            raise ConfigError(f"Unsupported character in search param '{key}': {value}")
        params[key] = decoded

    if not params:
        raise ConfigError(f"No usable search parameters in URL: {url}")

    ordered = [k for k in PARAM_ORDER if k in params]
    ordered += [k for k in params if k not in PARAM_ORDER]
    query = "&".join(f"{k}={params[k]}" for k in ordered)
    return f"{parts.scheme}://{parts.netloc}{parts.path}?{query}"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    search_url: str
    poll_seconds: int
    jitter_ratio: float
    quiet_start: int
    quiet_end: int
    timezone: ZoneInfo
    db_path: str
    impersonate: str
    ha_webhook_url: str | None
    telegram_token: str | None
    telegram_chat_id: str | None
    max_ads_per_batch: int
    require_keywords: str | None
    exclude_keywords: str | None
    search_body: bool
    dry_run: bool

    @property
    def has_quiet_hours(self) -> bool:
        return self.quiet_start != self.quiet_end

    @classmethod
    def from_env(cls) -> "Config":
        raw_url = os.environ.get("LBC_SEARCH_URL", "").strip()
        if not raw_url:
            raise ConfigError("LBC_SEARCH_URL is required")

        tz_name = os.environ.get("TZ", "Europe/Paris")
        try:
            timezone = ZoneInfo(tz_name)
        except Exception as exc:
            raise ConfigError(f"Unknown timezone: {tz_name}") from exc

        ha_webhook_url = os.environ.get("HA_WEBHOOK_URL", "").strip() or None
        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None
        telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None
        dry_run = _env_bool("DRY_RUN")

        if not dry_run and not ha_webhook_url and not telegram_token:
            raise ConfigError(
                "Configure HA_WEBHOOK_URL and/or TELEGRAM_BOT_TOKEN, or set DRY_RUN=true"
            )
        if telegram_token and not telegram_chat_id:
            raise ConfigError("TELEGRAM_CHAT_ID is required when TELEGRAM_BOT_TOKEN is set")

        quiet_start = _env_int("QUIET_START_HOUR", 23)
        quiet_end = _env_int("QUIET_END_HOUR", 8)
        for name, value in (("QUIET_START_HOUR", quiet_start), ("QUIET_END_HOUR", quiet_end)):
            if not 0 <= value <= 23:
                raise ConfigError(f"{name} must be between 0 and 23")

        poll_seconds = _env_int("POLL_SECONDS", 90)
        if poll_seconds < 30:
            # Below this the request rate stops looking like a human refreshing
            # a page, which is the whole point of the pacing.
            raise ConfigError("POLL_SECONDS must be >= 30 to stay under the radar")

        return cls(
            search_url=clean_search_url(raw_url),
            poll_seconds=poll_seconds,
            jitter_ratio=_env_float("POLL_JITTER_RATIO", 0.25),
            quiet_start=quiet_start,
            quiet_end=quiet_end,
            timezone=timezone,
            db_path=os.environ.get("DB_PATH", "/data/seen.db"),
            # See watcher/source.py: this value has to agree with the warm-up
            # request `lbc` sends, and chrome_android is the measured-good pick.
            impersonate=os.environ.get("IMPERSONATE", "chrome_android"),
            ha_webhook_url=ha_webhook_url,
            telegram_token=telegram_token,
            telegram_chat_id=telegram_chat_id,
            max_ads_per_batch=_env_int("MAX_ADS_PER_BATCH", 10),
            require_keywords=os.environ.get("REQUIRE_KEYWORDS", "").strip() or None,
            exclude_keywords=os.environ.get("EXCLUDE_KEYWORDS", "").strip() or None,
            search_body=_env_bool("FILTER_SEARCH_BODY"),
            dry_run=dry_run,
        )
