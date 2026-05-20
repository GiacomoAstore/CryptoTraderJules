#!/usr/bin/env python3
"""Wrapper — run DB schema verification (same as api_gateway boot check)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
script = ROOT / "api_gateway" / "verify_db_schema.py"
sys.exit(subprocess.call([sys.executable, str(script)], cwd=str(ROOT / "api_gateway")))
