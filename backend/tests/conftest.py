"""
These tests are offline by design: no database, no network, no API keys.
Everything they touch is either pure or stubbed, so they stay fast enough to
run on every change.

    pip install -r requirements-dev.txt && pytest
"""
import sys
from pathlib import Path

# Let `import app...` work whether pytest runs from backend/ or from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
