"""Metadata-only request counts and worker time; audio and text stay with their owners."""
import threading
import time


class SynthesisMetrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = {}
        self.last = None

    def enqueue(self, request_id):
        with self.lock:
            self.requests[request_id] = {'active': False, 'acquired': False, 'seconds': 0.0, 'steps': 0}

    def begin(self, request_id):
        with self.lock:
            self.requests[request_id]['acquired'] = True

    def rendering(self, request_id, active):
        with self.lock:
            request = self.requests.get(request_id)
            if request is not None:
                request['active'] = active

    def work(self, request_id, seconds):
        with self.lock:
            request = self.requests.get(request_id)
            if request is not None:
                request['seconds'] += max(0, seconds)
                request['steps'] += 1

    def finish(self, request_id, outcome):
        with self.lock:
            request = self.requests.pop(request_id)
            self.last = {'request_id': request_id, 'outcome': outcome,
                         'seconds': request['seconds'] if request['steps'] else None}

    def snapshot(self, ready=True):
        with self.lock:
            active = sum(int(request['active']) for request in self.requests.values())
            return {'time': time.time(), 'status': 'ok' if ready else 'error',
                    'synthesizing': active, 'waiting': sum(int(not request['acquired']) for request in self.requests.values()),
                    'last_synthesis_seconds': self.last['seconds'] if self.last else None,
                    'last_outcome': self.last['outcome'] if self.last else None,
                    'pending_items': None}
