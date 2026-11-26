"""Paths, environment and policy loading."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SAMPLES_DIR = ROOT / "samples"
STATIC_DIR = Path(__file__).resolve().parent / "static"
RUNTIME_DIR = Path(os.environ.get("AP_RUNTIME_DIR", ROOT / "runtime"))
UPLOAD_DIR = RUNTIME_DIR / "uploads"
DB_PATH = RUNTIME_DIR / "ap.db"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for raw in env.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# "auto" = Claude when a key is configured, otherwise the local rules parser
EXTRACTOR = os.environ.get("AP_EXTRACTOR", "auto").lower()
CLAUDE_MODEL = os.environ.get("AP_CLAUDE_MODEL", "claude-opus-5-5")
# Small visual pacing so each stage is visible in the live view (0 to disable)
STAGE_MIN_MS = int(os.environ.get("AP_STAGE_MIN_MS", "450"))


def claude_enabled() -> bool:
    if EXTRACTOR == "rules":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def load_policy() -> dict:
    return json.loads((DATA_DIR / "policy.json").read_text())
