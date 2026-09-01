"""database.py
Database - SQLite connection manager + schema bootstrap
- Open a single SQLite connection per process
- Create all tables on first use (idempotent)
- Provide thread-safe cursor access
- Expose DB path via config (WFC_DB_PATH env var)
Why SQLite:
- Zero external services - fits an edge / swarm node
- Single-file, easy to inspect, ship, and back up
- Sufficient write volume for commands/approvals/events
- stdlib only (sqlite3) - no new dependency
"""

from __future__ import annotations

# Standard Library
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Final

# Project Imports
from core.utils.logger import log

# region  SCHEMA

# region  CONCURRENCY TUNING
# This is a distributed system: each node process owns its own
# SQLite file, but *within* a process multiple threads write to it
# concurrently (MQTT callback thread, heartbeat monitor loop,
# metrics flush thread, ...). The Database._lock below serializes
# those writers in Python, which handles the common case.
# However, a write can still raise "database is locked" if the OS
# itself holds the file briefly (WAL checkpoint, antivirus/indexer
# on Windows, a concurrent read transaction in another process such
# as the dashboard's read-only history view). Two layers guard
# against this:
# 1. PRAGMA busy_timeout - tells SQLite's own driver to wait (and
# retry internally) for up to this many ms before raising
# "database is locked", instead of failing immediately.
# 2. A small Python-level retry with backoff around execute() /
# executemany(), for the rare case that even busy_timeout is
# exceeded (e.g. a long external lock).

SQLITE_BUSY_TIMEOUT_MS = 10_000  # passed to PRAGMA busy_timeout
SQLITE_CONNECT_TIMEOUT = 30.0  # seconds, passed to sqlite3.connect()
LOCKED_RETRY_ATTEMPTS: Final = 5
LOCKED_RETRY_BASE_DELAY = 0.05  # seconds, doubles each retry

# endregion

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id        TEXT PRIMARY KEY,
    node_type      TEXT NOT NULL,
    capabilities   TEXT NOT NULL DEFAULT '[]',   -- JSON list
    status         TEXT NOT NULL DEFAULT 'ALIVE',
    last_seen      REAL,
    registered_at  REAL NOT NULL,
    zone           TEXT,
    location_x     REAL,
    location_y     REAL,
    current_job    TEXT DEFAULT NULL,            -- fire_id or NULL
    updated_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_commands (
    pending_id   TEXT PRIMARY KEY,
    command_json TEXT NOT NULL,                  -- serialized Command
    status       TEXT NOT NULL DEFAULT 'PENDING',
    created_at   REAL NOT NULL,
    expires_at   REAL,
    decided_at   REAL,
    operator_id  TEXT,
    reason       TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_commands(status);

CREATE TABLE IF NOT EXISTS commands (
    trace_id    TEXT PRIMARY KEY,
    command_json TEXT NOT NULL,                  -- serialized Command dict
    status      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status);

CREATE TABLE IF NOT EXISTS command_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id    TEXT NOT NULL REFERENCES commands(trace_id),
    event_type  TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    timestamp   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_trace ON command_history(trace_id);

CREATE TABLE IF NOT EXISTS fire_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fire_id     TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    source      TEXT,
    severity    TEXT,
    location    TEXT,
    location_x  REAL,
    location_y  REAL,
    sensor_id   TEXT,
    timestamp   REAL NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fire_fireid ON fire_events(fire_id);
CREATE INDEX IF NOT EXISTS idx_fire_timestamp ON fire_events(timestamp);

CREATE TABLE IF NOT EXISTS fire_states (
    fire_id        TEXT PRIMARY KEY,
    state          TEXT NOT NULL,
    zone           TEXT NOT NULL,
    severity       TEXT NOT NULL,
    sensor_id      TEXT NOT NULL,
    location_x     REAL,
    location_y     REAL,
    assigned_node  TEXT,
    mission_id     TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    history_json   TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_fire_states_state ON fire_states(state);

CREATE TABLE IF NOT EXISTS missions (
    mission_id     TEXT PRIMARY KEY,
    fire_id        TEXT NOT NULL,
    state          TEXT NOT NULL,
    assigned_node  TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    history_json   TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_missions_fire_id ON missions(fire_id);
CREATE INDEX IF NOT EXISTS idx_missions_state ON missions(state);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id    TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,                  -- HIGH_FIRE | PENDING_APPROVAL | NODE_DOWN | COMMAND_FAILED
    severity    TEXT NOT NULL DEFAULT 'INFO',    -- INFO | WARNING | CRITICAL
    title       TEXT NOT NULL,
    detail      TEXT,
    source_ref  TEXT,                           -- fire_id / node_id / trace_id / pending_id
    created_at  REAL NOT NULL,
    acked_at    REAL,
    acked_by    TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_acked ON alerts(acked_at);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);

CREATE TABLE IF NOT EXISTS function_metrics (
    name          TEXT PRIMARY KEY,             -- "module.Class.method"
    call_count    INTEGER NOT NULL DEFAULT 0,
    error_count   INTEGER NOT NULL DEFAULT 0,
    total_time    REAL NOT NULL DEFAULT 0,       -- sum of durations (seconds)
    min_time      REAL,
    max_time      REAL,
    last_called_at REAL,
    -- Reservoir-style recent samples for percentile estimates,
    -- stored as a JSON array of up to METRICS_SAMPLE_SIZE floats.
    recent_samples TEXT NOT NULL DEFAULT '[]'
);

-- Commander decision event log (hybrid event-sourcing migration).
-- Append-only. Never updated or deleted.
CREATE TABLE IF NOT EXISTS domain_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,  -- sequence number
    event_id     TEXT NOT NULL UNIQUE,               -- UUID, dedup key
    event_type   TEXT NOT NULL,
    fire_id      TEXT,                               -- indexed
    node_id      TEXT,
    reason       TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    timestamp    REAL NOT NULL,
    source       TEXT NOT NULL DEFAULT 'commander'
);
CREATE INDEX IF NOT EXISTS idx_de_fire_id    ON domain_events(fire_id);
CREATE INDEX IF NOT EXISTS idx_de_event_type ON domain_events(event_type);
CREATE INDEX IF NOT EXISTS idx_de_timestamp  ON domain_events(timestamp);
"""


# endregion

# region  CLASS - Database


class Database:
    """Thin wrapper around a single SQLite connection.

    One Database instance is normally shared per-process (see
    `get_db()` below). Connections use `check_same_thread=False`
    plus a lock so they can be safely shared across the MQTT
    callback thread and the main thread.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:  # pyright: ignore[reportUnknownParameterType]
        self.path = Path(path)  # pyright: ignore[reportUnknownArgumentType]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            timeout=SQLITE_CONNECT_TIMEOUT,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};")
        self._init_schema()
        self._migrate()
        log("Database", f"opened SQLite db at {self.path}", channel="SYSTEM")

    def _init_schema(self) -> None:
        with self._lock:
            self._retry_locked(
                lambda: (
                    self._conn.executescript(SCHEMA),
                    self._conn.commit(),
                )
            )

    def _migrate(self) -> None:
        """Forward-only migrations for existing databases.
        Each migration is idempotent - safe to run on every startup.
        """
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(nodes)")}
        if "current_job" not in cols:
            self._conn.execute("ALTER TABLE nodes ADD COLUMN current_job TEXT DEFAULT NULL")
            self._conn.commit()
            log("Database", "migration: added nodes.current_job column", channel="SYSTEM")

        # create fire_states table if it doesn't exist
        tables = {row[0] for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "fire_states" not in tables:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS fire_states (
                    fire_id        TEXT PRIMARY KEY,
                    state          TEXT NOT NULL,
                    zone           TEXT NOT NULL,
                    severity       TEXT NOT NULL,
                    sensor_id      TEXT NOT NULL,
                    location_x     REAL,
                    location_y     REAL,
                    assigned_node  TEXT,
                    mission_id     TEXT,
                    created_at     REAL NOT NULL,
                    updated_at     REAL NOT NULL,
                    history_json   TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_fire_states_state ON fire_states(state);
            """)
            self._conn.commit()
            log("Database", "migration: created fire_states table", channel="SYSTEM")

        # create missions table if it doesn't exist
        if "missions" not in tables:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id     TEXT PRIMARY KEY,
                    fire_id        TEXT NOT NULL,
                    state          TEXT NOT NULL,
                    assigned_node  TEXT,
                    created_at     REAL NOT NULL,
                    updated_at     REAL NOT NULL,
                    history_json   TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_missions_fire_id ON missions(fire_id);
                CREATE INDEX IF NOT EXISTS idx_missions_state ON missions(state);
            """)
            self._conn.commit()
            log("Database", "migration: created missions table", channel="SYSTEM")

        # create domain_events table if it doesn't exist
        # (for databases created before the hybrid event-sourcing migration)
        if "domain_events" not in tables:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS domain_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id     TEXT NOT NULL UNIQUE,
                    event_type   TEXT NOT NULL,
                    fire_id      TEXT,
                    node_id      TEXT,
                    reason       TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    timestamp    REAL NOT NULL,
                    source       TEXT NOT NULL DEFAULT 'commander'
                );
                CREATE INDEX IF NOT EXISTS idx_de_fire_id    ON domain_events(fire_id);
                CREATE INDEX IF NOT EXISTS idx_de_event_type ON domain_events(event_type);
                CREATE INDEX IF NOT EXISTS idx_de_timestamp  ON domain_events(timestamp);
            """)
            self._conn.commit()
            log("Database", "migration: created domain_events table", channel="SYSTEM")

        # nodes.updated_at was written on every heartbeat but
        # never read by any query, rule, or snapshot merge - pure write overhead.
        # node_repo.py no longer writes it. Drop the column
        # from existing databases so the dead write can never reappear.
        # SQLite supports DROP COLUMN since 3.35.0 (2021-03). If the runtime
        # SQLite is older, the ALTER TABLE will raise - we catch and log rather
        # than crashing startup, since the column being present is harmless.
        node_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(nodes)")}
        if "updated_at" in node_cols:
            try:
                self._conn.execute("ALTER TABLE nodes DROP COLUMN updated_at")
                self._conn.commit()
                log("Database", "migration: dropped nodes.updated_at (dead column)", channel="SYSTEM")
            except Exception as exc:
                log(
                    "Database",
                    f"migration: could not drop nodes.updated_at (SQLite may be < 3.35): {exc}",
                    channel="SYSTEM",
                )

    # RETRY HELPER

    @staticmethod
    def _retry_locked(fn: Any):
        """Run `fn()`, retrying with backoff if SQLite reports the
        database is locked. `fn` must be idempotent / safe to retry
        (it is only re-run when the *first* statement in it failed
        before any commit took effect).

        This is a defensive second layer behind PRAGMA busy_timeout -
        it only triggers in the rare case an OS-level lock outlasts
        the busy_timeout window (e.g. antivirus/indexer on Windows,
        or a long-running reader from another process).
        """
        delay = LOCKED_RETRY_BASE_DELAY
        for attempt in range(LOCKED_RETRY_ATTEMPTS):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == LOCKED_RETRY_ATTEMPTS - 1:
                    raise
                log(
                    "Database",
                    f"database locked, retrying ({attempt + 1}/{LOCKED_RETRY_ATTEMPTS})",
                    channel="SYSTEM",
                )
                time.sleep(delay)
                delay *= 2

    # PUBLIC API

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Execute a single statement and commit. Thread-safe.
        Retries with backoff if the database is transiently locked.
        """
        with self._lock:

            def _run():
                cur = self._conn.execute(sql, params)  # pyright: ignore[reportUnknownArgumentType]
                self._conn.commit()
                return cur

            return self._retry_locked(_run)  # pyright: ignore[reportReturnType]

    def executemany(self, sql: str, seq_of_params: Any) -> sqlite3.Cursor:
        with self._lock:

            def _run():
                cur = self._conn.executemany(sql, seq_of_params)
                self._conn.commit()
                return cur

            return self._retry_locked(_run)  # pyright: ignore[reportReturnType]

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Run a SELECT and return all rows. Thread-safe."""
        with self._lock:
            cur = self._conn.execute(sql, params)  # pyright: ignore[reportUnknownArgumentType]
            return cur.fetchall()

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(sql, params)  # pyright: ignore[reportUnknownArgumentType]
            return cur.fetchone()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# endregion

# region  PER-PATH CACHE
# This is a distributed system: each node process (CentralNode,
# SwarmLeaderNode, InfraNode, ...) has its own NODE_ID and therefore
# its own SQLite file under data/<NODE_ID>.db. Within a single
# process that path is constant, so get_db() normally returns the
# same Database every time.
# However, some processes legitimately need *more than one* database
# at once - e.g. the monitor dashboard opening another node's DB for
# read-only history, or tests spinning up several nodes' databases
# in-process. A single global instance would silently return the
# wrong file in that case. We therefore cache by resolved absolute
# path: same path -> same shared connection (still avoids opening
# the same file twice), different path -> different connection.

_db_cache: dict[str, Database] = {}
_cache_lock = threading.Lock()


def get_db(path: str | os.PathLike[str] | None = None) -> Database:
    """Return a shared Database instance for the given path, opening
    and caching it on first use. Calling again with the *same*
    (resolved) path returns the same instance; a *different* path
    returns a different, independently-cached instance.

    Path resolution order:
      1. explicit `path` argument
      2. WFC_DB_PATH environment variable
      3. "data/wfc.db" (relative to current working directory)
    """
    resolved = str(path) if path is not None else os.getenv("WFC_DB_PATH", "data/wfc.db")  # pyright: ignore[reportUnknownArgumentType]
    key = str(Path(resolved).resolve())

    with _cache_lock:
        db = _db_cache.get(key)
        if db is None:
            db = Database(resolved)
            _db_cache[key] = db
        return db


def reset_db(path: str | os.PathLike[str] | None = None) -> None:
    """Close and evict cached Database instance(s).

    - With a path: closes and evicts only that path's cached instance.
    - Without a path (default): closes and evicts ALL cached instances.

    Mainly for tests / clean shutdown.
    """
    with _cache_lock:
        if path is not None:
            key = str(Path(str(path)).resolve())  # pyright: ignore[reportUnknownArgumentType]
            db = _db_cache.pop(key, None)
            if db is not None:
                db.close()
            return

        for db in _db_cache.values():
            db.close()
        _db_cache.clear()


# endregion
