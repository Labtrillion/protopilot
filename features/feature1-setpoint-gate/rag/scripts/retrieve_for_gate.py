"""Read a captured Feature 1 alarm from stdin and return the RAG result as JSON."""

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zoro_rag import ZoroRAG


def main():
    os.environ.setdefault("ZORO_DEVICE", "cpu")
    os.environ.setdefault("ZORO_MODEL_CACHE", str(ROOT / ".cache/hub"))
    os.environ.setdefault("ZORO_SPARSE_CACHE", str(ROOT / ".cache/fastembed"))
    with ZoroRAG(ROOT / "config/tep_studio.yaml") as rag:
        # The live simulator uses Ricker control. The indexed case plans target Braatz.
        rag.simulator = "protopilot_ricker"
        rag.check_index()
        print(rag.process_alarm_event(json.load(sys.stdin)).model_dump_json())


if __name__ == "__main__":
    main()
