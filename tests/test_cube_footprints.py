"""Tests for validation/cube_footprints.py (outline indexing only; the geometry is tested in
test_analyze_cube_run)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'validation'))
from cube_footprints import perimeter  # noqa: E402


def test_perimeter_closed_and_covers_corners():
    G, C = 1000, 300
    lines, cols = perimeter(G, C, step=64)
    assert len(lines) == len(cols)
    assert (lines[0], cols[0]) == (lines[-1], cols[-1]) == (0, 0)
    pts = set(zip(lines.tolist(), cols.tolist()))
    for corner in [(0, 0), (0, C - 1), (G - 1, C - 1), (G - 1, 0)]:
        assert corner in pts
    assert lines.min() == 0 and lines.max() == G - 1
    assert cols.min() == 0 and cols.max() == C - 1


def test_perimeter_stays_on_border():
    G, C = 130, 70
    lines, cols = perimeter(G, C, step=16)
    on_border = (lines == 0) | (lines == G - 1) | (cols == 0) | (cols == C - 1)
    assert on_border.all()
    # consecutive points move along one axis only
    steps = np.c_[np.diff(lines), np.diff(cols)]
    assert ((steps[:, 0] == 0) | (steps[:, 1] == 0)).all()
