# Bike Tracker

Real-time FTMS cycling dashboard for the JOROTO X4S (and any FTMS-compatible trainer).
Built for Arch Linux / Omarchy. Python 3.12+.

## Stack

| Layer | Tech |
|-------|------|
| BLE | `bleak` (async, cross-platform GATT) |
| Event loop | `qasync` (asyncio ↔ PyQt6 bridge) |
| UI | PyQt6 + pyqtgraph |
| Storage | SQLite (WAL mode, ~1 row/sec) |
| Analytics | Pure Python (Coggan NP/TSS, Storer VO2max) |

---

## Setup

```bash
# Arch deps
sudo pacman -S python python-pip bluez bluez-utils
sudo systemctl enable --now bluetooth

# Python deps (global, Arch-style)
pip install -r requirements.txt --break-system-packages

# Or virtualenv
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## Configure Before First Run

Edit `config.py`:

```python
USER = {
    "weight_kg": 82.0,          # YOUR weight
    "age": 35,                   # YOUR age
    "max_hr": 185,               # Measured max HR (or leave None → 220-age)
    "verified_vo2max": 48.2,     # Lab result if you have one — anchors estimates
    "ftp_watts": None,           # Set after a 20-min test: 0.95 × avg watts
    "resting_hr": 55,
}
```

---

## Running

```bash
python main.py
```

## Demo Mode

Launch a screenshot-ready one-hour sample ride with seeded history:

```bash
python -m demo.run_demo
```

1. Power on bike, ensure Bluetooth is active
2. Click **SCAN DEVICES** — pick your bike from the list
3. Click **START SESSION** when ready to ride
4. Click **STOP** to save the session

---

## Calibrating Power Estimates

The X4S may or may not report watts via FTMS (characteristic 0x2A63).
If it does, power readings are direct. If not, we estimate:

```
P = scale × (R/100)^r_exp × (cad/90)^c_exp
```

To calibrate:
1. Ride at a known steady state (e.g., 90 RPM, resistance 50)
2. Use a reference (smart trainer, metabolic test) to get actual watts
3. Adjust `POWER_MODEL["scale_watts"]` in config.py accordingly

A simpler calibration: do a 20-min FTP test, enter the result in `config.py`.
This feeds directly into VO2 max estimates via the Storer formula.

---

## VO2 Max Methods

| Method | When Used | Formula |
|--------|-----------|---------|
| `power` | FTP known, no HR | `(FTP × 10.8 / weight) + 7` (Storer 2000) |
| `hr_astrand` | 3+ min steady HR data | Åstrand-Rhyming submaximal nomogram |
| `hybrid` | Both available | 50/50 weighted blend |

If you have a verified lab value set in config, all estimates are blended 70% computed / 30% verified.

Set `VO2_METHOD = "hybrid"` (default) for best results.

---

## Data

SQLite at `sessions.db` (same directory). Schema:

- `sessions` — one row per ride, summary stats
- `datapoints` — ~1 Hz time series (cadence, power, HR, etc.)
- `vo2_estimates` — rolling VO2 max history including verified entries

---

## Architecture Notes

The asyncio/PyQt6 integration is the tricky bit. Key pattern:

```python
# BLE notification comes in on asyncio thread:
async def _async_on_data(self, bd: BikeData):
    self._data_ready.emit(bd, power_w)   # PyQt signal

# Received safely on Qt main thread:
@pyqtSlot(object, object)
def _on_data_ready(self, bd, power_w):
    self.live_tab.update_data(bd, power_w)
    # safe to touch all UI elements here
```

Never touch PyQt widgets from the asyncio/bleak thread.

---

## Extending

**Add resistance control:** `await ftms_client.set_resistance(level)` is already implemented.
Wire it to a slider in the UI.

**Export to .FIT:** Use `fitdecode` or `garmin-fit-sdk` to write sessions as .FIT files,
importable into GoldenCheetah, TrainingPeaks, or Garmin Connect.

**Power zones (Coggan):**
```python
zones = [0, 0.55, 0.75, 0.90, 1.05, 1.20, float('inf')]
zone = next(i for i, z in enumerate(zones) if power/ftp < z)
```

**Interval detection:** watch for sustained power > threshold for N seconds.
