#!/usr/bin/env python3
"""Compile the production IEC 61937 burst packer (app/src/main/jni/ptenc/iec61937.c) on the host.

Checks the preamble words, the payload length field, the big- to little-endian word swap,
an odd final byte, zero padding to the burst size, the AC-3 bsmod bits and rejection of
frames that do not fit or arguments that are invalid. No libavcodec is needed.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PTENC = ROOT / 'app/src/main/jni/ptenc'

TEST = r'''
#include <stdio.h>
#include <string.h>
#include "iec61937.h"

static int checks, failures;
static void check(int ok, const char* name) {
    ++checks; if (!ok) ++failures;
    printf("%s %s\n", ok ? "PASS" : "FAIL", name);
}
static unsigned le16(const uint8_t* p) { return p[0] | (p[1] << 8); }

int main(void) {
    uint8_t out[6144];
    // A fake AC-3 frame: sync word, CRC, fscod/frmsizecod, bsid/bsmod (byte 5 low bits = 3), then a ramp
    uint8_t ac3[1024];
    for (int i = 0; i < (int)sizeof(ac3); i++) ac3[i] = (uint8_t)(i * 7 + 1);
    ac3[0] = 0x0B; ac3[1] = 0x77; ac3[5] = 0x43;

    memset(out, 0xEE, sizeof(out));
    int n = iec61937_pack(IEC61937_AC3, ac3, sizeof(ac3), out, 6144);
    check(n == 6144, "an AC-3 frame packs into a 6144-byte burst");
    check(le16(out) == 0xF872 && le16(out + 2) == 0x4E1F, "Pa/Pb sync words are F872 4E1F");
    check(le16(out + 4) == (0x01 | (3 << 8)), "Pc is AC-3 with bsmod 3 in bits 8..10");
    check(le16(out + 6) == 1024 * 8, "Pd is the payload length in bits");
    check(out[8] == ac3[1] && out[9] == ac3[0] && out[8 + 1022] == ac3[1023] && out[8 + 1023] == ac3[1022],
          "payload words are byte swapped to little endian");
    int zero = 1;
    for (int i = 8 + 1024; i < 6144; i++) zero &= out[i] == 0;
    check(zero, "the burst is zero padded to its full length");

    // Odd length: the last byte is MSB aligned in one more word and Pd rounds up
    uint8_t dts[2011];
    for (int i = 0; i < (int)sizeof(dts); i++) dts[i] = (uint8_t)(i ^ 0x5A);
    memset(out, 0xEE, sizeof(out));
    n = iec61937_pack(IEC61937_DTS_TYPE_I, dts, sizeof(dts), out, 2048);
    check(n == 2048, "a DTS frame packs into a 2048-byte burst");
    check(le16(out + 4) == 0x0B, "Pc is DTS type I");
    check(le16(out + 6) == 2012 * 8, "Pd rounds an odd payload up to whole words");
    check(out[8 + 2010] == 0 && out[8 + 2011] == dts[2010], "a lone final byte is MSB aligned");
    zero = 1;
    for (int i = 8 + 2012; i < 2048; i++) zero &= out[i] == 0;
    check(zero, "padding follows the odd payload");

    // Rejections
    check(iec61937_pack(IEC61937_DTS_TYPE_I, dts, 2041, out, 2048) < 0, "a frame that does not fit the burst is rejected");
    check(iec61937_pack(IEC61937_DTS_TYPE_I, dts, 2040, out, 2048) == 2048, "a frame that exactly fills the burst is accepted");
    check(iec61937_pack(IEC61937_AC3, ac3, 5, out, 6144) < 0, "an AC-3 frame too short to carry bsmod is rejected");
    check(iec61937_pack(0x07, ac3, 64, out, 6144) < 0, "an unknown data type is rejected");
    check(iec61937_pack(IEC61937_AC3, NULL, 64, out, 6144) < 0 && iec61937_pack(IEC61937_AC3, ac3, 0, out, 6144) < 0,
          "null data and empty frames are rejected");

    printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
'''

compiler = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
if compiler is None:
    print('no C compiler found', file=sys.stderr)
    raise SystemExit(1)

with tempfile.TemporaryDirectory(prefix='iec61937-') as temp:
    work = Path(temp)
    (work / 'test.c').write_text(TEST, encoding='utf-8')
    exe = work / ('test.exe' if os.name == 'nt' else 'test')
    flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-O1', '-g']
    if os.name != 'nt':
        flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    subprocess.run([compiler, *flags, '-I', str(PTENC), str(PTENC / 'iec61937.c'), str(work / 'test.c'), '-o', str(exe)],
                   check=True)
    raise SystemExit(subprocess.run([str(exe)], timeout=15).returncode)
