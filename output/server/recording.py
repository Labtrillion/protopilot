"""Local run historian with a flat CSV and a streaming JSON source."""

import csv
import json
from datetime import datetime, timezone
from uuid import uuid4

from .catalog import CHANNELS, METADATA


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
