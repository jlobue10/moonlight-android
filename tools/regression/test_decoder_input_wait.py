#!/usr/bin/env python3
"""Text gate: with direct submit the decoder's input-buffer wait runs on common-c's receive
thread. One call waits at most INPUT_BUFFER_WAIT_MS (a failed wait makes the caller request an
IDR), the hung exception is measured across calls from inputStallStartMs, and direct submit is
advertised only when the activity allowed it (the plain 2D render path). --baseline reads HEAD.
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


renderer = read('app/src/main/java/com/limelight/binding/video/MediaCodecDecoderRenderer.java')
game = read('app/src/main/java/com/limelight/Game.java')
fetch = block(renderer, '    private boolean fetchNextInputBuffer() {')
caps = block(renderer, '    public int getCapabilities() {')
failures = 0
for ok, name in (
    ('< INPUT_BUFFER_WAIT_MS' in fetch and '< 5000' not in fetch, 'one input-buffer wait is bounded by INPUT_BUFFER_WAIT_MS, not the hang threshold'),
    ('if (inputStallStartMs == 0) {' in fetch and 'inputStallStartMs = startTime;' in fetch and
     'if (!stopping && stalledMs >= DECODER_HUNG_MS && initialException == null) {' in fetch and 'inputStallStartMs = 0;' in fetch,
     'the hung exception is measured across calls from the first failed wait and reset on success'),
    ('outputBufferQueue.take()' not in block(renderer, '    private void startRendererThread()') and 'outputBufferQueue.poll()' in block(renderer, '    private void startRendererThread()'),
     'the render thread never blocks on its own output queue (poll, not take)'),
    ('directSubmitAllowed' in caps[caps.index('CAPABILITY_DIRECT_SUBMIT') - 200:caps.index('CAPABILITY_DIRECT_SUBMIT')],
     'direct submit is advertised only when the activity allowed it'),
    ('setDirectSubmitAllowed(prefConfig.renderMode == 0)' in game and game.index('setDirectSubmitAllowed(') < game.index('conn.start('),
     'Game allows direct submit for the plain 2D render path only, before the connection starts'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
