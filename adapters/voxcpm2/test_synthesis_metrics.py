import json
import unittest

from inference_worker import InferenceWorker
from synthesis_metrics import SynthesisMetrics


class MetricsTest(unittest.TestCase):
    def test_queue_and_worker_time_exclude_consumer_work(self):
        metrics = SynthesisMetrics()
        metrics.enqueue('one')
        metrics.enqueue('two')
        self.assertEqual(metrics.snapshot()['waiting'], 2)
        metrics.begin('one')
        metrics.rendering('one', True)
        metrics.work('one', .12)
        metrics.work('one', .08)
        snapshot = metrics.snapshot()
        self.assertEqual((snapshot['synthesizing'], snapshot['waiting']), (1, 1))
        metrics.rendering('one', False)
        self.assertEqual((metrics.snapshot()['synthesizing'], metrics.snapshot()['waiting']), (0, 1))
        metrics.finish('one', 'completed')
        self.assertAlmostEqual(metrics.snapshot()['last_synthesis_seconds'], .2)
        metrics.finish('two', 'cancelled')
        self.assertIsNone(metrics.snapshot()['last_synthesis_seconds'])
        self.assertEqual(metrics.snapshot()['waiting'], 0)
        self.assertIsNone(metrics.snapshot()['pending_items'])

    def test_worker_observation_is_not_passed_as_model_input(self):
        observations = []
        lifecycle = []
        def render(text, seed, **options):
            self.assertEqual(options, {'style': 'plain'})
            yield b'first'
            yield b'second'
        worker = InferenceWorker(lambda: None, render)
        try:
            self.assertEqual(list(worker.generate('private-fixture', 42, observe=observations.append,
                on_start=lambda: lifecycle.append('start'), on_finish=lambda: lifecycle.append('finish'), style='plain')), [b'first', b'second'])
            self.assertEqual(len(observations), 3)
            self.assertEqual(lifecycle, ['start', 'finish'])
            self.assertTrue(all(value >= 0 for value in observations))
        finally:
            worker.close()
        self.assertNotIn('private-fixture', json.dumps(SynthesisMetrics().snapshot()))


if __name__ == '__main__':
    unittest.main()
