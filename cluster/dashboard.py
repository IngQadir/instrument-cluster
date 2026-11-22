"""pygame rendering of the instrument cluster."""
from __future__ import annotations

import datetime
import math
from typing import Dict, Optional, Tuple

import pygame
import pygame.gfxdraw

from . import config
from .gauges import END_DEG, START_DEG, damp, polar, value_to_angle
from .logic import TELLTALES, ClusterView

BG = (7, 9, 13)
PANEL = (16, 19, 26)
FG = (225, 230, 236)
DIM = (95, 102, 112)
GHOST = (30, 34, 41)
ACCENT = (0, 185, 255)
NEEDLE = (255, 72, 36)
COLORS = {
    "red": (238, 52, 48),
    "amber": (255, 168, 0),
    "green": (40, 210, 90),
    "blue": (70, 130, 255),
}

Color = Tuple[int, int, int]


def _font(size: int, bold: bool = False) -> pygame.font.Font:
    f = pygame.font.Font(None, size)
    f.set_bold(bold)
    return f


def _text(surf, font, txt, color, center=None, midleft=None, midright=None):
    img = font.render(txt, True, color)
    rect = img.get_rect()
    if center:
        rect.center = center
    elif midleft:
        rect.midleft = midleft
    elif midright:
        rect.midright = midright
    surf.blit(img, rect)
    return rect


def _thick_arc(surf, color, center, radius, width, a_from, a_to):
    """Arc from angle a_from down to a_to (degrees, a_from > a_to), drawn as a polygon band."""
    if a_from - a_to < 0.5:
        return
    steps = max(2, int((a_from - a_to) / 2))
    outer, inner = [], []
    for i in range(steps + 1):
        a = a_from - (a_from - a_to) * i / steps
        outer.append(polar(center[0], center[1], radius, a))
        inner.append(polar(center[0], center[1], radius - width, a))
    pts = outer + inner[::-1]
    pygame.gfxdraw.filled_polygon(surf, pts, color)
    pygame.gfxdraw.aapolygon(surf, pts, color)


class Gauge:
    def __init__(self, center, radius, vmin, vmax, major, minor, unit_label,
                 red_from=None, label_div=1, digits_font=26):
        self.center = center
        self.r = radius
        self.vmin, self.vmax = vmin, vmax
        self.major, self.minor = major, minor
        self.unit_label = unit_label
        self.red_from = red_from
        self.label_div = label_div
        self.display = float(vmin)
        self._static: Optional[pygame.Surface] = None
        self._digits_font = digits_font

    def _build_static(self) -> pygame.Surface:
        pad = 12
        size = 2 * (self.r + pad)
        s = pygame.Surface((size, size), pygame.SRCALPHA)
        c = (self.r + pad, self.r + pad)
        # dial face
        pygame.gfxdraw.filled_circle(s, c[0], c[1], self.r, PANEL)
        for dr, col in ((0, (60, 66, 76)), (1, (60, 66, 76)), (7, (28, 32, 40))):
            pygame.gfxdraw.aacircle(s, c[0], c[1], self.r - dr, col)
        # background track for the lit arc
        _thick_arc(s, (30, 36, 46), c, self.r - 3, 5, START_DEG, END_DEG)
        if self.red_from is not None:
            a_red = value_to_angle(self.red_from, self.vmin, self.vmax)
            _thick_arc(s, COLORS["red"], c, self.r - 10, 10, a_red, END_DEG)
        font = _font(self._digits_font, bold=True)
        v = self.vmin
        while v <= self.vmax + 1e-6:
            a = value_to_angle(v, self.vmin, self.vmax)
            is_major = abs((v - self.vmin) % self.major) < 1e-6
            hot = self.red_from is not None and v >= self.red_from
            col = COLORS["red"] if hot else (FG if is_major else DIM)
            r_out = self.r - 11
            r_in = self.r - (30 if is_major else 20)
            p1, p2 = polar(*c, r_out, a), polar(*c, r_in, a)
            pygame.draw.line(s, col, p1, p2, 3 if is_major else 1)
            if is_major:
                _text(s, font, f"{int(v / self.label_div)}", col, center=polar(*c, self.r - 50, a))
            v += self.minor
        if self.unit_label:
            _text(s, _font(20), self.unit_label, DIM, center=(c[0], c[1] - self.r * 0.38))
        return s

    def update(self, target: Optional[float], dt: float) -> None:
        # lost signal -> needle returns to zero (never freeze on an old value)
        self.display = damp(self.display, self.vmin if target is None else target, dt, 9.0)

    def draw(self, screen: pygame.Surface) -> None:
        if self._static is None:
            self._static = self._build_static()
        cx, cy = self.center
        screen.blit(self._static, self._static.get_rect(center=self.center))
        a = value_to_angle(self.display, self.vmin, self.vmax)
        _thick_arc(screen, ACCENT, self.center, self.r - 3, 5, START_DEG, a)
        tip = polar(cx, cy, self.r - 22, a)
        tail = polar(cx, cy, 22, a + 180)
        left = polar(cx, cy, 6, a + 90)
        right = polar(cx, cy, 6, a - 90)
        pts = [tip, left, tail, right]
        pygame.gfxdraw.filled_polygon(screen, pts, NEEDLE)
        pygame.gfxdraw.aapolygon(screen, pts, NEEDLE)
        pygame.gfxdraw.filled_circle(screen, cx, cy, 15, (24, 27, 34))
        pygame.gfxdraw.aacircle(screen, cx, cy, 15, (80, 86, 96))


class Dashboard:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        w, h = screen.get_size()
        self.w, self.h = w, h
        self.speedo = Gauge((300, 245), 190, 0, config.SPEED_MAX_KMH, 20, 10, "")
        self.tacho = Gauge((w - 300, 245), 190, 0, config.RPM_MAX, 1000, 250,
                           "x1000 rpm", red_from=config.RPM_REDLINE, label_div=1000)
        self.f_big = _font(84, bold=True)
        self.f_gear = _font(72, bold=True)
        self.f_med = _font(32, bold=True)
        self.f_small = _font(24)
        self.f_tiny = _font(19)
        self.f_badge = _font(20, bold=True)
        self.trip_start_km: Optional[float] = None
        self._msg_t = 0.0

    # -- pieces -----------------------------------------------------------
    def _turn_arrow(self, center, direction: int, on: bool) -> None:
        cx, cy = center
        d = direction  # -1 left, +1 right
        pts = [(cx + d * 22, cy), (cx + d * 2, cy - 18), (cx + d * 2, cy - 8),
               (cx - d * 20, cy - 8), (cx - d * 20, cy + 8), (cx + d * 2, cy + 8),
               (cx + d * 2, cy + 18)]
        col = COLORS["green"] if on else GHOST
        pygame.gfxdraw.filled_polygon(self.screen, pts, col)
        pygame.gfxdraw.aapolygon(self.screen, pts, col)

    def _badges(self, telltales: Dict[str, bool]) -> None:
        bw, bh, gap = 52, 28, 6
        per_row = 6
        rows = [TELLTALES[:per_row], TELLTALES[per_row:]]
        for ri, row in enumerate(rows):
            total = len(row) * bw + (len(row) - 1) * gap
            x0 = self.w // 2 - total // 2
            y = 392 + ri * (bh + 8)
            for i, (key, label, cls) in enumerate(row):
                rect = pygame.Rect(x0 + i * (bw + gap), y, bw, bh)
                on = telltales.get(key, False)
                col = COLORS[cls] if on else GHOST
                if on:
                    pygame.draw.rect(self.screen, tuple(c // 5 for c in col), rect, border_radius=6)
                pygame.draw.rect(self.screen, col, rect, width=2, border_radius=6)
                _text(self.screen, self.f_badge, label, col, center=rect.center)

    def _bar(self, y, label_l, label_r, title, frac, color, invalid) -> None:
        x0, width, height = self.w // 2 - 120, 240, 12
        _text(self.screen, self.f_tiny, title, DIM, midright=(x0 - 30, y + height // 2))
        _text(self.screen, self.f_tiny, label_l, DIM, midright=(x0 - 8, y + height // 2))
        _text(self.screen, self.f_tiny, label_r, DIM, midleft=(x0 + width + 8, y + height // 2))
        pygame.draw.rect(self.screen, (34, 39, 48), (x0, y, width, height), border_radius=6)
        if not invalid:
            fw = max(0, min(width, int(width * frac)))
            if fw > 0:
                pygame.draw.rect(self.screen, color, (x0, y, fw, height), border_radius=6)
        else:
            _text(self.screen, self.f_tiny, "- - -", DIM, center=(x0 + width // 2, y + height // 2))
        for i in range(1, 4):
            x = x0 + width * i // 4
            pygame.draw.line(self.screen, BG, (x, y), (x, y + height - 1), 2)

    def _center_panel(self, v: ClusterView, dt: float, mode_text: str) -> None:
        cx = self.w // 2
        # top line: clock, mode, outside temperature
        _text(self.screen, self.f_med, datetime.datetime.now().strftime("%H:%M"), FG, center=(cx, 40))
        _text(self.screen, self.f_tiny, mode_text, ACCENT, center=(cx, 66))
        self._turn_arrow((cx - 110, 40), -1, v.turn_left)
        self._turn_arrow((cx + 110, 40), +1, v.turn_right)
        out = "--.- °C" if v.outside_c is None else f"{v.outside_c:.1f} °C"
        _text(self.screen, self.f_small, out, DIM, center=(cx, 92))

        # gear selector strip PRND
        x = cx - 72
        for s in "PRND":
            active = v.selector == s
            _text(self.screen, self.f_med, s, FG if active else GHOST, center=(x, 132))
            if active:
                pygame.draw.line(self.screen, ACCENT, (x - 10, 150), (x + 10, 150), 3)
            x += 48

        # fuel + coolant
        fuel = v.fuel_pct
        low = v.telltales.get("low_fuel", False)
        self._bar(182, "E", "F", "FUEL", (fuel or 0) / 100.0,
                  COLORS["amber"] if low else ACCENT, fuel is None)
        cool = v.coolant_c
        hot = v.telltales.get("coolant", False)
        frac = ((cool or 50) - 50) / (130 - 50)
        self._bar(214, "C", "H", "TEMP", frac, COLORS["red"] if hot else ACCENT, cool is None)

        # odometer + trip
        if v.odometer_km is not None and self.trip_start_km is None:
            self.trip_start_km = v.odometer_km
        odo = "------.- km" if v.odometer_km is None else f"{v.odometer_km:08.1f} km"
        trip = "---.- km" if v.odometer_km is None or self.trip_start_km is None \
            else f"{v.odometer_km - self.trip_start_km:.1f} km"
        _text(self.screen, self.f_tiny, "ODO", DIM, midright=(cx - 70, 262))
        _text(self.screen, self.f_small, odo, FG, midleft=(cx - 60, 262))
        _text(self.screen, self.f_tiny, "TRIP", DIM, midright=(cx - 70, 290))
        _text(self.screen, self.f_small, trip, FG, midleft=(cx - 60, 290))

        # driver message line (rotates every 2.5 s if several are active)
        self._msg_t += dt
        if v.messages:
            sev, msg = v.messages[int(self._msg_t / 2.5) % len(v.messages)]
            col = COLORS[sev]
            rect = pygame.Rect(0, 0, 320, 36)
            rect.center = (cx, 342)
            pygame.draw.rect(self.screen, tuple(c // 6 for c in col), rect, border_radius=8)
            pygame.draw.rect(self.screen, col, rect, width=2, border_radius=8)
            _text(self.screen, self.f_small, msg, col, center=rect.center)
            if len(v.messages) > 1:
                _text(self.screen, self.f_tiny, f"{len(v.messages)} messages", DIM, center=(cx, 372))

    # -- frame ------------------------------------------------------------
    def draw(self, v: ClusterView, t_since_start: float, dt: float, mode_text: str = "",
             hint: str = "") -> None:
        self.screen.fill(BG)
        bulb_check = t_since_start < config.BULB_CHECK_S
        if bulb_check:
            # needle sweep: up in first half, back down in second half
            ph = t_since_start / config.BULB_CHECK_S
            frac = math.sin(math.pi * ph)
            self.speedo.display = frac * config.SPEED_MAX_KMH
            self.tacho.display = frac * config.RPM_MAX
            telltales = {k: True for k, _, _ in TELLTALES}
            v = ClusterView(**{**v.__dict__, "telltales": telltales,
                               "turn_left": True, "turn_right": True, "messages": []})
        else:
            self.speedo.update(v.speed_kmh, dt)
            self.tacho.update(v.rpm, dt)

        self.speedo.draw(self.screen)
        self.tacho.draw(self.screen)

        # digital readouts inside the dials
        sx, sy = self.speedo.center
        spd = "- - -" if v.speed_kmh is None else f"{int(round(v.speed_kmh))}"
        _text(self.screen, self.f_big, spd, FG, center=(sx, sy + 78))
        _text(self.screen, self.f_tiny, "km/h", DIM, center=(sx, sy + 118))
        tx, ty = self.tacho.center
        _text(self.screen, self.f_gear, v.gear_text, ACCENT, center=(tx, ty + 80))

        self._center_panel(v, dt, mode_text)
        self._badges(v.telltales)
        if hint:
            _text(self.screen, self.f_tiny, hint, (70, 76, 86), center=(self.w // 2, self.h - 10))
