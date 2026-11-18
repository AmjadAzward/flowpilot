"""FlowPilot worker: runs queued jobs. Start the API with FLOWPILOT_QUEUE=1, then start one or more workers:

    python -m app.worker --concurrency 4

Workers share the database with the API, so run them on the same computer (or the same network drive) with the same
FLOWPILOT_DB and FLOWPILOT_KEY_FILE settings. Run as many workers as you like. Use --once to process the queue and exit.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import main

log = logging.getLogger("flowpilot.worker")


def work(concurrency: int = 2, once: bool = False, poll: float = 0.5, stop: threading.Event | None = None) -> int:
    """Process jobs until stopped (or until the queue is empty with once=True). Returns how many jobs ran."""
    stop = stop or threading.Event()
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    ran, last_maintenance = 0, 0.0
    lock = threading.Lock()
    slots = threading.Semaphore(concurrency)

    def run(job: dict) -> None:
        nonlocal ran
        try: main.run_job(job)
        finally:
            with lock: ran += 1
            slots.release()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        while not stop.is_set():
            if time.time() - last_maintenance > 5:
                last_maintenance = time.time()
                try:
                    main.recover_jobs()
                    main.resume_due_waits()
                except Exception: log.exception("Maintenance failed")
            if not slots.acquire(timeout=poll): continue
            job = main.claim_job(worker_id)
            if job is None:
                slots.release()
                if once: break
                stop.wait(poll)
                continue
            pool.submit(run, job)
        # leaving the with-block waits for running jobs to finish
    return ran


def cli() -> None:
    parser = argparse.ArgumentParser(description="FlowPilot queue worker")
    parser.add_argument("--concurrency", type=int, default=2, help="jobs to run at the same time in this process (default 2)")
    parser.add_argument("--once", action="store_true", help="process the queue and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    print(f"FlowPilot worker started (concurrency {args.concurrency}). Press Ctrl+C to stop.", flush=True)
    n = work(max(1, min(args.concurrency, 16)), once=args.once, stop=stop)
    print(f"Worker stopped after {n} jobs.", flush=True)


if __name__ == "__main__":
    cli()
