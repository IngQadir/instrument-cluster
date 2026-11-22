"""Gauge geometry helpers (pure math, unit-tested)."""
from __future__ import annotations

import math
from typing import Tuple

START_DEG = 225.0  # lower-left, 0 on the dial
END_DEG = -45.0    # lower-right, full scale


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def value_to_angle(value: float, vmin: float, vmax: float,
                   start_deg: float = START_DEG, end_deg: float = END_DEG) -> float:
    """Map a value to a needle angle (degrees, counter-clockwise from +x)."""
    frac = (clamp(value, vmin, vmax) - vmin) / (vmax - vmin)
    return start_deg + frac * (end_deg - start_deg)


def polar(cx: float, cy: float, r: float, angle_deg: float) -> Tuple[float, float]:
    """Point on a circle in screen coordinates (y grows downwards)."""
    a = math.radians(angle_deg)
    return cx + r * math.cos(a), cy - r * math.sin(a)


def damp(current: float, target: float, dt: float, rate: float) -> float:
    """First-order needle damping so the pointer moves smoothly."""
    return current + (target - current) * min(1.0, dt * rate)
