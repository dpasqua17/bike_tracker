"""
Entry point.

qasync bridges asyncio (needed for bleak BLE) into PyQt6's event loop.
Run with:  python main.py
"""

import sys
import asyncio
import logging

from PyQt6.QtWidgets import QApplication
import qasync
import pyqtgraph as pg

import config
from db.database import init_db
from ui.dashboard import MainWindow
import ui.styles as styles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def main():
    config.load_user_profile()

    pg.setConfigOptions(
        antialias=True,
        background=styles.PG_BACKGROUND,
        foreground=styles.PG_FOREGROUND,
    )

    db = init_db()
    window = MainWindow(db)
    window.show()

    # Keep the event loop alive until window closes
    while window.isVisible():
        await asyncio.sleep(0.05)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Bike Tracker")

    # qasync integrates asyncio with Qt event loop
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        loop.run_until_complete(main())
