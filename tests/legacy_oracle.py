"""Load the frozen Python implementation strictly as a test oracle."""

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "tests" / "fixtures" / "legacy"
os.environ.setdefault("SDLC_SOURCE_PATH", str(ROOT))
sys.path.insert(0, str(LEGACY))
