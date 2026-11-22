"""Cluster HMI logic: turns raw signals into what the driver should see.

Pure functions, no pygame - so all display rules are unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import config
from .signals import SignalStore

SELECTOR_NAMES = {0: "P", 1: "R", 2: "N", 3: "D"}

# key, label, ISO-style colour class (red = stop/critical, amber = warning, green/blue = info)
TELLTALES: Tuple[Tuple[str, str, str], ...] = (
    ("low_beam", "LOW", "green"),
    ("high_beam", "HIGH", "blue"),
    ("parking_brake", "(P)", "red"),
    ("seatbelt", "BELT", "red"),
    ("door", "DOOR", "amber"),
    ("abs", "ABS", "amber"),
    ("check_engine", "ENG", "amber"),
    ("oil", "OIL", "red"),
    ("battery", "BATT", "red"),
    ("low_fuel", "FUEL", "amber"),
    ("coolant", "TEMP", "red"),
)


@dataclass
class ClusterView:
    speed_kmh: Optional[float] = None
    rpm: Optional[float] = None
    coolant_c: Optional[float] = None
    fuel_pct: Optional[float] = None
    outside_c: Optional[float] = None
    odometer_km: Optional[float] = None
    selector: Optional[str] = None
    actual_gear: Optional[int] = None
    turn_left: bool = False
    turn_right: bool = False
    telltales: Dict[str, bool] = field(default_factory=dict)
    messages: List[Tuple[str, str]] = field(default_factory=list)  # (severity, text)

    @property
    def gear_text(self) -> str:
        if self.selector is None:
            return "-"
        if self.selector == "D" and self.actual_gear:
            return f"D{self.actual_gear}"
        return self.selector


def build_view(store: SignalStore, now: float) -> ClusterView:
    g = lambda name: store.get(name, now)  # noqa: E731
    blink = (now % 1.0) < 0.5

    v = ClusterView(
        speed_kmh=g("VehicleSpeed"),
        rpm=g("EngineSpeed"),
        coolant_c=g("CoolantTemp"),
        fuel_pct=g("FuelLevel"),
        outside_c=g("OutsideTemp"),
        odometer_km=g("Odometer"),
    )
    sel = g("GearSelector")
    v.selector = SELECTOR_NAMES.get(int(sel)) if sel is not None else None
    gear = g("ActualGear")
    v.actual_gear = int(gear) if gear is not None else None
    v.turn_left = bool(g("TurnLeftLamp"))
    v.turn_right = bool(g("TurnRightLamp"))

    lost = store.invalid_messages(now)
    abs_lost = "ABS_VehicleSpeed" in lost
    ecm_lost = "ECM_Engine" in lost
    speed = v.speed_kmh or 0.0
    belt_open = bool(g("SeatbeltUnfastened"))

    t = {
        "low_beam": bool(g("LowBeam")),
        "high_beam": bool(g("HighBeam")),
        "parking_brake": bool(g("ParkingBrake")),
        # seat belt reminder: steady when stationary, flashing above 20 km/h
        "seatbelt": belt_open and (speed < config.SEATBELT_BLINK_KMH or blink),
        "door": bool(g("DoorOpen")),
        # a lost ABS message must light the ABS lamp - the driver can't trust ABS
        "abs": abs_lost or bool(g("ABS_Fault")),
        "check_engine": ecm_lost or bool(g("CheckEngine")),
        "oil": bool(g("OilPressureLow")),
        "battery": bool(g("ChargingFault")),
        "low_fuel": v.fuel_pct is not None and v.fuel_pct < config.LOW_FUEL_PCT,
        "coolant": v.coolant_c is not None and v.coolant_c > config.COOLANT_HOT_C,
    }
    v.telltales = t

    msgs: List[Tuple[str, str]] = []
    for name in lost:
        msgs.append(("red", f"{config.MESSAGE_SOURCE.get(name, name)}: no signal"))
    if t["coolant"]:
        msgs.append(("red", "Engine overheating - stop safely"))
    if t["parking_brake"] and speed > 5:
        msgs.append(("red", "Release parking brake"))
    if t["oil"]:
        msgs.append(("red", "Oil pressure low - stop engine"))
    if t["door"]:
        msgs.append(("amber", "Door open"))
    if t["low_fuel"]:
        msgs.append(("amber", "Low fuel - refuel soon"))
    if bool(g("CheckEngine")):
        msgs.append(("amber", "Engine fault - visit workshop"))
    v.messages = msgs
    return v
