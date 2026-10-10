"""Keep model initialization, CUDA graph warmup and generation on one owner thread."""
from __future__ import annotations

from dataclasses import dataclass, field
import queue
import threading


@dataclass
class Job:
    text: str
    seed: int
    output: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=8))
    cancelled: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None


class InferenceWorker:
    def __init__(self, initialize, render):
        self.initialize = initialize
        self.render = render
        self.jobs = queue.Queue()
        self.ready = threading.Event()
        self.closing = threading.Event()
        self.startup_error = None
        self.current = None
        self.thread = threading.Thread(target=self._run, name='voxcpm2-inference', daemon=True)
        self.thread.start()
        self.ready.wait()
        if self.startup_error is not None:
            raise self.startup_error

    def _run(self):
        try:
            self.initialize()
        except Exception as exc:
            self.startup_error = exc
        finally:
            self.ready.set()
        if self.startup_error is not None:
            return
        while True:
            job = self.jobs.get()
            if job is None:
                return
            self.current = job
            iterator = None
            try:
                if self.closing.is_set() or job.cancelled.is_set():
                    continue
                iterator = iter(self.render(job.text, job.seed))
                for chunk in iterator:
                    while not job.cancelled.is_set() and not self.closing.is_set():
                        try:
                            job.output.put(chunk, timeout=0.05)
                            break
                        except queue.Full:
                            pass
                    else:
                        break
            except Exception as exc:
                job.error = exc
            finally:
                if iterator is not None:
                    try:
                        iterator.close()
                    except Exception as exc:
                        if job.error is None:
                            job.error = exc
                job.done.set()
                self.current = None

    def generate(self, text, seed):
        if self.closing.is_set():
            raise RuntimeError('Inference worker is stopping')
        job = Job(text, seed)
        self.jobs.put(job)
        try:
            while True:
                try:
                    chunk = job.output.get(timeout=0.02)
                except queue.Empty:
                    if job.done.is_set():
                        if job.error is not None:
                            raise job.error
                        return
                    if not self.thread.is_alive():
                        raise RuntimeError('Inference worker exited')
                    continue
                yield chunk
        finally:
            job.cancelled.set()

    def close(self):
        self.closing.set()
        if self.current is not None:
            self.current.cancelled.set()
        self.jobs.put(None)
        self.thread.join(5)
