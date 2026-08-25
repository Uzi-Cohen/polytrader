"""SQLite silently drops tzinfo on `DateTime(timezone=True)` columns --
values read back from the DB come back naive even though everything
written in was UTC-aware. Postgres doesn't have this problem, but this
project's default is SQLite (core/db.py), so every comparison between a
DB-sourced datetime and a freshly-constructed `datetime.now(timezone.utc)`
needs this normalization or it raises `TypeError: can't subtract
offset-naive and offset-aware datetimes`.
"""
from __future__ import annotations

from datetime import datetime, timezone


def ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
