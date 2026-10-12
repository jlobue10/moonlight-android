#!/usr/bin/env python3
"""The USB driver service balances still-open permission prompts through Game's state listener
while it stops, which can run after Game.onDestroy: a picture-in-picture update on a destroyed
activity throws on the main thread. Text gate: updatePipAutoEnter() returns on a finishing or
destroyed activity, and onDestroy detaches both binder listeners before unbindService().
--baseline reads HEAD.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/Game.java'
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


pip = method('    public void updatePipAutoEnter() {')
destroy = method('    protected void onDestroy() {') if '    protected void onDestroy() {' in source else method('    public void onDestroy() {')
failures = 0
for ok, name in (
    ('isDestroyed()' in pip and 'isFinishing()' in pip and pip.index('isDestroyed()') < pip.index('setPictureInPictureParams'),
     'a PiP update on a finishing/destroyed activity returns before touching the window'),
    ('usbDriverBinder.setStateListener(null)' in destroy and 'usbDriverBinder.setListener(null)' in destroy and
     destroy.index('usbDriverBinder.setStateListener(null)') < destroy.index('unbindService(usbDriverServiceConnection)'),
     'onDestroy detaches both USB service listeners before unbinding'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
