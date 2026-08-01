"""SQLite record of ads already seen, so a restart never re-alerts."""

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_ads (
    ad_id      INTEGER PRIMARY KEY,
    subject    TEXT,
    price      REAL,
    url        TEXT,
    first_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def is_bootstrapped(self) -> bool:
        row = self._db.execute("SELECT value FROM meta WHERE key = 'bootstrapped'").fetchone()
        return row is not None

    def mark_bootstrapped(self) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('bootstrapped', ?)",
            (str(time.time()),),
        )
        self._db.commit()

    def filter_new(self, ads: list) -> list:
        """Return the subset of `ads` never recorded before, oldest first."""
        if not ads:
            return []
        ids = [ad.id for ad in ads if ad.id is not None]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        known = {
            row["ad_id"]
            for row in self._db.execute(
                f"SELECT ad_id FROM seen_ads WHERE ad_id IN ({placeholders})", ids
            )
        }
        fresh = [ad for ad in ads if ad.id is not None and ad.id not in known]
        # The API returns newest first; alert in publication order.
        return list(reversed(fresh))

    def record(self, ads: list) -> None:
        now = time.time()
        self._db.executemany(
            "INSERT OR IGNORE INTO seen_ads (ad_id, subject, price, url, first_seen)"
            " VALUES (?, ?, ?, ?, ?)",
            [(ad.id, ad.subject, ad.price, ad.url, now) for ad in ads if ad.id is not None],
        )
        self._db.commit()

    def prune(self, keep_days: int = 60) -> int:
        cutoff = time.time() - keep_days * 86400
        cur = self._db.execute("DELETE FROM seen_ads WHERE first_seen < ?", (cutoff,))
        self._db.commit()
        return cur.rowcount

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM seen_ads").fetchone()[0]

    def close(self) -> None:
        self._db.close()
