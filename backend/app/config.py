from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "分拣过机台"
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("SORTBAY_DB", str(ROOT / "data" / "sortbay.db")))
