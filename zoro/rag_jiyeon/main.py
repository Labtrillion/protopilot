"""Process an alarm JSON event and retrieve supporting knowledge."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from zoro_rag.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
