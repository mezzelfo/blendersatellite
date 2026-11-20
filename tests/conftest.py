"""Make the modules in pipeline/ importable by the tests (pytest tests/ from the repository root)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
