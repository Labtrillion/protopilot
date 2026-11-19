"""Serve Zoro-1 typed decisions over HTTP with plain transformers, for GPUs without the SGLang stack.

    python tools/zoro_decide_server.py --port 30001 [--dtype float16] [--device cuda:0]
    ZORO_BASE_URL=http://127.0.0.1:30001 npm start

Answers the two request shapes server/assistant.py sends:
  POST /v1/systemone  {"model", "state", "questions", "reasoning"}
  POST /v1/decide     {"request": {"state", "questions"}, "reasoning"}
with {"answers": {...}, "timing_ms"} from the checkpoint's own `zoro_decide.Decider` (shipped in the model repo).
Requires torch, transformers >= 5.2 and huggingface_hub in the Python that runs it. On Turing GPUs (no bf16)
use float16, which matched float32 decisions to within 0.002 in probability.
"""

import argparse
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

MAX_BODY = 4 * 1024 * 1024


def load_decider(model, device, dtype):
    import torch
    from huggingface_hub import snapshot_download

    path = snapshot_download(model)
    sys.path.insert(0, path)
    from zoro_decide import Decider  # noqa: E402 - shipped inside the model snapshot

    return Decider(path, device=device, dtype=getattr(torch, dtype))


def make_handler(decider):
    lock = Lock()  # one forward pass at a time: the model holds one GPU's memory

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body):
            payload = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            if self.path not in ("/v1/systemone", "/v1/decide"):
                self.reply(404, {"error": "unknown path"})
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= MAX_BODY:
                    raise ValueError("request size")
                body = json.loads(self.rfile.read(size))
                request = body["request"] if self.path == "/v1/decide" else {
                    "state": body["state"], "questions": body["questions"]}
                if not isinstance(request.get("questions"), dict) or not request["questions"]:
                    raise ValueError("questions required")
                with lock:
                    result = decider.decide(request, reasoning=bool(body.get("reasoning", False)))
                self.reply(200, result)
            except (KeyError, TypeError, ValueError) as error:
                self.reply(400, {"error": f"{type(error).__name__}: {error}"})
            except Exception:  # noqa: BLE001 - report model failures to the caller, keep serving
                logging.exception("decision failed")
                self.reply(500, {"error": "decision failed"})

        def log_message(self, format, *args):
            logging.info("%s %s", self.address_string(), format % args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=30001)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--model", default="trillionlabs/Zoro-1")
    parser.add_argument("--device", default=None)
    parser.add_argument("--dtype", default="float16", choices=("float16", "bfloat16", "float32"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    decider = load_decider(args.model, args.device, args.dtype)
    with ThreadingHTTPServer((args.host, args.port), make_handler(decider)) as server:
        print(f"Zoro-1 decisions  http://{args.host}:{args.port}/v1  ({args.dtype})", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
