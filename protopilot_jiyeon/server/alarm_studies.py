"""Run independent alarm comparisons in parallel and share work by checkpoint."""

from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from os import cpu_count
from signal import SIG_IGN, SIGINT, signal
from threading import Lock

from .alarm_response import evaluate_alarm
from .assistant import append_trace


def ignore_worker_interrupts():
    signal(SIGINT, SIG_IGN)


class AlarmStudies:
    def __init__(self, trace_path, *, workers=None, analyze=None):
        self.trace_path = trace_path
        workers = workers if workers is not None else max(2, min(4, (cpu_count() or 2) - 1))
        self.processes = None if analyze else ProcessPoolExecutor(
            max_workers=workers, mp_context=get_context("spawn"), initializer=ignore_worker_interrupts,
        )
        self.analyze = analyze
        # Keep queued jobs out of the process pool until a worker slot is available.
        self.executor = ThreadPoolExecutor(max_workers=workers)
        self.lock = Lock()
        self.jobs = OrderedDict()

    def submit(self, checkpoint, *, retry=False):
        # The current recording can rotate while an alarm checkpoint stays available.
        job_id = f"{checkpoint.run_id}-{checkpoint.alarm['id']}"
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or (job.done() and (retry or job.exception() is not None)):
                job = self.executor.submit(self.analyze or self._analyze, checkpoint)
                self.jobs[job_id] = job
            self.jobs.move_to_end(job_id)
            expired = len(self.jobs) - 80
            if expired > 0:
                completed = [key for key, other in self.jobs.items() if key != job_id and other.done()]
                for key in completed[:expired]:
                    del self.jobs[key]
            return job_id, job

    def get(self, job_id):
        with self.lock:
            return self.jobs.get(job_id)

    def _analyze(self, checkpoint):
        result, trace = self.processes.submit(evaluate_alarm, checkpoint).result()
        result["logged"] = append_trace(self.trace_path, trace)
        return result

    def close(self):
        self.executor.shutdown(cancel_futures=True)
        if self.processes is not None:
            self.processes.shutdown()
