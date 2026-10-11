#!/usr/bin/env python3
"""Host-supplied codec configuration must fail through the decoder's diagnosed RendererException
path, never abort the process: every VPS/SPS/PPS append goes through a capacity check, the H.264
SPS is parsed over the unit's length only (the array is reused), and the SPS parser/writer calls
are guarded. Text-level gate on MediaCodecDecoderRenderer; --baseline reads HEAD.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/binding/video/MediaCodecDecoderRenderer.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())


def method(signature):
    start = source.index(signature)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


submit = method('    public int submitDecodeUnit(')
put_config = method('    private void putCodecConfig()') if 'private void putCodecConfig()' in source else ''
failures = 0
for ok, name in (
    (put_config != '' and 'nextInputBuffer.remaining()' in put_config and put_config.index('nextInputBuffer.remaining()') < put_config.index('nextInputBuffer.put('),
     'codec config is size-checked before it is appended'),
    (len(re.findall(r'nextInputBuffer\.put\((vps|sps|pps)Buffer\)', submit)) == 0 and submit.count('putCodecConfig();') == 2,
     'both codec-config submission sites use the checked helper'),
    ('ByteBuffer.wrap(decodeUnitData, 0, decodeUnitLength)' in submit, 'the H.264 SPS is parsed over the unit length only'),
    (re.search(r'try \{\s*sps = H264Utils\.readSPS\(spsBuf\);\s*\} catch \(RuntimeException', submit) is not None,
     'a malformed SPS unwinds through the diagnosed path'),
    (re.search(r'try \{\s*escapedNalu = H264Utils\.writeSPS\(', submit) is not None, 'SPS re-encoding is guarded'),
    ('codecConfigFailure(' in method('    private RendererException codecConfigFailure(') if 'codecConfigFailure(' in source else False,
     'the failure helper exists'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
