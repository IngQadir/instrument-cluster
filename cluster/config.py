"""Central configuration: CAN message timing, display limits, thresholds."""
from pathlib import Path

DBC_PATH = Path(__file__).resolve().parent.parent / "dbc" / "vehicle.dbc"

# Per-message transmit cycle, receive timeout and (optional) alive-counter signal.
# Timeout is ~10x the cycle time, a common choice for cluster comm monitoring.
MESSAGES = {
    "ABS_VehicleSpeed": {"cycle_ms": 20, "timeout_s": 0.20, "counter": "ABS_AliveCounter"},
    "ECM_Engine": {"cycle_ms": 20, "timeout_s": 0.20, "counter": "ECM_AliveCounter"},
    "BCM_Body": {"cycle_ms": 100, "timeout_s": 0.50, "counter": None},
}

# Which ECU is responsible for a message (used in "comm lost" texts).
MESSAGE_SOURCE = {
    "ABS_VehicleSpeed": "ABS",
    "ECM_Engine": "Engine ECU",
    "BCM_Body": "Body ECU",
}

SPEED_MAX_KMH = 260
RPM_MAX = 8000
RPM_REDLINE = 6500
LOW_FUEL_PCT = 12.0
COOLANT_HOT_C = 115.0
SEATBELT_BLINK_KMH = 20.0

SCREEN_SIZE = (1280, 480)
FPS = 60
BULB_CHECK_S = 1.8  # lamp test + needle sweep at start-up
