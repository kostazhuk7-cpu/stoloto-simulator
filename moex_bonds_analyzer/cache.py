"""
SQLite-кэш для MOEX ISS API с TTL 1 час.
Хранится в ~/.moex_bonds_cache.db

Потокобезопасен: использует threading.Lock для.serialизации
доступа к SQLite, который создаётся с check_same_thread=False.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional


class MOEXCache:
    """Потокобезопасный кэш с автоматической очисткой устаревших записей."""

    def __init__(self, db_path: Optional[Path] = None, ttl: int = 3600):
        self.db_path = db_path or Path.home() / ".moex_bonds_cache.db"
        self.ttl = ttl
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    def __enter__(self) -> "MOEXCache":
        self._ensure_conn()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=10,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS cache "
                "(url TEXT PRIMARY KEY, data TEXT, expires_at REAL)"
            )
        return self._conn

    def get(self, url: str) -> Optional[Any]:
        """Вернуть распарсенный JSON или None, если записи нет / истекла."""
        with self._lock:
            conn = self._ensure_conn()
            self._clear_expired()
            row = conn.execute(
                "SELECT data FROM cache WHERE url = ? AND expires_at > ?",
                (url, time.time()),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["data"])

    def set(self, url: str, data: Any) -> None:
        """Сохранить данные в кэш."""
        with self._lock:
            conn = self._ensure_conn()
            expires_at = time.time() + self.ttl
            conn.execute(
                "INSERT OR REPLACE INTO cache (url, data, expires_at) VALUES (?, ?, ?)",
                (url, json.dumps(data, ensure_ascii=False, default=str), expires_at),
            )
            conn.commit()

    def _clear_expired(self) -> None:
        if self._conn is None:
            return
        self._conn.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))
        self._conn.commit()

    def clear_expired(self) -> None:
        """Публичный метод очистки."""
        self._clear_expired()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
