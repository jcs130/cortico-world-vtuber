"""Managed child ownership uses a real stdin pipe, without a model or network."""
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path


class ManagedLifecycleTest(unittest.TestCase):
    def test_parent_eof_terminates_managed_child(self):
        proc = subprocess.Popen(
            [sys.executable, '-u', '-c',
             'from managed_lifecycle import watch_parent; watch_parent(); '
             'print("ready", flush=True); import time; time.sleep(30)'],
            cwd=Path(__file__).parent,
            env={**os.environ, 'CORTICO_TTS_MANAGED_TOKEN': 'fixture-owner'},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )
        try:
            self.assertEqual(proc.stdout.readline().strip(), b'ready')
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=3), 0)
        finally:
            proc.stdin.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()

    def test_unmanaged_child_does_not_depend_on_stdin(self):
        env = dict(os.environ)
        env.pop('CORTICO_TTS_MANAGED_TOKEN', None)
        proc = subprocess.Popen(
            [sys.executable, '-u', '-c',
             'from managed_lifecycle import watch_parent; watch_parent(); '
             'print("ready", flush=True); import time; time.sleep(30)'],
            cwd=Path(__file__).parent, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )
        try:
            self.assertEqual(proc.stdout.readline().strip(), b'ready')
            proc.stdin.close()
            time.sleep(0.1)
            self.assertIsNone(proc.poll())
        finally:
            proc.stdin.close()
            proc.kill()
            proc.wait()
            proc.stdout.close()


if __name__ == '__main__':
    unittest.main()
