"""DB schema contract tests (no live DB required for import checks)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api_gateway"))

from db_schema import HEAD_REVISION, REQUIRED_TABLES, REQUIRED_TICKS_COLUMNS  # noqa: E402


def test_head_revision_stable():
    assert HEAD_REVISION == "0001_baseline"


def test_required_tables():
    assert "ticks" in REQUIRED_TABLES
    assert "positions" in REQUIRED_TABLES
    assert len(REQUIRED_TABLES) == 7


def test_ticks_columns_include_book():
    assert "bid_price" in REQUIRED_TICKS_COLUMNS
    assert "timestamp_ms" in REQUIRED_TICKS_COLUMNS
