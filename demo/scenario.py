"""Deterministic demo data for screenshot-ready UI states."""

from __future__ import annotations

import math
import time
from typing import Optional

from ble.ftms_client import BikeData


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _session_targets(elapsed_s: int, duration_s: int) -> tuple[float, float, int]:
    """Return cadence, power, and target HR for a structured endurance workout."""
    progress = elapsed_s / max(duration_s, 1)

    if progress < 0.10:
        cadence = 82 + 10 * progress / 0.10
        power = 135 + 45 * progress / 0.10
        hr = 118 + int(18 * progress / 0.10)
    elif progress < 0.25:
        cadence = 92 + 2 * math.sin(elapsed_s / 30)
        power = 188 + 12 * math.sin(elapsed_s / 45)
        hr = 138 + int(6 * math.sin(elapsed_s / 50))
    elif progress < 0.58:
        interval_phase = ((elapsed_s - int(duration_s * 0.25)) // 180) % 2
        if interval_phase == 0:
            cadence = 98 + 3 * math.sin(elapsed_s / 18)
            power = 248 + 16 * math.sin(elapsed_s / 24)
            hr = 158 + int(5 * math.sin(elapsed_s / 30))
        else:
            cadence = 88 + 2 * math.sin(elapsed_s / 20)
            power = 205 + 10 * math.sin(elapsed_s / 28)
            hr = 148 + int(4 * math.sin(elapsed_s / 24))
    elif progress < 0.80:
        cadence = 94 + 4 * math.sin(elapsed_s / 22)
        power = 228 + 14 * math.sin(elapsed_s / 20)
        hr = 154 + int(5 * math.sin(elapsed_s / 26))
    elif progress < 0.92:
        cadence = 90 + 3 * math.sin(elapsed_s / 25)
        power = 196 + 10 * math.sin(elapsed_s / 30)
        hr = 146 + int(4 * math.sin(elapsed_s / 28))
    else:
        cooldown = (progress - 0.92) / 0.08
        cadence = 88 - 16 * cooldown
        power = 182 - 72 * cooldown
        hr = 143 - int(23 * cooldown)

    return cadence, power, hr


def generate_demo_samples(duration_s: int = 3600) -> list[tuple[BikeData, float, int]]:
    """Generate a one-hour ride with plausible training dynamics."""
    samples: list[tuple[BikeData, float, int]] = []
    now = time.time()
    distance_m = 0.0
    kcal = 0.0

    for elapsed_s in range(1, duration_s + 1):
        cadence, power, hr = _session_targets(elapsed_s, duration_s)
        cadence += 0.8 * math.sin(elapsed_s / 7)
        power += 4.0 * math.sin(elapsed_s / 9)
        hr += int(1.5 * math.sin(elapsed_s / 13))

        cadence = _clamp(cadence, 55, 110)
        power = _clamp(power, 90, 320)
        hr = int(_clamp(hr, 98, 176))

        resistance = int(round(_clamp(28 + (power - 110) / 4.2, 20, 72)))
        speed = _clamp(24.0 + (cadence - 80) * 0.16 + (power - 160) * 0.015, 18.0, 42.0)

        distance_m += speed * (1000 / 3600)
        kcal += power * 0.00024

        samples.append((
            BikeData(
                timestamp=now - (duration_s - elapsed_s),
                instantaneous_speed_kmh=round(speed, 1),
                instantaneous_cadence_rpm=round(cadence, 1),
                total_distance_m=int(distance_m),
                resistance_level=resistance,
                instantaneous_power_w=int(round(power)),
                total_energy_kcal=int(round(kcal)),
                heart_rate_bpm=hr,
                elapsed_time_s=elapsed_s,
            ),
            round(power, 1),
            hr,
        ))

    return samples


def _insert_completed_session(
    db,
    *,
    started_at: float,
    samples: list[tuple[BikeData, float, int]],
    vo2_estimate: float,
) -> None:
    avg_power = sum(power for _, power, _ in samples) / len(samples)
    max_power = max(power for _, power, _ in samples)
    avg_hr = sum(hr for _, _, hr in samples) / len(samples)
    max_hr = max(hr for _, _, hr in samples)
    avg_cadence = sum(sample.cadence or 0 for sample, _, _ in samples) / len(samples)
    total_kcal = samples[-1][0].total_energy_kcal or 0
    distance_m = samples[-1][0].total_distance_m or 0
    np_watts = avg_power * 1.03
    tss = (len(samples) / 3600) * (np_watts / 220) * (np_watts / 220) * 100

    cur = db.execute(
        """
        INSERT INTO sessions(
            started_at, ended_at, duration_s, distance_m, avg_cadence, avg_power,
            max_power, avg_hr, max_hr, total_kcal, np_watts, tss, vo2max_est
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            started_at,
            started_at + len(samples),
            len(samples),
            distance_m,
            avg_cadence,
            avg_power,
            max_power,
            avg_hr,
            max_hr,
            total_kcal,
            np_watts,
            tss,
            vo2_estimate,
        ),
    )
    session_id = cur.lastrowid

    datapoints = [
        (
            session_id,
            started_at + (sample.elapsed_time_s or 0),
            sample.cadence,
            int(round(power)),
            sample.speed,
            hr,
            sample.resistance_level,
            sample.total_distance_m,
            sample.elapsed_time_s,
        )
        for sample, power, hr in samples
    ]
    db.executemany(
        """
        INSERT INTO datapoints(
            session_id, ts, cadence, power, speed, hr, resistance, distance_m, elapsed_s
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        datapoints,
    )
    db.execute(
        "INSERT INTO vo2_estimates(session_id, ts, estimate, method) VALUES(?,?,?,?)",
        (session_id, started_at + len(samples), vo2_estimate, "demo_computed"),
    )


def seed_demo_database(db) -> None:
    """Seed ended sessions so the history tab has useful context."""
    now = time.time()
    session_specs = [
        (now - 9 * 86400, 3000, 45.6),
        (now - 6 * 86400, 3300, 46.2),
        (now - 3 * 86400, 3600, 46.9),
    ]

    for started_at, duration_s, vo2_estimate in session_specs:
        samples = generate_demo_samples(duration_s=duration_s)
        _insert_completed_session(
            db,
            started_at=started_at,
            samples=samples,
            vo2_estimate=vo2_estimate,
        )

    db.execute(
        "INSERT INTO vo2_estimates(ts, estimate, method, verified) VALUES(?,?,?,1)",
        (now - 14 * 86400, 45.0, "lab_verified_demo"),
    )
    db.commit()
