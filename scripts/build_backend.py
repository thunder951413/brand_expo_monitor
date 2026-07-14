from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist-backend"
WORK = ROOT / "build-backend"


def main() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    separator = os.pathsep
    options = [
        str(ROOT / "app.py"),
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name=brandscope-backend",
        f"--distpath={DIST}",
        f"--workpath={WORK}",
        f"--specpath={WORK}",
        f"--add-data={ROOT / 'templates'}{separator}templates",
        f"--add-data={ROOT / 'static'}{separator}static",
        "--collect-all=selenium",
    ]
    if sys.platform == "win32":
        options.append("--noconsole")
    PyInstaller.__main__.run(options)


if __name__ == "__main__":
    main()

