"""Serve the compiled console, live process data, controls, and local exports."""

import argparse
import json
import logging
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, RLock, Thread
from urllib.parse import parse_qs, urlsplit
from wsgiref.simple_server import ServerHandler, WSGIRequestHandler, WSGIServer

from .catalog import CHANNELS, METADATA
from .experiments import EXPERIMENTS_PATH, create_experiments
from .plant import Plant, TICK_SECONDS
from .recording import ArchivedRecording, encode

ROOT = Path(__file__).resolve().parents[1]


class ConsoleServer(ThreadingHTTPServer):
    def server_bind(self):
        super().server_bind()
        WSGIServer.setup_environ(self)


class ConsoleHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, plant, lock, experiments, **kwargs):
        self.plant = plant
        self.lock = lock
        self.experiments = experiments
        super().__init__(*args, directory=str(ROOT / "dist"), **kwargs)

    def log_message(self, format, *args):
        if args[1] not in ("200", "304"):
            super().log_message(format, *args)

    def serve_experiments(self):
        environ = WSGIRequestHandler.get_environ(self)
        handler = ServerHandler(self.rfile, self.wfile, sys.stderr, environ, multithread=True)
        handler.request_handler = self
        handler.run(self.experiments)

    def respond(self, body, status=200, *, attachment=None):
        payload = encode(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        if path.startswith(EXPERIMENTS_PATH):
            self.serve_experiments()
        elif path == "/api/schema":
            self.respond(METADATA)
        elif path == "/api/live":
            with self.lock:
                self.respond(self.plant.status())
        elif path == "/api/scenario":
            with self.lock:
                self.respond(self.plant.scenario(), attachment=f"scenario-{self.plant.recording.id}.json")
        elif path == "/api/recordings":
            self.respond(ArchivedRecording.list(self.plant.root))
        elif path.startswith("/api/recordings/"):
            self.archive(path, parse_qs(url.query))
        elif path in ("/api/export/csv", "/api/export/json"):
            self.download(path.rsplit("/", 1)[1])
        else:
            super().do_GET()

    def do_HEAD(self):
        if urlsplit(self.path).path.startswith(EXPERIMENTS_PATH):
            self.serve_experiments()
        else:
            super().do_HEAD()

    def do_POST(self):
        if urlsplit(self.path).path.startswith(EXPERIMENTS_PATH):
            self.serve_experiments()
            return
        if self.path != "/api/command":
            self.send_error(404)
            return
        if self.headers.get_content_type() != "application/json":
            self.respond({"error": "JSON 요청이 필요합니다."}, 415)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 1024 * 1024:
                raise ValueError("요청 크기를 확인하세요.")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("요청 형식을 확인하세요.")
            with self.lock:
                self.plant.command(body)
                self.respond(self.plant.status())
        except (ValueError, TypeError) as error:
            self.respond({"error": str(error)}, 400)
        except OSError:
            logging.exception("Could not record operator command")
            self.respond({"error": "로컬 기록 쓰기에 실패했습니다."}, 500)

    def archive(self, path, query):
        try:
            run_id, part = path.removeprefix("/api/recordings/").split("/")
            recording = ArchivedRecording.open(self.plant.root, run_id)
            if part == "scenario":
                self.respond(recording.scenario(), attachment=f"scenario-{run_id}.json")
                return
            if part == "series":
                tag = query.get("tag", ["XMEAS(7)"])[0]
                if tag not in CHANNELS:
                    self.respond({"error": "계측 태그를 확인하세요."}, 400)
                    return
                self.respond({"tag": tag, **recording.series(tag)})
                return
            if part not in {"csv", "json"}:
                raise FileNotFoundError(path)
            schema = (recording.directory / "schema.json").read_bytes() if part == "json" else None
        except (OSError, ValueError):
            self.respond({"error": "기록을 찾을 수 없습니다."}, 404)
            return
        self.download(part, recording=recording, schema=schema)

    def download(self, kind, *, recording=None, schema=None):
        with self.lock:
            recording = recording or self.plant.recording
            source = recording.csv_path if kind == "csv" else recording.json_path
            size = source.stat().st_size
            events_size = recording.events_path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ("text/csv" if kind == "csv" else "application/json") + "; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="tep-{recording.id}.{kind}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if kind == "csv":
            with source.open("rb") as file:
                remaining = size
                while remaining > 0 and (chunk := file.read(min(remaining, 65536))):
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        else:
            self.wfile.write(f'{{"run":"{recording.id}","schema":'.encode())
            self.wfile.write(schema.strip() if schema is not None else encode(METADATA).encode())
            self.wfile.write(b',"samples":[')
            self.write_lines(source, size)
            self.wfile.write(b'],"events":[')
            self.write_lines(recording.events_path, events_size)
            self.wfile.write(b"]}")

    def write_lines(self, path, size):
        with path.open("rb") as file:
            separator = b""
            while file.tell() < size:
                self.wfile.write(separator + file.readline().rstrip(b"\n"))
                separator = b","


def advance_forever(plant, lock, stop):
    while not stop.wait(TICK_SECONDS):
        with lock:
            if plant.running:
                try:
                    plant.tick()
                except Exception:
                    logging.exception("Simulation stopped")
                    plant.running = False
                    plant.error = "시뮬레이션 또는 로컬 기록 오류"
                    plant.version += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    if not (ROOT / "dist/index.html").is_file():
        parser.error("먼저 npm run build를 실행하세요.")
    lock, stop = RLock(), Event()
    plant = Plant(args.data)
    experiments = create_experiments(args.data)
    handler = partial(ConsoleHandler, plant=plant, lock=lock, experiments=experiments.server)
    with ConsoleServer(("127.0.0.1", args.port), handler) as server:
        thread = Thread(target=advance_forever, args=(plant, lock, stop), daemon=True)
        thread.start()
        print(f"ProtoPilot  http://127.0.0.1:{args.port}  |  {args.data.resolve()}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            thread.join()


if __name__ == "__main__":
    main()
