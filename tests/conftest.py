import sys
from pathlib import Path

# Make `harness` importable regardless of pytest invocation directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
