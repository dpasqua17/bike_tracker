"""
User configuration — edit before first run.
All physiological values affect VO2 max estimates.
"""

USER = {
    "name": "Your Name",
    "weight_kg": 75.0,          # update to your actual weight
    "age": 30,                  # update to your age
    "max_hr": None,             # set if known; otherwise some formulas fall back to age-based estimates
    "verified_vo2max": None,    # set if you have a lab-tested value to anchor estimates
    "ftp_watts": None,           # set if you've done a 20-min FTP test (0.95 × 20min avg)
    "resting_hr": 60,            # for Karvonen HR reserve calculations
}

# Power estimation model for this bike (calibrate via a timed test)
# Without actual wattage from FTMS, we model: P = scale × (R/100)^r_exp × (cad/90)^c_exp
# These defaults are reasonable for a mid-range magnetic resistance bike.
# Override after a calibration ride against known reference.
POWER_MODEL = {
    "scale_watts": 250,          # power at resistance=100, cadence=90 RPM
    "resistance_exp": 1.4,       # resistance curve shape (>1 = progressive)
    "cadence_exp": 1.0,          # cadence contribution (linear is realistic)
    "use_ftms_power": True,      # prefer reported FTMS power if available (0x2A63)
}

# BLE scan timeout in seconds
BLE_SCAN_TIMEOUT = 10.0

# Preferred bike connection settings.
# The bike may advertise as JOROTO-X4S / Gerato X4S depending on firmware/app state.
BIKE = {
    "name": "JOROTO-X4S",
    "aliases": ["GERATO", "X4S", "JOROTO"],
    "address": None,
    "auto_connect": False,
    "connect_timeout": 15.0,
}

WATCH = {
    "name": "Forerunner 255",
    "aliases": ["GARMIN", "FORERUNNER", "255"],
    "auto_connect": True,
    "connect_timeout": 10.0,
}

# Safety: keep FTMS control-point writes disabled unless you explicitly opt in.
ALLOW_BIKE_CONTROL_WRITES = False

# DB path
DB_PATH = "sessions.db"

# UI update rate (Hz) — 2Hz is plenty for display
UI_HZ = 2

# VO2 max estimation method: "power", "hr_astrand", "hybrid"
VO2_METHOD = "hybrid"
