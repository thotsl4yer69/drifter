"""
conftest.py — add src/ to sys.path so tests can import drifter modules
without any in-test path manipulation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
