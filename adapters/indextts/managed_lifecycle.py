"""A managed adapter exits when its parent's stdin pipe closes."""
import os
import sys
import threading
import time


def watch_parent():
    if not os.environ.get('CORTICO_TTS_MANAGED_TOKEN'):
        return

    descriptor = sys.stdin.fileno()
    # Windows 的阻塞 CRT stdin 读取会占住描述符锁，使数值库的 DLL 初始化挂起。
    os.set_blocking(descriptor, False)

    def wait_for_eof():
        while True:
            try:
                if not os.read(descriptor, 1):
                    os._exit(0)
            except BlockingIOError:
                pass
            time.sleep(0.2)

    threading.Thread(target=wait_for_eof, name='tts-parent', daemon=True).start()
