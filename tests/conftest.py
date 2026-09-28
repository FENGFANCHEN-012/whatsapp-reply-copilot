import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "examples"


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """Point every data file at a temp dir so tests never touch a real data/."""
    from copilot import config
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "PAIRS_FILE", tmp_path / "pairs.jsonl")
    monkeypatch.setattr(config, "FEEDBACK_FILE", tmp_path / "feedback.jsonl")
    monkeypatch.setattr(config, "INDEX_FILE", tmp_path / "index.npz")
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    return tmp_path
