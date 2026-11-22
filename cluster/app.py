"""Main loop: vehicle simulation -> CAN bus -> cluster receiver -> HMI.

    python -m cluster.app                      # drive with the keyboard (W/S, Q/E, P/R/N/D)
    python -m cluster.app --demo               # automatic demo drive
    python -m cluster.app --interface socketcan --channel vcan0       # use Linux vcan
    python -m cluster.app --interface socketcan --channel vcan0 --no-sim  # display only
    python -m cluster.app --headless 8 --screenshot shot.png          # CI / screenshots
"""
from __future__ import annotations

import argparse
import os
import sys

import can
import cantools

from . import config
from .can_io import CanRx, CanTx
from .logic import build_view
from .signals import SignalStore
from .vehicle_sim import DemoDriver, ManualDriver, VehicleSim

HINT_AUTO = "M manual driving  |  F ABS bus fault  |  X engine fault  |  ESC quit"
HINT_MANUAL = ("W gas  S brake  P/R/N/D gear  Q/E indicator  H high beam  B park brake  "
               "C belt  O door  |  M demo  F/X faults")
SHIFT_LOCK_KMH = 5.0  # P and R are only accepted at (nearly) standstill


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Digital instrument cluster (CAN-driven)")
    p.add_argument("--interface", default="virtual", help="python-can interface (virtual, socketcan, ...)")
    p.add_argument("--channel", default="cluster", help="CAN channel, e.g. vcan0")
    p.add_argument("--no-sim", action="store_true", help="do not run the vehicle simulation (display only)")
    p.add_argument("--demo", action="store_true", help="start with the automatic demo drive")
    p.add_argument("--time-lapse", type=float, default=25.0,
                   help="speed-up for fuel use and engine warm-up in the demo")
    p.add_argument("--headless", type=float, metavar="SECONDS",
                   help="run without a window for SECONDS of simulated time, then exit")
    p.add_argument("--screenshot", help="save the last frame to this PNG file")
    p.add_argument("--fault-abs-at", type=float, metavar="T",
                   help="(headless helper) stop sending the ABS frame from time T on")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.headless is not None:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # imported late so SDL_VIDEODRIVER is honoured

    db = cantools.database.load_file(str(config.DBC_PATH))
    rx_bus = can.Bus(interface=args.interface, channel=args.channel)
    tx_bus = None if args.no_sim else can.Bus(interface=args.interface, channel=args.channel)
    store = SignalStore.from_dbc(db, config.MESSAGES)
    rx = CanRx(rx_bus, db, store)
    tx = CanTx(tx_bus, db, config.MESSAGES) if tx_bus else None

    sim = VehicleSim(time_lapse=args.time_lapse)
    sim.refuel_below_pct = 4.0
    demo, manual = DemoDriver(), ManualDriver()
    # keyboard driving is the default; the automatic demo is used for --demo and --headless
    manual_mode = not (args.demo or args.headless is not None)

    pygame.display.init()  # no audio needed
    pygame.font.init()
    pygame.display.set_caption("Digital Instrument Cluster")
    screen = pygame.display.set_mode(config.SCREEN_SIZE)
    from .dashboard import Dashboard  # needs an initialised display
    dash = Dashboard(screen)
    clock = pygame.time.Clock()

    now = 0.0  # simulated time base used by sim, TX scheduler and timeouts
    running = True
    try:
        while running:
            if args.headless is not None:
                dt = 1.0 / config.FPS
                if now >= args.headless:
                    running = False
            else:
                dt = min(clock.tick(config.FPS) / 1000.0, 0.1)
            now += dt

            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.KEYDOWN:
                    k = ev.key
                    mi = manual.inp
                    if k == pygame.K_ESCAPE:
                        running = False
                    elif k == pygame.K_m:
                        manual_mode = not manual_mode
                        if manual_mode:  # take over the current state smoothly
                            manual.inp.selector = sim.inp.selector
                            manual.inp.parking_brake = sim.inp.parking_brake
                    elif k == pygame.K_f and tx:
                        tx.muted ^= {"ABS_VehicleSpeed"}
                    elif k == pygame.K_x:
                        sim.faults["check_engine"] = not sim.faults["check_engine"]
                    elif manual_mode:
                        gear_keys = {pygame.K_p: "P", pygame.K_r: "R",
                                     pygame.K_n: "N", pygame.K_d: "D"}
                        if k in gear_keys:
                            want = gear_keys[k]
                            # like a real shift lock: no P or R while rolling
                            if want in ("N", "D") or sim.speed_kmh < SHIFT_LOCK_KMH:
                                mi.selector = want
                        elif k == pygame.K_q:
                            mi.turn_left, mi.turn_right = not mi.turn_left, False
                        elif k == pygame.K_e:
                            mi.turn_right, mi.turn_left = not mi.turn_right, False
                        elif k == pygame.K_b:
                            mi.parking_brake = not mi.parking_brake
                        elif k == pygame.K_h:
                            mi.high_beam = not mi.high_beam
                        elif k == pygame.K_c:
                            mi.seatbelt_on = not mi.seatbelt_on
                        elif k == pygame.K_o:
                            mi.door_open = not mi.door_open

            if tx:
                if args.fault_abs_at is not None and now >= args.fault_abs_at:
                    tx.muted.add("ABS_VehicleSpeed")
                if manual_mode:
                    keys = pygame.key.get_pressed()
                    inp = manual.step(dt, keys[pygame.K_w], keys[pygame.K_s])
                else:
                    inp = demo.step(dt, sim.speed_kmh)
                sim.step(dt, inp)
                tx.service(now, sim.can_signals())

            rx.poll(now)
            view = build_view(store, now)

            if not tx:
                mode = "DISPLAY ONLY"
            else:
                mode = "MANUAL" if manual_mode else "AUTO DEMO"
                if "ABS_VehicleSpeed" in tx.muted:
                    mode += "  |  ABS FRAME MUTED"
            dash.draw(view, now, dt, mode, HINT_MANUAL if manual_mode else HINT_AUTO)
            pygame.display.flip()

        if args.screenshot:
            pygame.image.save(screen, args.screenshot)
    finally:
        pygame.quit()
        rx_bus.shutdown()
        if tx_bus:
            tx_bus.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
