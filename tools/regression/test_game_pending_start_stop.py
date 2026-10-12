#!/usr/bin/env python3
"""Leaving the stream activity while the connection spinner is up must stop the pending start.
Game.connecting was never set, so stopConnection() (gated on connecting || connected) skipped
conn.stop(): the native start completed against a destroyed activity and NvConnection's
process-wide permit was never released, wedging every later stream. Text gate on the production
sources; --baseline reads HEAD.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
    return (ROOT / path).read_text(encoding='utf-8')


def block(source, marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


game = read('app/src/main/java/com/limelight/Game.java')
conn = read('app/src/main/java/com/limelight/nvstream/NvConnection.java')
lam_start = game.index('streamContainer.setOnSurfaceAvailable(')
lam = game[lam_start:game.index('gameMenuCallbacks = new GameMenu', lam_start)]
stop = block(conn, '    public void stop() {')
start = block(conn, '    public void start(final AudioRenderer audioRenderer')
started = block(game, '    public void connectionStarted() {')
failures = 0
for ok, name in (
    ('connecting = true;' in lam and lam.index('connecting = true;') < lam.index('conn.start('), 'Game marks itself connecting before it starts the connection'),
    ('connectionPermitHeld' in stop and stop.index('connectionPermitHeld') < stop.index('connectionAllowed.release()'), 'stop() releases the connection permit only when its start acquired it'),
    ('pendingStart.join();' in stop and 'join(10000)' not in stop, 'a non-owner stop waits for the launch request without a cap (the launch has no read timeout)'),
    ('stopRequested' in start and start.rindex('if (stopRequested)') > start.index('connectionAllowed.acquire()') and start.rindex('if (stopRequested)') < start.index('MoonBridge.setupBridge('),
     'the start thread re-checks a stop request after acquiring the permit and before the native start'),
    ('isDestroyed()' in started and started.index('isDestroyed()') < started.index('connected = true;'), 'connectionStarted() ignores a finishing/destroyed activity'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
