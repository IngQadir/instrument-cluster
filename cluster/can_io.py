"""CAN transmit scheduler (simulated ECUs) and receiver (cluster side)."""
from __future__ import annotations

from typing import Dict, Set

import can

from .signals import SignalStore


def _clamp_to_dbc(msg, data: Dict[str, float]) -> Dict[str, float]:
    """Clamp every physical value into the DBC [min|max] range before encoding."""
    out = {}
    for sig in msg.signals:
        v = data.get(sig.name, 0)
        if sig.minimum is not None:
            v = max(sig.minimum, v)
        if sig.maximum is not None:
            v = min(sig.maximum, v)
        out[sig.name] = v
    return out


class CanTx:
    """Sends each message on its own cycle time, like real ECUs would."""

    def __init__(self, bus: can.BusABC, db, message_cfg: Dict[str, dict]):
        self.bus = bus
        self.db = db
        self._cfg = message_cfg
        self._next_due = {name: 0.0 for name in message_cfg}
        self._counters = {name: 0 for name in message_cfg}
        self.muted: Set[str] = set()  # fault injection: messages that stop being sent
        self.sent = 0

    def service(self, now: float, signals: Dict[str, Dict[str, float]]) -> None:
        for name, cfg in self._cfg.items():
            if now < self._next_due[name]:
                continue
            cycle = cfg["cycle_ms"] / 1000.0
            self._next_due[name] = max(self._next_due[name] + cycle, now)
            if name in self.muted:
                continue
            msg = self.db.get_message_by_name(name)
            data = dict(signals.get(name, {}))
            counter = cfg.get("counter")
            if counter:
                data[counter] = self._counters[name]
                self._counters[name] = (self._counters[name] + 1) % 16
            payload = msg.encode(_clamp_to_dbc(msg, data))
            self.bus.send(can.Message(arbitration_id=msg.frame_id, data=payload,
                                      is_extended_id=False))
            self.sent += 1


class CanRx:
    """Drains the bus without blocking and feeds decoded values into the store."""

    def __init__(self, bus: can.BusABC, db, store: SignalStore):
        self.bus = bus
        self.db = db
        self.store = store
        self.unknown_frames = 0
        self.received = 0

    def poll(self, now: float, max_frames: int = 500) -> int:
        n = 0
        while n < max_frames:
            frame = self.bus.recv(timeout=0)
            if frame is None:
                break
            n += 1
            try:
                msg = self.db.get_message_by_frame_id(frame.arbitration_id)
            except KeyError:
                self.unknown_frames += 1
                continue
            values = msg.decode(frame.data, decode_choices=False)
            self.store.update(msg.name, values, now)
            self.received += 1
        return n
