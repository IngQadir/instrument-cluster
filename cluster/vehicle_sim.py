"""Simple vehicle model standing in for the ABS, engine and body ECUs.

It is deliberately simple (point-mass longitudinal dynamics, 6-speed
automatic gearbox, flashing turn lamps) - just enough to produce plausible,
consistent signals for the cluster.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

GEAR_RATIOS = (4.17, 2.34, 1.52, 1.14, 0.87, 0.69)
FINAL_DRIVE = 3.5
REVERSE_RATIO = 3.8
WHEEL_CIRC_M = 2.0
IDLE_RPM = 750.0
REV_LIMIT_RPM = 7000.0
TANK_L = 50.0
SELECTOR_CODES = {"P": 0, "R": 1, "N": 2, "D": 3}


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass
class DriverInput:
    throttle: float = 0.0
    brake: float = 0.0
    selector: str = "P"
    turn_left: bool = False
    turn_right: bool = False
    low_beam: bool = True
    high_beam: bool = False
    parking_brake: bool = True
    door_open: bool = False
    seatbelt_on: bool = True


class VehicleSim:
    FLASH_PERIOD_S = 0.7  # ~85 flashes per minute

    def __init__(self, odometer_km: float = 12345.6, fuel_pct: float = 62.0,
                 coolant_c: float = 20.0, outside_c: float = 7.5,
                 time_lapse: float = 1.0):
        """``time_lapse`` speeds up slow effects (fuel use, warm-up) for demos."""
        self.speed_mps = 0.0
        self.gear = 1
        self.rpm = IDLE_RPM
        self.odometer_km = odometer_km
        self.fuel_pct = fuel_pct
        self.coolant_c = coolant_c
        self.outside_c = outside_c
        self.time_lapse = time_lapse
        self.inp = DriverInput()
        self.faults = {"check_engine": False, "abs": False, "oil": False, "charging": False}
        self._flash_t = 0.0
        self._shift_timer = 0.0
        self.refuel_below_pct = None  # demo helper: auto-refuel when nearly empty

    @property
    def speed_kmh(self) -> float:
        return self.speed_mps * 3.6

    # -- model -------------------------------------------------------------
    def _gear_rpm(self, gear: int) -> float:
        return self.speed_mps / WHEEL_CIRC_M * 60.0 * GEAR_RATIOS[gear - 1] * FINAL_DRIVE

    def _auto_shift(self, throttle: float, dt: float) -> None:
        self._shift_timer = max(0.0, self._shift_timer - dt)
        if self._shift_timer > 0:
            return
        up = 1900.0 + 3800.0 * throttle
        down = 1100.0 + 1500.0 * throttle
        if self.gear < len(GEAR_RATIOS) and self._gear_rpm(self.gear) > up:
            self.gear += 1
            self._shift_timer = 0.8
        elif self.gear > 1 and self._gear_rpm(self.gear) < down \
                and self._gear_rpm(self.gear - 1) < 0.9 * up:
            self.gear -= 1
            self._shift_timer = 0.8

    def step(self, dt: float, inp: DriverInput) -> None:
        self.inp = inp
        throttle = clamp(inp.throttle, 0.0, 1.0)
        brake = clamp(inp.brake, 0.0, 1.0)
        v = self.speed_mps

        drive = inp.selector in ("D", "R") and not inp.parking_brake
        v_max = 72.0 if inp.selector == "D" else 8.0
        accel = throttle * 4.0 * max(0.0, 1.0 - v / v_max) if drive else 0.0
        accel -= brake * 9.0
        if inp.parking_brake:
            accel -= 3.0
        if inp.selector == "P":
            accel -= 6.0  # park lock
        if v > 0:
            accel -= 0.12 + 0.00035 * v * v  # rolling resistance + aero drag
        self.speed_mps = v = max(0.0, v + accel * dt)

        # gearbox and engine speed
        if inp.selector == "D":
            self._auto_shift(throttle, dt)
            ratio = GEAR_RATIOS[self.gear - 1]
        elif inp.selector == "R":
            self.gear = 1
            ratio = REVERSE_RATIO
        else:
            self.gear = 1
            ratio = None
        if ratio is not None:
            coupled = v / WHEEL_CIRC_M * 60.0 * ratio * FINAL_DRIVE
            target = max(coupled, IDLE_RPM + throttle * 1800.0)  # torque converter slip
        else:
            target = IDLE_RPM + throttle * 5500.0  # free revving in P/N
        target = min(target, REV_LIMIT_RPM)
        self.rpm += (target - self.rpm) * min(1.0, dt * 6.0)

        # slow effects
        self.odometer_km += v * dt / 1000.0
        litres_per_h = 0.8 + 25.0 * throttle * self.rpm / 6000.0
        self.fuel_pct -= litres_per_h / 3600.0 * dt * self.time_lapse / TANK_L * 100.0
        self.fuel_pct = max(0.0, self.fuel_pct)
        if self.refuel_below_pct is not None and self.fuel_pct < self.refuel_below_pct:
            self.fuel_pct = 80.0
        warm_rate = 0.01 * (self.rpm / 2000.0) * self.time_lapse
        self.coolant_c += (90.0 - self.coolant_c) * min(1.0, warm_rate * dt)

        # turn indicator flasher (BCM)
        if inp.turn_left or inp.turn_right:
            self._flash_t += dt
        else:
            self._flash_t = 0.0

    def _flash_on(self) -> bool:
        return (self._flash_t % self.FLASH_PERIOD_S) < self.FLASH_PERIOD_S / 2

    # -- CAN signal view ---------------------------------------------------
    def can_signals(self) -> Dict[str, Dict[str, float]]:
        inp = self.inp
        flash = self._flash_on()
        return {
            "ABS_VehicleSpeed": {
                "VehicleSpeed": self.speed_kmh,
                "ABS_Fault": int(self.faults["abs"]),
            },
            "ECM_Engine": {
                "EngineSpeed": self.rpm,
                "CoolantTemp": self.coolant_c,
                "FuelLevel": self.fuel_pct,
                "GearSelector": SELECTOR_CODES[inp.selector],
                "ActualGear": self.gear if inp.selector == "D" else 0,
                "CheckEngine": int(self.faults["check_engine"]),
                "OilPressureLow": int(self.faults["oil"]),
                "ChargingFault": int(self.faults["charging"]),
            },
            "BCM_Body": {
                "TurnLeftLamp": int(inp.turn_left and flash),
                "TurnRightLamp": int(inp.turn_right and flash),
                "LowBeam": int(inp.low_beam),
                "HighBeam": int(inp.high_beam),
                "DoorOpen": int(inp.door_open),
                "SeatbeltUnfastened": int(not inp.seatbelt_on),
                "ParkingBrake": int(inp.parking_brake),
                "OutsideTemp": self.outside_c,
                "Odometer": self.odometer_km,
            },
        }


# ---------------------------------------------------------------------------
# Drivers: a scripted demo drive cycle and a keyboard driver
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Segment:
    duration: float
    target_kmh: float
    selector: str = "D"
    turn: str = ""            # "L", "R" or ""
    high_beam: bool = False
    parking_brake: bool = False
    door_open: bool = False
    seatbelt_on: bool = True


DEMO_CYCLE: Tuple[Segment, ...] = (
    Segment(2.5, 0, "P", parking_brake=True, door_open=True, seatbelt_on=False),
    Segment(1.5, 0, "P", parking_brake=True),
    Segment(1.5, 0, "D"),
    Segment(10, 50),
    Segment(4, 50, turn="R"),
    Segment(12, 100),
    Segment(12, 130, high_beam=True),
    Segment(5, 130, turn="L", high_beam=True),
    Segment(10, 80),
    Segment(8, 30, turn="R"),
    Segment(8, 0),
    Segment(2, 0, "P", parking_brake=True),
)


class DemoDriver:
    """Follows the demo cycle with a simple speed controller (P + feed-forward)."""

    def __init__(self, cycle: Tuple[Segment, ...] = DEMO_CYCLE):
        self.cycle = cycle
        self.total = sum(s.duration for s in cycle)
        self.t = 0.0

    def segment(self) -> Segment:
        t = self.t % self.total
        for seg in self.cycle:
            if t < seg.duration:
                return seg
            t -= seg.duration
        return self.cycle[-1]

    def step(self, dt: float, speed_kmh: float) -> DriverInput:
        self.t += dt
        seg = self.segment()
        err = seg.target_kmh - speed_kmh
        throttle = brake = 0.0
        if seg.selector == "D" and seg.target_kmh > 0:
            throttle = clamp(0.05 * err + 0.0022 * seg.target_kmh, 0.0, 1.0)
        if err < -3.0:
            brake = clamp(-0.04 * (err + 3.0), 0.0, 1.0)
        if seg.target_kmh == 0 and speed_kmh > 0.1:
            brake = max(brake, 0.35)
        return DriverInput(
            throttle=throttle, brake=brake, selector=seg.selector,
            turn_left=seg.turn == "L", turn_right=seg.turn == "R",
            low_beam=True, high_beam=seg.high_beam,
            parking_brake=seg.parking_brake, door_open=seg.door_open,
            seatbelt_on=seg.seatbelt_on,
        )


class ManualDriver:
    """Keyboard driver. The app feeds it key states each frame."""

    def __init__(self):
        # start in P with the parking brake released: press D and W to drive off
        self.inp = DriverInput(selector="P", parking_brake=False)

    def step(self, dt: float, accel_pressed: bool, brake_pressed: bool) -> DriverInput:
        rate = 1.5
        self.inp.throttle = clamp(self.inp.throttle + (rate if accel_pressed else -2 * rate) * dt, 0, 1)
        self.inp.brake = clamp(self.inp.brake + (rate if brake_pressed else -3 * rate) * dt, 0, 1)
        return self.inp
