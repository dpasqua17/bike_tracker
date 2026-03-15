"""Launch the app with screenshot-ready demo data."""

from __future__ import annotations

import asyncio
import logging
import sys

import pyqtgraph as pg
import qasync
from PyQt6.QtWidgets import QApplication

from db.database import init_db
from demo.scenario import generate_demo_samples, seed_demo_database
from ui.dashboard import MainWindow
import ui.styles as styles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def main():
    pg.setConfigOptions(
        antialias=True,
        background=styles.PG_BACKGROUND,
        foreground=styles.PG_FOREGROUND,
    )

    db = init_db(":memory:")
    seed_demo_database(db)

    window = MainWindow(db)
    window.show()
    window.load_demo_session(generate_demo_samples(), bike_name="JOROTO-X4S DEMO")

    while window.isVisible():
        await asyncio.sleep(0.05)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Bike Tracker Demo")

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        loop.run_until_complete(main())
