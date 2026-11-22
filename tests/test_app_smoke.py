"""End-to-end smoke test: run the full app headless and save a frame."""
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from cluster.app import main  # noqa: E402


def test_headless_run_saves_screenshot(tmp_path):
    out = tmp_path / "frame.png"
    assert main(["--headless", "3", "--screenshot", str(out),
                 "--channel", "smoke"]) == 0
    assert out.exists() and out.stat().st_size > 10_000
