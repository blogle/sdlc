#!/usr/bin/env python3
"""Install bundled SDLC skills into an agent skills directory."""
import shutil
import sys
from pathlib import Path


def install(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: install_skills.py SOURCE DESTINATION")
    install(Path(sys.argv[1]), Path(sys.argv[2]))
