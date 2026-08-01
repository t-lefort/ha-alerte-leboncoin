"""Constants for the Leboncoin alert integration."""

DOMAIN = "leboncoin_alert"
SUBENTRY_TYPE = "search"

STORAGE_KEY = f"{DOMAIN}.seen"
STORAGE_VERSION = 1

EVENT_NEW_ADS = f"{DOMAIN}_new_ads"

CONF_URL = "url"
CONF_REQUIRE_KEYWORDS = "require_keywords"
CONF_EXCLUDE_KEYWORDS = "exclude_keywords"
CONF_SEARCH_BODY = "search_body"
CONF_POLL_SECONDS = "poll_seconds"
CONF_QUIET_START = "quiet_start"
CONF_QUIET_END = "quiet_end"
CONF_NOTIFY_SERVICES = "notify_services"
CONF_CRITICAL = "critical"
CONF_MAX_ADS = "max_ads"

DEFAULT_POLL_SECONDS = 90
DEFAULT_QUIET_START = 23
DEFAULT_QUIET_END = 8
DEFAULT_MAX_ADS = 10

# Below this the request rate stops looking like a human refreshing a page,
# which is the whole point of the pacing.
MIN_POLL_SECONDS = 30

JITTER_RATIO = 0.25

# Even a healthy setup gets challenged now and then — measured at roughly one
# poll in six. The first couple of blocks are treated as noise: new session,
# short pause, try again. Only a run of them means we were actually flagged.
TRANSIENT_BLOCKS = 2
TRANSIENT_PAUSE = 30
BACKOFF_START = 300
BACKOFF_MAX = 3600

# Ads are forgotten after this long, keeping the store from growing forever.
SEEN_RETENTION_DAYS = 60

# See coordinator.py for the measurements behind this value.
IMPERSONATE = "chrome_android"
