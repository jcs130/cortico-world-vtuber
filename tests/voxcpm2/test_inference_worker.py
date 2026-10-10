"""Thread ownership and cancellation gates required by compiled CUDA inference."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'adapters/voxcpm2'))
from inference_worker import InferenceWorker


class WorkerTest(unittest.TestCase):
    def test_initialize_generate_and_close_generator_share_one_owner_thread(self):
        thread_ids = []
        active = 0
        maximum_active = 0

        def initialize():
            thread_ids.append(threading.get_ident())

        def render(text, seed):
            nonlocal active, maximum_active
            thread_ids.append(threading.get_ident())
            active += 1
            maximum_active = max(maximum_active, active)
            try:
                yield text.encode()
                time.sleep(0.02)
                yield bytes([seed])
            finally:
                thread_ids.append(threading.get_ident())
                active -= 1

        worker = InferenceWorker(initialize, render)
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(lambda n=n: list(worker.generate(str(n), n))) for n in range(3)]
                self.assertEqual([f.result(2) for f in futures], [[b'0', b'\x00'], [b'1', b'\x01'], [b'2', b'\x02']])
            self.assertEqual(len(set(thread_ids)), 1)
            self.assertNotEqual(thread_ids[0], threading.get_ident())
            self.assertEqual(maximum_active, 1)
        finally:
            worker.close()
        self.assertFalse(worker.thread.is_alive())

    def test_cancel_releases_a_backpressured_generator_and_next_job_runs(self):
        closed = threading.Event()

        def render(text, seed):
            try:
                for _ in range(10000 if text == 'cancel' else 2):
                    yield b'audio'
            finally:
                closed.set()

        worker = InferenceWorker(lambda: None, render)
        try:
            current = worker.generate('cancel', 42)
            self.assertEqual(next(current), b'audio')
            time.sleep(0.05)
            current.close()
            self.assertTrue(closed.wait(1))
            self.assertEqual(list(worker.generate('next', 42)), [b'audio', b'audio'])
        finally:
            worker.close()

    def test_decoder_error_reaches_client_and_worker_remains_usable(self):
        def render(text, seed):
            yield b'first'
            if text == 'error':
                raise ValueError('decoder failed')
            yield b'last'

        worker = InferenceWorker(lambda: None, render)
        try:
            with self.assertRaisesRegex(ValueError, 'decoder failed'):
                list(worker.generate('error', 42))
            self.assertEqual(list(worker.generate('next', 42)), [b'first', b'last'])
        finally:
            worker.close()

    def test_initialization_failure_is_reported_before_serving(self):
        def initialize():
            raise RuntimeError('compile warmup failed')

        with self.assertRaisesRegex(RuntimeError, 'compile warmup failed'):
            InferenceWorker(initialize, lambda *_: iter(()))


if __name__ == '__main__':
    unittest.main()
