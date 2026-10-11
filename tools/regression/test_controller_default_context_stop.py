#!/usr/bin/env python3
"""ControllerHandler.stop() must retire the default input context too (device id 0 / the Android 11
virtual device): it is in neither context map, and a mouse-emulation runnable left on it keeps
re-posting every 50 ms after the stream ends and can drive sendMouseMove into a later stream.
Text-level gate on the production method; --baseline reads HEAD.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/binding/input/ControllerHandler.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True, encoding='utf-8')
          if '--baseline' in sys.argv else (ROOT / PATH).read_text(encoding='utf-8'))


def method(signature):
    start = source.index(signature)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


stop = method('    public void stop() {')
failures = 0
for ok, name in (
    ('defaultContext.destroy();' in stop, 'stop() destroys the default context'),
    ('defaultContext.destroy();' in stop and stop.index('defaultContext.destroy();') > stop.index('usbDeviceContexts.values()'),
     'the default context is retired after the mapped contexts'),
    ('mainThreadHandler.removeCallbacks(mouseEmulationRunnable)' in method('        public void destroy() {\n            mouseEmulationActive = false;'),
     'a context destroy stops mouse emulation and removes its runnable'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
