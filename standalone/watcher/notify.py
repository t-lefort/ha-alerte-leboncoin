"""Delivery: a Home Assistant webhook, with Telegram as a standalone fallback.

The watcher deliberately does not decide *how* you get alerted. It posts a
batch to Home Assistant and lets HA fan out to the critical push, Telegram, or
anything else. The direct Telegram path exists only so a Home Assistant outage
does not also take the alerts down.
"""

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

TIMEOUT = 15


def serialise(ad) -> dict:
    images = ad.images or []
    location = ad.location
    city = getattr(location, "city_label", None) or getattr(location, "city", None)
    return {
        "id": ad.id,
        "title": ad.subject,
        "price": ad.price,
        "price_label": f"{ad.price:g} €" if ad.price is not None else "Prix non précisé",
        "url": ad.url,
        "image": images[0] if images else None,
        "city": city,
        "zipcode": getattr(location, "zipcode", None),
        "published": ad.first_publication_date,
        "category": ad.category_name,
    }


def build_payload(ads: list, kind: str) -> dict:
    items = [serialise(ad) for ad in ads]
    return {
        "kind": kind,  # "live" while watching, "catchup" for the post-quiet-hours digest
        "count": len(items),
        "ads": items,
        "top": items[0] if items else None,
    }


def _post_json(url: str, payload: dict) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        response.read()


def send_home_assistant(url: str, payload: dict) -> bool:
    try:
        _post_json(url, payload)
        log.info("Home Assistant webhook delivered (%d ad(s))", payload["count"])
        return True
    except (urllib.error.URLError, OSError) as exc:
        log.error("Home Assistant webhook failed: %s", exc)
        return False


def _escape(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_telegram(token: str, chat_id: str, payload: dict, silent: bool) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for ad in payload["ads"]:
        where = f" — {_escape(ad['city'])}" if ad["city"] else ""
        text = (
            f"🔔 <b>{_escape(ad['title'])}</b>\n"
            f"{_escape(ad['price_label'])}{where}\n"
            f"{_escape(ad['url'])}"
        )
        try:
            _post_json(
                url,
                {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_notification": silent,
                },
            )
        except (urllib.error.URLError, OSError) as exc:
            log.error("Telegram send failed for ad %s: %s", ad["id"], exc)
            ok = False
    if ok:
        log.info("Telegram fallback delivered (%d ad(s))", payload["count"])
    return ok


class Notifier:
    def __init__(self, config):
        self.config = config

    def send(self, ads: list, kind: str) -> None:
        payload = build_payload(ads, kind)
        if self.config.dry_run:
            log.info("DRY_RUN, would send: %s", json.dumps(payload, ensure_ascii=False))
            return

        delivered = False
        if self.config.ha_webhook_url:
            delivered = send_home_assistant(self.config.ha_webhook_url, payload)

        # Only fall back to Telegram when HA did not take it, otherwise every
        # alert arrives twice.
        if not delivered and self.config.telegram_token:
            send_telegram(
                self.config.telegram_token,
                self.config.telegram_chat_id,
                payload,
                silent=(kind == "catchup"),
            )
