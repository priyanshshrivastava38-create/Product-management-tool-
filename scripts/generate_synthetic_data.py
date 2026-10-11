"""Regenerate the bundled synthetic datasets in data/sample/.

Usage: python scripts/generate_synthetic_data.py
Output is deterministic (fixed seeds), so committed files are reproducible.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from productpulse.sample_data import write_all  # noqa: E402

if __name__ == "__main__":
    counts = write_all(ROOT / "data" / "sample")
    for name, n in counts.items():
        print(f"{name}: {n} rows")
