"""Signal store with per-message timeout and alive-counter monitoring.

The cluster never reads CAN frames directly. Every decoded value lands here,
and readers get ``None`` back when the source message is missing or stale,
so the HMI can show "---" instead of a frozen, wrong value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class MessageState:
    timeout_s: float
    counter_signal: Optional[str] = None
    last_rx: Optional[float] = None
    values: Dict[str, Any] = field(default_factory=dict)
    last_counter: Optional[int] = None
    counter_errors: int = 0
    rx_count: int = 0


class SignalStore:
    def __init__(self, signal_to_message: Dict[str, str], message_cfg: Dict[str, dict]):
        self._sig2msg = dict(signal_to_message)
        self._msgs = {
            name: MessageState(cfg["timeout_s"], cfg.get("counter"))
            for name, cfg in message_cfg.items()
        }

    @classmethod
    def from_dbc(cls, db, message_cfg: Dict[str, dict]) -> "SignalStore":
        mapping = {}
        for msg in db.messages:
            if msg.name in message_cfg:
                for sig in msg.signals:
                    mapping[sig.name] = msg.name
        return cls(mapping, message_cfg)

    # -- writing ---------------------------------------------------------
    def update(self, message: str, values: Dict[str, Any], now: float) -> None:
        st = self._msgs.get(message)
        if st is None:
            return
        if st.counter_signal and st.counter_signal in values:
            cnt = int(values[st.counter_signal])
            if st.last_counter is not None and cnt != (st.last_counter + 1) % 16:
                st.counter_errors += 1
            st.last_counter = cnt
        st.values.update(values)
        st.last_rx = now
        st.rx_count += 1

    # -- reading ---------------------------------------------------------
    def is_valid(self, message: str, now: float) -> bool:
        st = self._msgs[message]
        return st.last_rx is not None and (now - st.last_rx) <= st.timeout_s

    def get(self, signal: str, now: float, default: Any = None) -> Any:
        msg = self._sig2msg.get(signal)
        if msg is None or not self.is_valid(msg, now):
            return default
        return self._msgs[msg].values.get(signal, default)

    def invalid_messages(self, now: float):
        return [name for name in self._msgs if not self.is_valid(name, now)]

    def counter_errors(self, message: str) -> int:
        return self._msgs[message].counter_errors

    def rx_count(self, message: str) -> int:
        return self._msgs[message].rx_count
