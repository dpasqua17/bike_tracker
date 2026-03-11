"""
Analytics: power estimation, VO2 max, and training load.

Power model (when bike doesn't report watts via FTMS 0x2A63):
  P = scale × (R/100)^r_exp × (cad/ref_cad)^c_exp

VO2 max methods:
  1. power_based   — Storer et al. formula from FTP/best efforts
  2. hr_astrand    — Åstrand-Rhyming submaximal cycle test adaptation
  3. hybrid        — weighted blend, anchored to verified value if available

Normalized Power and TSS (Coggan model):
  NP  = (mean of 30s rolling avg power^4)^0.25
  IF  = NP / FTP
  TSS = (duration_s × NP × IF) / (FTP × 3600) × 100
"""

import math
import logging
from collections import deque
from typing import Optional
import config

log = logging.getLogger(__name__)

_cfg = config.USER
_pm  = config.POWER_MODEL


# ─── Power Estimation ────────────────────────────────────────────────────────

def estimate_power(resistance: Optional[int], cadence: Optional[float]) -> Optional[float]:
    """
    Estimate power when FTMS doesn't report it.
    resistance: 0–100 (as reported by bike)
    cadence: RPM
    """
    if resistance is None or cadence is None:
        return None
    if cadence < 20 or resistance <= 0:
        return 0.0

    scale = _pm["scale_watts"]
    r_exp = _pm["resistance_exp"]
    c_exp = _pm["cadence_exp"]

    p = scale * ((resistance / 100) ** r_exp) * ((cadence / 90) ** c_exp)
    return max(0.0, round(p, 1))


# ─── VO2 Max Estimation ──────────────────────────────────────────────────────

def vo2max_from_power(ftp_watts: float, weight_kg: float) -> float:
    """
    Storer et al. (2000) adaptation for cycle ergometry.
    VO2max (ml/kg/min) = (FTP × 10.8 / weight_kg) + 7
    FTP ≈ 0.95 × best 20-min average power.
    """
    return (ftp_watts * 10.8 / weight_kg) + 7.0


def vo2max_from_hr_astrand(
    power_watts: float,
    steady_hr: float,
    weight_kg: float,
    age: int,
    sex: str = "male",
) -> float:
    """
    Åstrand-Rhyming submaximal cycle ergometer method.
    Best used when HR is 120–170 bpm during a 6-minute steady state effort.

    VO2 (L/min) from nomogram approximation (Maritz et al. adaptation):
      VO2 = (power_W × 0.01141) + 0.435
    Then correct for age (Åstrand factor table → linear approx):
      age_factor ≈ 1.0 - 0.0073 × (age - 25) for males
    Then VO2max = VO2_sub × (HRmax / steady_HR)
    """
    if power_watts <= 0 or steady_hr <= 0:
        return 0.0

    max_hr = _cfg.get("max_hr") or (220 - age)
    vo2_sub_L = (power_watts * 0.01141) + 0.435  # L/min
    age_factor = max(0.75, 1.0 - 0.0073 * (age - 25)) if sex == "male" else \
                 max(0.70, 1.0 - 0.0073 * (age - 25) + 0.05)

    vo2max_L = vo2_sub_L * (max_hr / steady_hr) * age_factor
    vo2max_ml_kg = (vo2max_L * 1000) / weight_kg
    return round(vo2max_ml_kg, 1)


def anchor_estimate(raw_estimate: float, verified: Optional[float]) -> float:
    """
    Blend computed estimate with verified lab value.
    As sessions accumulate, trust shifts toward computed.
    Simple version: 70/30 weighted average.
    """
    if not verified:
        return raw_estimate
    return round(0.7 * raw_estimate + 0.3 * verified, 1)


def classify_vo2max(vo2max: float, age: int, sex: str = "male") -> str:
    """Return fitness category string."""
    # Simplified Heyward norms for males (age 30-39 example)
    if sex == "male":
        if age < 30:
            thresholds = [(25, "Poor"), (33, "Fair"), (42, "Good"), (52, "Excellent")]
        elif age < 40:
            thresholds = [(23, "Poor"), (30, "Fair"), (38, "Good"), (48, "Excellent")]
        elif age < 50:
            thresholds = [(20, "Poor"), (26, "Fair"), (35, "Good"), (45, "Excellent")]
        else:
            thresholds = [(18, "Poor"), (22, "Fair"), (31, "Good"), (41, "Excellent")]
    else:  # female (simplified)
        if age < 30:
            thresholds = [(24, "Poor"), (31, "Fair"), (37, "Good"), (45, "Excellent")]
        elif age < 40:
            thresholds = [(20, "Poor"), (27, "Fair"), (33, "Good"), (42, "Excellent")]
        else:
            thresholds = [(17, "Poor"), (22, "Fair"), (29, "Good"), (36, "Excellent")]

    for threshold, label in thresholds:
        if vo2max <= threshold:
            return label
    return "Superior"


# ─── Normalized Power & TSS ──────────────────────────────────────────────────

class RollingMetrics:
    """
    Maintains running NP calculation using Coggan's 30-second rolling average.
    Feed power values in real-time; call np() to get current NP estimate.
    """

    def __init__(self, ftp: Optional[float] = None):
        self.ftp = ftp or _cfg.get("ftp_watts") or 200.0
        self._window: deque[float] = deque(maxlen=30)
        self._np_sum4: float = 0.0
        self._np_count: int = 0
        self.all_powers: list[float] = []

    def push(self, power_w: float):
        self._window.append(power_w)
        self.all_powers.append(power_w)
        if len(self._window) == 30:
            avg30 = sum(self._window) / 30
            self._np_sum4 += avg30 ** 4
            self._np_count += 1

    def np(self) -> float:
        """Current Normalized Power."""
        if self._np_count == 0:
            return 0.0
        return (self._np_sum4 / self._np_count) ** 0.25

    def intensity_factor(self) -> float:
        return self.np() / self.ftp

    def tss(self, duration_s: float) -> float:
        np_val = self.np()
        if np_val == 0 or duration_s == 0:
            return 0.0
        IF = np_val / self.ftp
        return (duration_s * np_val * IF) / (self.ftp * 3600) * 100

    def avg_power(self) -> float:
        if not self.all_powers:
            return 0.0
        return sum(self.all_powers) / len(self.all_powers)

    def max_power(self) -> float:
        return max(self.all_powers, default=0.0)

    def best_n_second_power(self, n: int) -> float:
        """Best average power over n seconds."""
        p = self.all_powers
        if len(p) < n:
            return self.avg_power()
        best = 0.0
        for i in range(len(p) - n + 1):
            avg = sum(p[i:i+n]) / n
            if avg > best:
                best = avg
        return best


# ─── Live VO2 max Updater ────────────────────────────────────────────────────

class LiveVO2Estimator:
    """
    Updates VO2 max estimate in real time as session data comes in.
    Uses hybrid method when possible.
    """

    def __init__(self, rolling: RollingMetrics):
        self.rolling = rolling
        self._weight = _cfg["weight_kg"]
        self._age = _cfg["age"]
        self._verified = _cfg.get("verified_vo2max")
        self._hr_samples: deque[tuple[float, float]] = deque(maxlen=360)  # (power, hr)
        self.current_estimate: Optional[float] = None

    def update(self, power_w: Optional[float], hr: Optional[int]):
        if power_w and hr and hr > 100:
            self._hr_samples.append((power_w, hr))

        method = config.VO2_METHOD

        if method == "power" or (method == "hybrid" and not self._hr_samples):
            ftp = _cfg.get("ftp_watts")
            if not ftp:
                # Estimate FTP from best 20-min power so far
                ftp = self.rolling.best_n_second_power(1200) * 0.95
            if ftp > 50:
                raw = vo2max_from_power(ftp, self._weight)
                self.current_estimate = anchor_estimate(raw, self._verified)

        if method in ("hr_astrand", "hybrid") and len(self._hr_samples) >= 180:
            # Use last 3 min of steady-state data
            recent = list(self._hr_samples)[-180:]
            avg_p = sum(r[0] for r in recent) / len(recent)
            avg_h = sum(r[1] for r in recent) / len(recent)
            # Only valid if HR is in sub-max zone (50-85% HRmax)
            max_hr = _cfg.get("max_hr") or (220 - self._age)
            if 0.50 * max_hr < avg_h < 0.90 * max_hr:
                raw_hr = vo2max_from_hr_astrand(avg_p, avg_h, self._weight, self._age)
                if method == "hr_astrand":
                    self.current_estimate = anchor_estimate(raw_hr, self._verified)
                else:
                    # Blend power + HR estimates
                    raw_p = self.current_estimate or raw_hr
                    blended = 0.5 * raw_p + 0.5 * raw_hr
                    self.current_estimate = anchor_estimate(blended, self._verified)

        return self.current_estimate
