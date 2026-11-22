"""HMI logic, gauge math and vehicle model tests."""
import pytest

from cluster import config
from cluster.gauges import END_DEG, START_DEG, damp, polar, value_to_angle
from cluster.logic import build_view
from cluster.signals import SignalStore
from cluster.vehicle_sim import DemoDriver, DriverInput, ManualDriver, VehicleSim

MAP = {
    "VehicleSpeed": "ABS_VehicleSpeed", "ABS_Fault": "ABS_VehicleSpeed",
    "EngineSpeed": "ECM_Engine", "FuelLevel": "ECM_Engine", "CoolantTemp": "ECM_Engine",
    "GearSelector": "ECM_Engine", "ActualGear": "ECM_Engine", "CheckEngine": "ECM_Engine",
    "SeatbeltUnfastened": "BCM_Body", "ParkingBrake": "BCM_Body", "DoorOpen": "BCM_Body",
}


def make_store(abs_=None, ecm=None, bcm=None, t=0.0):
    s = SignalStore(MAP, config.MESSAGES)
    base_ecm = {"EngineSpeed": 800, "FuelLevel": 50, "CoolantTemp": 90,
                "GearSelector": 3, "ActualGear": 2, "CheckEngine": 0}
    if abs_ is not False:
        s.update("ABS_VehicleSpeed", {"VehicleSpeed": 0, "ABS_Fault": 0, **(abs_ or {})}, t)
    s.update("ECM_Engine", {**base_ecm, **(ecm or {})}, t)
    s.update("BCM_Body", {"SeatbeltUnfastened": 0, "ParkingBrake": 0, "DoorOpen": 0, **(bcm or {})}, t)
    return s


# -- gauge math ------------------------------------------------------------
def test_value_to_angle_endpoints_and_clamping():
    assert value_to_angle(0, 0, 260) == START_DEG
    assert value_to_angle(260, 0, 260) == END_DEG
    assert value_to_angle(130, 0, 260) == pytest.approx(90.0)   # straight up
    assert value_to_angle(-10, 0, 260) == START_DEG
    assert value_to_angle(999, 0, 260) == END_DEG


def test_polar_screen_coordinates():
    x, y = polar(100, 100, 10, 90)
    assert (x, y) == pytest.approx((100, 90))   # up is smaller y on screen


def test_damping_converges_without_overshoot():
    v = 0.0
    for _ in range(120):
        v = damp(v, 100.0, 1 / 60, 9.0)
        assert v <= 100.0
    assert v == pytest.approx(100.0, abs=0.1)


# -- HMI logic ---------------------------------------------------------------
def test_gear_text():
    v = build_view(make_store(ecm={"GearSelector": 3, "ActualGear": 4}), 0.0)
    assert v.gear_text == "D4"
    v = build_view(make_store(ecm={"GearSelector": 1, "ActualGear": 0}), 0.0)
    assert v.gear_text == "R"


def test_low_fuel_telltale_and_message():
    v = build_view(make_store(ecm={"FuelLevel": 8}), 0.0)
    assert v.telltales["low_fuel"]
    assert ("amber", "Low fuel - refuel soon") in v.messages
    assert not build_view(make_store(ecm={"FuelLevel": 30}), 0.0).telltales["low_fuel"]


def test_abs_lamp_on_when_abs_message_lost():
    store = make_store(abs_=False)
    v = build_view(store, 0.0)
    assert v.speed_kmh is None
    assert v.telltales["abs"]
    assert any("ABS" in m for _, m in v.messages)


def test_engine_values_go_invalid_after_timeout():
    store = make_store(t=0.0)
    assert build_view(store, 0.1).rpm == 800
    v = build_view(store, 1.0)  # everything stale
    assert v.rpm is None and v.fuel_pct is None
    assert v.telltales["check_engine"]


def test_seatbelt_steady_when_slow_flashing_when_fast():
    def belt(speed, t):
        # frames received at time t, evaluated right away (no timeout)
        store = make_store(abs_={"VehicleSpeed": speed}, bcm={"SeatbeltUnfastened": 1}, t=t)
        return build_view(store, t).telltales["seatbelt"]

    assert belt(5, 0.1) and belt(5, 0.6)              # steady below 20 km/h
    assert belt(50, 0.1) and not belt(50, 0.6)        # 1 Hz flashing above


def test_parking_brake_warning_only_when_moving():
    still = build_view(make_store(bcm={"ParkingBrake": 1}), 0.0)
    moving = build_view(make_store(abs_={"VehicleSpeed": 30}, bcm={"ParkingBrake": 1}), 0.0)
    assert not any("parking" in m for _, m in still.messages)
    assert ("red", "Release parking brake") in moving.messages


# -- vehicle model -------------------------------------------------------------
def test_no_motion_in_park():
    sim = VehicleSim()
    for _ in range(300):
        sim.step(0.02, DriverInput(throttle=1.0, selector="P", parking_brake=True))
    assert sim.speed_kmh == 0
    assert sim.rpm > 3000          # free revving


def test_accelerates_and_upshifts_in_drive():
    sim = VehicleSim()
    for _ in range(500):  # 10 s
        sim.step(0.02, DriverInput(throttle=0.6, selector="D", parking_brake=False))
    assert sim.speed_kmh > 60
    assert sim.gear >= 3
    assert sim.rpm < 7000


def test_turn_lamp_flashes():
    sim = VehicleSim()
    states = []
    for _ in range(100):
        sim.step(0.02, DriverInput(turn_left=True))
        states.append(sim.can_signals()["BCM_Body"]["TurnLeftLamp"])
    assert 0 in states and 1 in states
    assert all(s["TurnRightLamp"] == 0 for s in [sim.can_signals()["BCM_Body"]])


def test_demo_driver_reaches_cruise_speed():
    sim, drv = VehicleSim(), DemoDriver()
    while drv.t < 30:  # end of the 100 km/h segment
        sim.step(0.02, drv.step(0.02, sim.speed_kmh))
    assert 90 < sim.speed_kmh < 110


def test_keyboard_driver_w_accelerates_s_brakes():
    sim, drv = VehicleSim(), ManualDriver()
    drv.inp.selector = "D"
    for _ in range(250):  # hold W for 5 s
        sim.step(0.02, drv.step(0.02, accel_pressed=True, brake_pressed=False))
    v_fast = sim.speed_kmh
    assert v_fast > 40
    for _ in range(150):  # hold S for 3 s
        sim.step(0.02, drv.step(0.02, accel_pressed=False, brake_pressed=True))
    assert sim.speed_kmh < 5


def test_park_lock_holds_car():
    sim = VehicleSim()
    for _ in range(200):
        sim.step(0.02, DriverInput(throttle=1.0, selector="P", parking_brake=False))
    assert sim.speed_kmh == 0
