#!/usr/bin/env python3
"""Compile the real renderer and feed synthetic PyroWave record frames through the
hole-tolerant per-packet path (decodeFrame/parseRecordFrame) next to the strict
whole-frame parser (pushRecordFrame).

Frames are built the way Vibepollo packs them: a sequence header, the coarsest
level's block records, the finer levels' records (oversized ones first, then the
rest packed so that none crosses a packet boundary) and padding records that fill
every packet. The PyroWave decoder is a capture stub, so the checks compare the
bytes that would reach it. No GPU, NDK or PyroWave shared library. --baseline uses HEAD.
"""
import pathlib
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
RENDERER = ROOT/'app/src/main/jni/pyrowave-renderer'
STUBS = {
'jni.h': '''#pragma once
#include <cstdint>
#define JNIEXPORT
#define JNICALL
#define JNI_TRUE 1
#define JNI_FALSE 0
using jboolean=bool; using jlong=int64_t; using jint=int; using jfloat=float;
using jobject=void*; using jclass=void*; using jbyteArray=void*; using jintArray=void*; using jbyte=signed char;
struct JNIEnv {int GetArrayLength(jbyteArray){return 0;} void GetByteArrayRegion(jbyteArray,int,int,jbyte*){}
 void GetIntArrayRegion(jintArray,int,int,jint*){}};
''',
'android/native_window_jni.h': '''#pragma once
#include <jni.h>
struct ANativeWindow {};
inline ANativeWindow* ANativeWindow_fromSurface(JNIEnv*,jobject){return nullptr;}
inline void ANativeWindow_release(ANativeWindow*){}
''',
'android/log.h': '''#pragma once
#define ANDROID_LOG_INFO 4
#define ANDROID_LOG_WARN 5
#define ANDROID_LOG_ERROR 6
inline int __android_log_print(int,const char*,const char*,...){return 0;}
''',
'sys/system_properties.h': '''#pragma once
#define PROP_VALUE_MAX 92
inline int __system_property_get(const char*,char*){return 0;}
'''}

# Stream geometry of every fixture: 256x128 4:2:0 has 12 coarsest-level blocks and
# 72 blocks in all (bitstream.md: five levels, 4:2:0 drops chroma level 0).
WIDTH, HEIGHT = 256, 128
COARSE_BLOCKS, TOTAL_BLOCKS = 12, 72
PACKET = 96
PADDING_MAGIC = 0xFFFFFFFF
SUBMIT_OK, SUBMIT_SKIPPED, SUBMIT_SKIPPED_LOSS = 0, 1, 2


def sequence_header(total_blocks=TOTAL_BLOCKS, width=WIDTH, height=HEIGHT, sequence=0, code=0, chroma444=False):
    word0 = 0x80000000 | (sequence << 28) | ((height - 1) << 14) | (width - 1)
    word1 = total_blocks | (code << 24) | (int(chroma444) << 26)
    return struct.pack('<II', word0, word1)


def block(index, size, sequence=0):
    # size in bytes including the 8-byte header; payload_words counts the header too
    words = size // 4
    word0 = (sequence << 28) | (words << 16) | 0xBEEF
    word1 = (index << 8) | 0x11
    body = bytes((index * 7 + i + 1) & 0xFF for i in range(size - 8))
    return struct.pack('<II', word0, word1) + body


def padding(size):
    return struct.pack('<II', PADDING_MAGIC, size // 4 - 2) + b'\0' * (size - 8)


def records(coarse_size=16, oversized=(200, 200), ordinary=24, total=TOTAL_BLOCKS):
    """Sequence header, coarse blocks, oversized finer records, then ordinary ones."""
    out = [sequence_header(total)]
    index = 0
    for _ in range(COARSE_BLOCKS):
        out.append(block(index, coarse_size)); index += 1
    for size in oversized:
        out.append(block(index, size)); index += 1
    while index < total:
        out.append(block(index, ordinary)); index += 1
    return out


def packetize(items, size=PACKET):
    """Packs records into packets with padding, Vibepollo style. Returns the frame
    bytes, the packets as (length, record_start) and each record's [start, end)."""
    packets, flags, spans = [], [], []
    cur, cur_flag, offset = bytearray(), True, 0

    def flush():
        nonlocal cur, cur_flag
        packets.append(bytes(cur)); flags.append(cur_flag); cur = bytearray(); cur_flag = False

    def pad_out():
        nonlocal cur
        cur += padding(size - len(cur)); flush()

    for record in items:
        if len(cur) == 0:
            cur_flag = True
        if len(record) > size - len(cur):
            if len(cur):
                pad_out()
            cur_flag = True
        start = sum(map(len, packets)) + len(cur)
        spans.append((start, start + len(record)))
        data = record
        while len(data) > size - len(cur):
            take = size - len(cur); cur += data[:take]; data = data[take:]; flush()
        cur += data
        if len(cur) == size:
            flush()
    if len(cur):
        pad_out()
    frame = b''.join(packets)
    return frame, [(len(p), f) for p, f in zip(packets, flags)], spans


def expected_bytes(items, spans, packets, lost, flagged=True):
    """Records the hole-tolerant walk keeps: none that touches a lost packet or the
    dead zone from a lost packet to the next received packet that starts a record."""
    starts = []
    pos = 0
    for length, flag in packets:
        starts.append(pos); pos += length
    ends = [s + l for s, (l, _) in zip(starts, packets)]
    zones = []
    for i in lost:
        nxt = next((starts[j] for j in range(i + 1, len(packets))
                    if j not in lost and (packets[j][1] or not flagged)), pos)
        zones.append((starts[i], nxt))
    kept = []
    for record, (start, end) in zip(items, spans):
        if not any(start < z1 and end > z0 for z0, z1 in zones):
            kept.append(record)
    return b''.join(kept)


def critical_count(spans, packets):
    """Leading packets holding the sequence header and the coarse blocks."""
    end = spans[COARSE_BLOCKS][1]
    pos = 0
    for i, (length, _) in enumerate(packets):
        pos += length
        if pos >= end:
            return i + 1
    raise AssertionError


fixtures = []


def fixture(name, frame, packets, lost, critical, result, expected=None, strict_same=False,
            partial=None, coarse_intact=None, flagged=True):
    pk = [(length, int(i in lost), int(flag and flagged)) for i, (length, flag) in enumerate(packets)]
    data = bytearray(frame)
    pos = 0
    for i, (length, _) in enumerate(packets):
        if i in lost:
            data[pos:pos + length] = b'\0' * length  # moonlight-common-c zero-fills lost packets
        pos += length
    fixtures.append(dict(name=name, data=bytes(data), packets=pk, critical=critical, result=result,
                         expected=expected, strict_same=strict_same, partial=partial, coarse_intact=coarse_intact))


items = records()
frame, packets, spans = packetize(items)
critical = critical_count(spans, packets)
assert any(not f for _, f in packets[1:]), 'the oversized records must span packets'
first_ordinary_packet = next(i for i, (l, f) in enumerate(packets) if f and i > critical and
                             sum(packets[k][0] for k in range(i)) > spans[COARSE_BLOCKS + 2][1])

# (a) no loss: same bytes as the strict parser, from one push
fixture('intact frame matches the strict parser byte for byte', frame, packets, set(), critical, SUBMIT_OK,
        strict_same=True, partial=0, coarse_intact=1)
fixture('intact frame with an unknown critical count', frame, packets, set(), 0, SUBMIT_OK,
        strict_same=True, partial=0, coarse_intact=1)
# (e) padding stripped: the strict parser strips it too, so (a) covers the bytes;
# also check that no padding header reaches the decoder (done in C++ for every fixture)
# (b) a lost detail packet: the frame is kept, the record spanning into the hole is
# dropped, later records resync at the next flagged packet
lost = {first_ordinary_packet}
fixture('lost ordinary detail packet keeps the frame and resyncs at the next record start', frame, packets, lost,
        critical, SUBMIT_OK, expected=expected_bytes(items, spans, packets, lost), partial=1, coarse_intact=1)
spanning = next(i for i, (l, f) in enumerate(packets) if not f and i > 0)
lost = {spanning - 1}
fixture('lost packet under an oversized record drops that record and resyncs later', frame, packets, lost,
        critical, SUBMIT_OK, expected=expected_bytes(items, spans, packets, lost), partial=1, coarse_intact=1)
lost = {first_ordinary_packet, first_ordinary_packet + 1, len(packets) - 1}
fixture('a burst and the tail packet lost still decode what arrived', frame, packets, lost,
        critical, SUBMIT_OK, expected=expected_bytes(items, spans, packets, lost), partial=1, coarse_intact=1)
fixture('unknown critical count: loss after the first finer block decodes', frame, packets,
        {first_ordinary_packet}, 0, SUBMIT_OK, expected=expected_bytes(items, spans, packets, {first_ordinary_packet}),
        partial=1, coarse_intact=1)
# unflagged host: resync at the next received packet once ordinary finer records are behind
lost = {first_ordinary_packet + 1}
fixture('unflagged host resyncs at the next received packet after ordinary detail records', frame, packets, lost,
        critical, SUBMIT_OK, expected=expected_bytes(items, spans, packets, lost, flagged=False), partial=1,
        coarse_intact=1, flagged=False)
# (c) loss inside the critical prefix
fixture('lost packet inside the announced critical prefix skips the frame for loss', frame, packets, {1}, critical,
        SUBMIT_SKIPPED_LOSS, partial=1, coarse_intact=0)
fixture('unknown critical count: loss before the first finer block skips the frame for loss', frame, packets, {1}, 0,
        SUBMIT_SKIPPED_LOSS, partial=1, coarse_intact=0)
# (d) the sequence header's packet lost
fixture('lost first packet (sequence header) skips the frame for loss', frame, packets, {0}, critical,
        SUBMIT_SKIPPED_LOSS)
# (f) malformed records with a packet map are rejected like before
bad = list(items); bad[5] = struct.pack('<II', (1 << 16) | 0xBEEF, 5 << 8) + bad[5][8:]  # payload_words 1
bframe, bpackets, _ = packetize(bad)
fixture('block shorter than its header is rejected with a packet map', bframe, bpackets, set(), critical, SUBMIT_SKIPPED,
        strict_same=True)
# a record declaring more bytes than the frame holds, in a trailing packet of its own
fixture('record running past the frame is rejected with a packet map', frame + block(TOTAL_BLOCKS - 1, 24)[:16],
        packets + [(16, True)], set(), critical, SUBMIT_SKIPPED, strict_same=True)
bad = list(items); bad[0] = sequence_header(TOTAL_BLOCKS + 1)
bframe, bpackets, _ = packetize(bad)
fixture('intact frame with fewer blocks than announced is rejected', bframe, bpackets, set(), critical, SUBMIT_SKIPPED)
bad = list(items); bad[0] = sequence_header(width=WIDTH + 32)
bframe, bpackets, _ = packetize(bad)
fixture('sequence header for another stream size is rejected', bframe, bpackets, set(), critical, SUBMIT_SKIPPED)
bad = list(items); bad[20] = block(5000, 24)
bframe, bpackets, _ = packetize(bad)
fixture('out-of-range block index is rejected', bframe, bpackets, set(), critical, SUBMIT_SKIPPED)
bad = [padding(16), block(0, 16)] + list(items)  # padding is fine anywhere, a block is not
bframe, bpackets, _ = packetize(bad)
fixture('block record before the sequence header is rejected', bframe, bpackets, set(), critical,
        SUBMIT_SKIPPED, strict_same=True)
bad = list(items); bad[30] = block(30, 24, sequence=3)
bframe, bpackets, _ = packetize(bad)
fixture('block record from another frame is rejected', bframe, bpackets, set(), critical, SUBMIT_SKIPPED)
# a lost packet in a frame of another container cannot be resynchronised
prefixed = struct.pack('<I', 1) + struct.pack('<I', 88) + b'\x11' * 88
fixture('length-prefixed frame with a lost packet is skipped', prefixed, [(48, False), (48, False)], {1}, 0,
        SUBMIT_SKIPPED)


def cbytes(data):
    return '{' + ','.join(str(b) for b in data) + '}' if data else '{0}'


header = ['#include <cstddef>', '#include <cstdint>',
          'struct Fixture { const char* name; const uint8_t* data; size_t size; const uint32_t* packets; size_t packetCount;'
          ' size_t critical; int result; const uint8_t* expected; long expectedSize; int strictSame; int partial; int coarseIntact; };']
for i, f in enumerate(fixtures):
    header.append('static const uint8_t data%d[] = %s;' % (i, cbytes(f['data'])))
    header.append('static const uint32_t packets%d[] = {%s};' % (i, ','.join('%d,%d,%d' % p for p in f['packets'])))
    header.append('static const uint8_t expected%d[] = %s;' % (i, cbytes(f['expected'] or b'')))
header.append('static const Fixture fixtures[] = {')
for i, f in enumerate(fixtures):
    header.append('  {"%s", data%d, sizeof(data%d), packets%d, %d, %d, %d, expected%d, %d, %d, %d, %d},' % (
        f['name'], i, i, i, len(f['packets']), f['critical'], f['result'], i,
        len(f['expected']) if f['expected'] is not None else -1, int(f['strict_same']),
        -1 if f['partial'] is None else f['partial'], -1 if f['coarse_intact'] is None else f['coarse_intact']))
header.append('};')

TEST = r'''
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <memory>
#include <mutex>
#include <vector>
#include <cstdio>
#define private public
#include "renderer.cpp"
#undef private
#include "fixtures.h"
static std::vector<std::vector<uint8_t>> pushes;
static int sidebandCalls = 0, strictCalls = 0;
extern "C" {
void pyrowave_device_set_command_buffer(pyrowave_device,VkCommandBuffer){}
pyrowave_result pyrowave_decoder_decode_gpu_buffer(pyrowave_decoder,const pyrowave_gpu_sync_operation*,
 const pyrowave_gpu_sync_operation*,const pyrowave_gpu_buffers*){return PYROWAVE_SUCCESS;}
void pyrowave_device_report_performance_stats(pyrowave_device,pyrowave_message_cb,void*,bool){}
void pyrowave_decoder_destroy(pyrowave_decoder){}
void pyrowave_device_destroy(pyrowave_device){}
void pyrowave_decoder_clear(pyrowave_decoder){pushes.clear();}
pyrowave_result pyrowave_decoder_push_packet(pyrowave_decoder,const void* data,size_t size){
 const uint8_t* p=static_cast<const uint8_t*>(data);pushes.emplace_back(p,p+size);return PYROWAVE_SUCCESS;}
bool pyrowave_decoder_decode_is_ready(pyrowave_decoder,bool){++strictCalls;return true;}
bool pyrowave_decoder_decode_is_ready_with_sideband(pyrowave_decoder,bool,int,float,const uint32_t*,size_t){++sidebandCalls;return true;}
}
static std::vector<uint8_t> pushed(){std::vector<uint8_t> all;for(auto&p:pushes)all.insert(all.end(),p.begin(),p.end());return all;}
static bool hasPadding(const std::vector<uint8_t>& bytes){
 for(size_t i=0;i+4<=bytes.size();i+=4){uint32_t w;std::memcpy(&w,bytes.data()+i,4);if(w==0xFFFFFFFFu)return true;}return false;}
int main(){
 int failures=0,checks=0;
 auto check=[&](bool ok,const char* name){++checks;failures+=!ok;printf("%s %s\n",ok?"PASS":"FAIL",name);};
 Renderer r;r.width=WIDTH_;r.height=HEIGHT_;r.chroma444=false;
 for(const Fixture& f:fixtures){
  std::vector<PacketSegment> segments;size_t offset=0;
  for(size_t i=0;i<f.packetCount;i++){segments.push_back({offset,f.packets[i*3],f.packets[i*3+1]!=0,f.packets[i*3+2]!=0});offset+=f.packets[i*3];}
  pushes.clear();sidebandCalls=strictCalls=0;
  const int result=r.decodeFrame(f.data,f.size,segments,f.critical);
  std::vector<uint8_t> bytes=pushed();
  bool ok=result==f.result;
  if(f.result==SUBMIT_OK){
   ok=ok&&pushes.size()==1&&!hasPadding(bytes)&&!bytes.empty();
   // A partial frame uses the lenient readiness check; an intact one the usual one
   ok=ok&&(r.recordFrame.partial?sidebandCalls==1&&strictCalls==0:sidebandCalls==0&&strictCalls==1);
  } else {
   ok=ok&&pushes.empty();
  }
  if(f.expectedSize>=0)ok=ok&&bytes==std::vector<uint8_t>(f.expected,f.expected+f.expectedSize);
  if(f.partial>=0)ok=ok&&r.recordFrame.partial==(f.partial!=0);
  if(f.coarseIntact>=0)ok=ok&&r.recordFrame.coarseLevelIntact==(f.coarseIntact!=0);
  if(f.strictSame){
   pushes.clear();const bool strictOk=r.pushRecordFrame(f.data,f.size);
   ok=ok&&strictOk==(f.result==SUBMIT_OK)&&(!strictOk||pushed()==bytes);
  }
  if(!ok)printf("  result %d (expected %d), %zu pushes, %zu bytes, partial %d, coarse intact %d\n",result,f.result,
                pushes.size(),bytes.size(),int(r.recordFrame.partial),int(r.recordFrame.coarseLevelIntact));
  check(ok,f.name);
 }
 // An empty packet map takes the legacy whole-frame path
 {
  const Fixture& f=fixtures[0];pushes.clear();
  const int result=r.decodeFrame(f.data,f.size,{},0);std::vector<uint8_t> bytes=pushed();
  pushes.clear();r.pushRecordFrame(f.data,f.size);
  check(result==SUBMIT_OK&&bytes==pushed(),"no packet map falls back to the strict whole-frame parser");
 }
 // A map that does not tile the frame is rejected before any record is read
 {
  const Fixture& f=fixtures[0];pushes.clear();
  std::vector<PacketSegment> segments={{0,f.size-4,false,true}};
  check(r.decodeFrame(f.data,f.size,segments,0)==SUBMIT_SKIPPED&&pushes.empty(),"a packet map that does not tile the frame is rejected");
 }
 // Lossy frames are logged on the first and then every LOSSY_FRAME_LOG_INTERVAL
 check(r.lossyFrames>0,"lossy frames are counted for rate-limited logging");
 printf("%d checks, %d failures\n",checks,failures);
 return failures!=0;
}
'''.replace('WIDTH_', str(WIDTH)).replace('HEIGHT_', str(HEIGHT))

with tempfile.TemporaryDirectory(prefix='pyrowave-framing-') as directory:
    work = pathlib.Path(directory)
    for name, source in STUBS.items():
        path = work/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    source = (subprocess.check_output(['git','show','HEAD:app/src/main/jni/pyrowave-renderer/pyrowave_renderer.cpp'],cwd=ROOT,text=True)
              if '--baseline' in sys.argv else (RENDERER/'pyrowave_renderer.cpp').read_text())
    (work/'renderer.cpp').write_text(source)
    (work/'fixtures.h').write_text('\n'.join(header) + '\n')
    (work/'test.cpp').write_text(TEST)
    subprocess.run(['g++','-std=c++17','-O1','-g','-fsanitize=address,undefined',
        '-ffunction-sections','-fdata-sections','-I'+str(work),'-I'+str(RENDERER),
        '-I'+str(RENDERER/'pyrowave/Granite/third_party/khronos/vulkan-headers/include'),
        str(work/'test.cpp'),'-Wl,--gc-sections','-ldl','-pthread','-o',str(work/'test')],check=True)
    raise SystemExit(subprocess.run([str(work/'test')]).returncode)
