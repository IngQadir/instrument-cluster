"""DBC, CAN transmit/receive and signal timeout tests."""
import can
import cantools
import pytest

from cluster import config
from cluster.can_io import CanRx, CanTx
from cluster.signals import SignalStore
from cluster.vehicle_sim import DriverInput, VehicleSim


@pytest.fixture(scope="module")
def db():
    return cantools.database.load_file(str(config.DBC_PATH))


@pytest.fixture
def chain(db, request):
    ch = f"test_{request.node.name}"
    tx_bus = can.Bus(interface="virtual", channel=ch)
    rx_bus = can.Bus(interface="virtual", channel=ch)
    store = SignalStore.from_dbc(db, config.MESSAGES)
    yield CanTx(tx_bus, db, config.MESSAGES), CanRx(rx_bus, db, store), store
    tx_bus.shutdown()
    rx_bus.shutdown()


def test_dbc_has_expected_messages(db):
    ids = {m.name: m.frame_id for m in db.messages}
    assert ids == {"ABS_VehicleSpeed": 0x100, "ECM_Engine": 0x101, "BCM_Body": 0x200}
    for m in db.messages:
        assert m.length == 8


def test_speed_roundtrip_resolution(db):
    msg = db.get_message_by_name("ABS_VehicleSpeed")
    data = msg.encode({"VehicleSpeed": 123.45, "ABS_Fault": 0, "ABS_AliveCounter": 3})
    out = msg.decode(data)
    assert out["VehicleSpeed"] == pytest.approx(123.45, abs=0.01)
    assert out["ABS_AliveCounter"] == 3


def test_sim_values_arrive_at_cluster(chain):
    tx, rx, store = chain
    sim = VehicleSim()
    sim.speed_mps = 100 / 3.6
    sim.fuel_pct = 40.0
    sim.step(0.0, DriverInput(selector="D", parking_brake=False, high_beam=True))
    tx.service(0.0, sim.can_signals())
    rx.poll(0.0)
    assert store.get("VehicleSpeed", 0.0) == pytest.approx(100, abs=0.01)
    assert store.get("FuelLevel", 0.0) == pytest.approx(40, abs=0.5)
    assert store.get("GearSelector", 0.0) == 3
    assert store.get("HighBeam", 0.0) == 1
    assert store.get("Odometer", 0.0) == pytest.approx(12345.6, abs=0.1)


def test_tx_respects_cycle_times(chain):
    tx, rx, store = chain
    sim = VehicleSim()
    t, dt = 0.0, 0.005
    while t < 1.0 - 1e-9:  # 1 s of bus traffic
        tx.service(t, sim.can_signals())
        t += dt
    rx.poll(t)
    assert store.rx_count("ABS_VehicleSpeed") == 50   # 20 ms
    assert store.rx_count("ECM_Engine") == 50         # 20 ms
    assert store.rx_count("BCM_Body") == 10           # 100 ms
    assert store.counter_errors("ABS_VehicleSpeed") == 0


def test_signal_invalid_before_first_frame_and_after_timeout(chain):
    tx, rx, store = chain
    assert store.get("VehicleSpeed", 0.0) is None
    tx.service(0.0, VehicleSim().can_signals())
    rx.poll(0.0)
    assert store.get("VehicleSpeed", 0.1) is not None
    tx.muted.add("ABS_VehicleSpeed")
    t = 0.0
    while t < 0.5:
        t += 0.01
        tx.service(t, VehicleSim().can_signals())
        rx.poll(t)
    assert store.get("VehicleSpeed", t) is None          # timed out
    assert store.get("EngineSpeed", t) is not None       # other ECUs unaffected
    assert "ABS_VehicleSpeed" in store.invalid_messages(t)


def test_alive_counter_jump_is_counted(db):
    store = SignalStore.from_dbc(db, config.MESSAGES)
    for i, cnt in enumerate([0, 1, 2, 5, 6]):
        store.update("ABS_VehicleSpeed", {"VehicleSpeed": 0, "ABS_AliveCounter": cnt}, i * 0.02)
    assert store.counter_errors("ABS_VehicleSpeed") == 1


def test_out_of_range_values_are_clamped(chain):
    tx, rx, store = chain
    sigs = VehicleSim().can_signals()
    sigs["ABS_VehicleSpeed"]["VehicleSpeed"] = 999.0
    sigs["ECM_Engine"]["FuelLevel"] = -5.0
    tx.service(0.0, sigs)
    rx.poll(0.0)
    assert store.get("VehicleSpeed", 0.0) == pytest.approx(300)
    assert store.get("FuelLevel", 0.0) == 0
