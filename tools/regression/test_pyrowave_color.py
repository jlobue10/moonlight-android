#!/usr/bin/env python3
"""Evaluate production GLSL transfer helpers per channel and verify SPIR-V ABI.

No GPU execution: scalar C++ evaluates the component-wise GLSL functions. Surface
negotiation and push constants are exercised by test_pyrowave_fences.py.
--baseline reads the shader at HEAD and models its direct encoded writes.
"""
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
REL = 'app/src/main/jni/pyrowave-renderer/'
baseline = '--baseline' in sys.argv
def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + REL + path], cwd=ROOT, text=True)
            if baseline else (ROOT / REL / path).read_text())
source = read('shaders/planar_csc.frag')
def function(name):
    return re.search(r'vec3 ' + name + r'\([^)]*\)\s*\{[^}]+\}', source).group(0)
code = r'''
#include <algorithm>
#include <cmath>
#include <cstdio>
using vec3 = float;
using std::clamp; using std::pow;
float step(float edge, float x) { return x < edge ? 0.f : 1.f; }
float mix(float a, float b, float t) { return a * (1.f-t) + b*t; }
struct { int output_srgb; } p;
FUNCTIONS
int main() {
    bool encoded=true, linear=true, unorm=true;
    for(int i=0;i<=1024;i++) {
        float value=float(i)/1024.f;
        p.output_srgb=1;
        // Hardware sRGB encoding must reproduce the original encoded sample.
        encoded &= std::abs(srgb_oetf(attachment_color(value))-value)<0.000001f;
        // Tone-mapped linear output must not receive an extra OETF in the shader.
        linear &= std::abs(sdr_attachment_color(value)-value)<0.000001f;
        p.output_srgb=0;
        unorm &= attachment_color(value)==value &&
                 sdr_attachment_color(value)==srgb_oetf(value);
    }
    p.output_srgb=1;
    bool midtone=std::abs(attachment_color(0.5f)-0.21404114f)<0.000001f;
    bool clip=std::abs(sdr_attachment_color(-1.f))<0.000001f &&
              std::abs(sdr_attachment_color(2.f)-1.f)<0.000001f;
    std::printf("%s 1025 encoded SDR samples survive hardware sRGB encoding\n",encoded?"PASS":"FAIL");
    std::printf("%s 1025 linear tone-map samples avoid a duplicate transfer\n",linear?"PASS":"FAIL");
    std::printf("%s UNORM transfer behavior is preserved\n",unorm?"PASS":"FAIL");
    std::printf("%s encoded 0.5 maps to linear 0.21404114\n",midtone?"PASS":"FAIL");
    std::printf("%s tone-mapped out-of-gamut values clamp to the attachment range\n",clip?"PASS":"FAIL");
    return !(encoded && linear && unorm && midtone && clip);
}
'''
helpers = [function('srgb_eotf'), function('srgb_oetf')]
if 'vec3 attachment_color(' in source:
    helpers += [function('attachment_color'), function('sdr_attachment_color')]
else:
    helpers += ['vec3 attachment_color(vec3 encoded) { return encoded; }',
                'vec3 sdr_attachment_color(vec3 linear) { return srgb_oetf(linear); }']
# GLSL permits scalar arguments to vector clamp; C++ template deduction requires
# matching float literals. All functions operate independently on each channel.
translated = re.sub(r'(?<![\w.])(\d+\.\d+)(?![\w.])', r'\1f', '\n'.join(helpers))
with tempfile.TemporaryDirectory(prefix='pyrowave-color-') as directory:
    work = Path(directory)
    (work/'test.cpp').write_text(code.replace('FUNCTIONS', translated))
    subprocess.run(['g++', '-std=c++17', '-O2', str(work/'test.cpp'), '-o', str(work/'test')], check=True)
    result = subprocess.run([str(work/'test')]).returncode

# Confirm the checked-in binary exposes the same 40-byte push-constant block.
header = read('shaders_spv.h').split('planar_csc_frag_spv[] = {', 1)[1].split('};', 1)[0]
words = [int(w, 16) for w in re.findall(r'0x([0-9a-fA-F]+)', header)]
names, offsets = {}, {}
at = 5
while at < len(words):
    count, opcode = words[at] >> 16, words[at] & 0xffff
    assert count > 0 and at + count <= len(words), 'invalid SPIR-V instruction'
    args = words[at+1:at+count]
    if opcode == 5:  # OpName
        names[args[0]] = struct.pack('<'+'I'*(len(args)-1), *args[1:]).split(b'\0')[0].decode()
    elif opcode == 72 and args[2] == 35:  # OpMemberDecorate Offset
        offsets.setdefault(args[0], {})[args[1]] = args[3]
    at += count
params = [ident for ident, name in names.items() if name == 'Params']
abi_ok = len(params) == 1 and offsets.get(params[0]) == dict(enumerate([0,8,16,20,24,28,32,36]))
print(('PASS' if abi_ok else 'FAIL') + ' checked-in fragment SPIR-V has the 40-byte CSC layout')
raise SystemExit(result or not abi_ok)
