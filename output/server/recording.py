"""Local run historian with a flat CSV and a streaming JSON source."""

import csv
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from .catalog import CHANNELS, METADATA

RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7}")


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class Recording:
    def __init__(self, root):
        self.id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:7]
        self.directory = root / self.id
        self.directory.mkdir(parents=True)
        self.csv_path = self.directory / "telemetry.csv"
        self.json_path = self.directory / "telemetry.jsonl"
        self.events_path = self.directory / "events.jsonl"
        self.fields = ["time_utc", "time_hours", "sample", *CHANNELS]
        with self.csv_path.open("w", newline="", encoding="utf-8") as file:
            csv.writer(file).writerow(self.fields)
        self.json_path.touch()
        self.events_path.touch()
        (self.directory / "schema.json").write_text(encode(METADATA) + "\n", encoding="utf-8")

    def sample(self, row):
        line = encode(row)
        with self.json_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        with self.csv_path.open("a", newline="", encoding="utf-8") as file:
            csv.writer(file).writerow([row["time_utc"], row["time_hours"], row["sample"], *row["values"].values()])
        latest = self.directory / "latest.json"
        temp = latest.with_suffix(".tmp")
        temp.write_text(line + "\n", encoding="utf-8")
        temp.replace(latest)

    def event(self, event):
        with self.events_path.open("a", encoding="utf-8") as file:
            file.write(encode(event) + "\n")

    def save_scenario(self, document):
        target = self.directory / "scenario.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(encode(document) + "\n", encoding="utf-8")
        temp.replace(target)


class ArchivedRecording:
    def __init__(self, directory):
        self.id = directory.name
        self.directory = directory
        self.csv_path = directory / "telemetry.csv"
        self.json_path = directory / "telemetry.jsonl"
        self.events_path = directory / "events.jsonl"

    @classmethod
    def open(cls, root, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise FileNotFoundError(run_id)
        recording = cls(root / run_id)
        if not recording.json_path.is_file():
            raise FileNotFoundError(run_id)
        return recording

    @classmethod
    def list(cls, root):
        entries = []
        for folder in sorted(root.iterdir(), reverse=True):
            if not RUN_ID.fullmatch(folder.name):
                continue
            latest = folder / "latest.json"
            try:
                sample = json.loads(latest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            entries.append({"run": folder.name, "sample": sample["sample"], "time_hours": sample["time_hours"],
                            "scenario": (folder / "scenario.json").is_file()})
        return entries

    def scenario(self):
        scenario = json.loads((self.directory / "scenario.json").read_text(encoding="utf-8"))
        latest = json.loads((self.directory / "latest.json").read_text(encoding="utf-8"))
        scenario["through_step"] = max(scenario["through_step"], latest.get("step", 0))
        return scenario

    def series(self, tag):
        latest = json.loads((self.directory / "latest.json").read_text(encoding="utf-8"))
        stride = max(1, (latest["sample"] + 719) // 720)
        points = []
        with self.json_path.open(encoding="utf-8") as stream:
            for index, line in enumerate(stream):
                if index >= latest["sample"]:
                    break
                if index % stride == 0 or index == latest["sample"] - 1:
                    row = json.loads(line)
                    points.append([row["time_hours"], row.get("values", {}).get(tag)])
        return points
