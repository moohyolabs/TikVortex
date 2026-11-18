"""Double-click to start TikVortex without a console window (Windows)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tikvortex.app import main  # noqa: E402

main()
