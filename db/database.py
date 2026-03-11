"""
SQLite data layer.

Schema:
  sessions   — one row per ride (metadata + computed summary)
  datapoints — time-series data during a session
  vo2_estimates — historical VO2 max snapshots

Designed for fast insert (WAL mode) during live sessions
and efficient reads for history/analytics.
"""

import sqlite3
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from typing import Optional
import config

log = logging.getLogger(__name__)


@dataclass
class SessionSummary:
    id: int
    started_at: float
    ended_at: float
    duration_s: int
    total_distance_m: float
    avg_cadence_rpm: float
    avg_power_w: float
    max_power_w: float
    avg_hr_bpm: float
    max_hr_bpm: float
    total_kcal: float
    np_watts: float             # Normalized Power
    tss: float                  # Training Stress Score
    vo2max_estimate: float
    notes: str


def get_connection(path: str = None) -> sqlite3.Connection:
    db = sqlite3.connect(path or config.DB_PATH, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.row_factory = sqlite3.Row
    return db


def init_db(path: str = None):
    db = get_connection(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at    REAL NOT NULL,
            ended_at      REAL,
            duration_s    INTEGER,
            distance_m    REAL,
            avg_cadence   REAL,
            avg_power     REAL,
            max_power     REAL,
            avg_hr        REAL,
            max_hr        REAL,
            total_kcal    REAL,
            np_watts      REAL,
            tss           REAL,
            vo2max_est    REAL,
            notes         TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS datapoints (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id    INTEGER NOT NULL REFERENCES sessions(id),
            ts            REAL NOT NULL,
            cadence       REAL,
            power         INTEGER,
            speed         REAL,
            hr            INTEGER,
            resistance    INTEGER,
            distance_m    REAL,
            elapsed_s     INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_dp_session ON datapoints(session_id);
        CREATE INDEX IF NOT EXISTS idx_dp_ts ON datapoints(ts);

        CREATE TABLE IF NOT EXISTS vo2_estimates (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id    INTEGER REFERENCES sessions(id),
            ts            REAL NOT NULL,
            estimate      REAL NOT NULL,
            method        TEXT,
            verified      INTEGER DEFAULT 0  -- 1 if lab-tested
        );
    """)
    db.commit()

    # Insert verified VO2 max if configured
    if config.USER.get("verified_vo2max"):
        existing = db.execute(
            "SELECT COUNT(*) FROM vo2_estimates WHERE verified=1"
        ).fetchone()[0]
        if existing == 0:
            db.execute(
                "INSERT INTO vo2_estimates(ts, estimate, method, verified) VALUES(?,?,?,1)",
                (time.time(), config.USER["verified_vo2max"], "lab_verified")
            )
            db.commit()
            log.info(f"Stored verified VO2 max: {config.USER['verified_vo2max']}")
    return db


class SessionDB:
    """Context-managed session recorder."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.session_id: Optional[int] = None
        self._buffer: list[tuple] = []
        self._flush_every = 10  # flush to DB every N points

    def start_session(self) -> int:
        cur = self.db.execute(
            "INSERT INTO sessions(started_at) VALUES(?)", (time.time(),)
        )
        self.db.commit()
        self.session_id = cur.lastrowid
        log.info(f"Session {self.session_id} started")
        return self.session_id

    def record_point(self, cadence, power, speed, hr, resistance, distance_m, elapsed_s):
        if not self.session_id:
            return
        self._buffer.append((
            self.session_id, time.time(),
            cadence, power, speed, hr, resistance, distance_m, elapsed_s
        ))
        if len(self._buffer) >= self._flush_every:
            self.flush()

    def flush(self):
        if not self._buffer:
            return
        self.db.executemany(
            """INSERT INTO datapoints
               (session_id, ts, cadence, power, speed, hr, resistance, distance_m, elapsed_s)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            self._buffer
        )
        self.db.commit()
        self._buffer.clear()

    def end_session(self, summary: dict, vo2_estimate: float):
        self.flush()
        if not self.session_id:
            return
        self.db.execute("""
            UPDATE sessions SET
                ended_at=?, duration_s=?, distance_m=?, avg_cadence=?, avg_power=?,
                max_power=?, avg_hr=?, max_hr=?, total_kcal=?, np_watts=?, tss=?, vo2max_est=?
            WHERE id=?
        """, (
            time.time(),
            summary.get("duration_s"),
            summary.get("distance_m"),
            summary.get("avg_cadence"),
            summary.get("avg_power"),
            summary.get("max_power"),
            summary.get("avg_hr"),
            summary.get("max_hr"),
            summary.get("total_kcal"),
            summary.get("np_watts"),
            summary.get("tss"),
            vo2_estimate,
            self.session_id,
        ))
        if vo2_estimate:
            self.db.execute(
                "INSERT INTO vo2_estimates(session_id, ts, estimate, method) VALUES(?,?,?,?)",
                (self.session_id, time.time(), vo2_estimate, "computed")
            )
        self.db.commit()
        log.info(f"Session {self.session_id} saved")

    def get_sessions(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute("""
            SELECT * FROM sessions
            WHERE ended_at IS NOT NULL
            ORDER BY started_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_session_datapoints(self, session_id: int) -> list[dict]:
        rows = self.db.execute("""
            SELECT * FROM datapoints WHERE session_id=? ORDER BY ts
        """, (session_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_vo2_history(self) -> list[dict]:
        rows = self.db.execute("""
            SELECT * FROM vo2_estimates ORDER BY ts
        """).fetchall()
        return [dict(r) for r in rows]

    def get_best_powers(self, durations_s: list[int] = None) -> dict[int, float]:
        """Compute all-time best average power for given durations."""
        if durations_s is None:
            durations_s = [5, 30, 60, 300, 600, 1200, 3600]
        results = {}
        all_points = self.db.execute(
            "SELECT ts, power FROM datapoints WHERE power IS NOT NULL ORDER BY session_id, ts"
        ).fetchall()
        if not all_points:
            return results

        powers = [r["power"] for r in all_points]

        for dur in durations_s:
            if len(powers) < dur:
                continue
            # Sliding window max mean
            best = 0.0
            window_sum = sum(powers[:dur])
            best = window_sum / dur
            for i in range(dur, len(powers)):
                window_sum += powers[i] - powers[i - dur]
                avg = window_sum / dur
                if avg > best:
                    best = avg
            results[dur] = round(best, 1)
        return results
