#!/usr/bin/env python3
"""Fault-inject the real JNI frame-buffer lifecycle at the VM allocation boundary.

Uses the JDK's JNI ABI and production setup/submit/cleanup functions, not a VM or
decoder. --baseline extracts HEAD production code; normal mode reads the worktree.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/jni/moonlight-core/callbacks.c'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())

def block(marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]

prefix = r'''
#include <jni.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "Limelight.h"
typedef struct {int size, locals, globals;} Array;
static Array original, allocated, intOriginal, intAllocated;
static jbyteArray DecodedFrameBuffer;
static jshortArray DecodedAudioBuffer;
static jclass GlobalBridgeClass;
static jmethodID BridgeDrSetupMethod=(jmethodID)1, BridgeDrSubmitDecodeUnitMethod=(jmethodID)2, BridgeDrCleanupMethod=(jmethodID)3;
static jmethodID BridgeArInitMethod=(jmethodID)4, BridgeArCleanupMethod=(jmethodID)5, BridgeDrSubmitPyroWaveFrameMethod=(jmethodID)6;
static int failArray, failGlobal, failWithException, pending, invalid, creates, setups, submissions, cleanups, setupResult, setupThrows;
static int arInits, arInitResult, arInitThrows, decoderCreates, decoderDestroys;
// PyroWave per-packet delivery (the production statics live above the extracted functions)
#define PYROWAVE_PACKET_LOST          0x1
#define PYROWAVE_PACKET_RECORD_START  0x2
#define PYROWAVE_PACKET_INTS          2
static bool PyroWaveStream;
static jintArray PyroWavePacketBuffer;
static jint* PyroWavePacketStaging;
static int PyroWavePacketStagingCount;
static int intCreates, pwSubmissions, pwLength, pwCount, pwCritical, stagedCount;
static jint stagedInts[64];
#define ANDROID_LOG_INFO 4
static int __android_log_print(int prio,const char* tag,const char* fmt,...){(void)prio;(void)tag;(void)fmt;return 0;}
const char* LiGetHostPyroWaveBitstreamId(void){return PYROWAVE_BITSTREAM_ID;}
typedef struct OpusMSDecoder OpusMSDecoder;
static OpusMSDecoder* Decoder;
static OPUS_MULTISTREAM_CONFIGURATION OpusConfig;
static OpusMSDecoder* opus_multistream_decoder_create(int a,int b,int c,int d,const unsigned char* e,int* err){++decoderCreates;*err=0;return (OpusMSDecoder*)&decoderCreates;}
static void opus_multistream_decoder_destroy(OpusMSDecoder* d){if(d)++decoderDestroys;}
static int errors, checks;
static void check(int ok,const char* text){++checks;printf("%s %s\n",ok?"PASS":"FAIL",text);errors+=!ok;}
static jbyteArray JNICALL makeArray(JNIEnv* e,jsize n){
    ++creates;if(failArray){pending=1;return NULL;}
    allocated=(Array){n,1,0};return (jbyteArray)&allocated;
}
static jshortArray JNICALL makeShortArray(JNIEnv* e,jsize n){return (jshortArray)makeArray(e,n);}
static jintArray JNICALL makeIntArray(JNIEnv* e,jsize n){
    ++intCreates;if(failArray){pending=1;return NULL;}
    intAllocated=(Array){n,1,0};return (jintArray)&intAllocated;
}
static void JNICALL setInts(JNIEnv* e,jintArray o,jsize off,jsize n,const jint* p){
    if(!o||pending||off+n>((Array*)o)->size||n>64){invalid++;return;}
    stagedCount=n;memcpy(stagedInts,p,n*sizeof(jint));
}
static jobject JNICALL makeGlobal(JNIEnv* e,jobject o){
    if(failGlobal){pending=failWithException;return NULL;}
    if(o==NULL){invalid++;return NULL;}((Array*)o)->globals++;return o;
}
static void JNICALL dropGlobal(JNIEnv* e,jobject o){if(o)((Array*)o)->globals--;}
static void JNICALL dropLocal(JNIEnv* e,jobject o){if(o)((Array*)o)->locals--;}
static jsize JNICALL length(JNIEnv* e,jarray o){if(!o||pending){invalid++;return 0;}return ((Array*)o)->size;}
static void JNICALL setBytes(JNIEnv* e,jbyteArray o,jsize off,jsize n,const jbyte* p){if(!o||pending||off+n>((Array*)o)->size)invalid++;}
static jint JNICALL callInt(JNIEnv* e,jclass c,jmethodID m,...){
    if(m==BridgeDrSetupMethod){++setups;pending=setupThrows;return setupResult;}
    if(m==BridgeArInitMethod){++arInits;pending=arInitThrows;return arInitResult;}
    if(m==BridgeDrSubmitPyroWaveFrameMethod){
        ++pwSubmissions;va_list a;va_start(a,m);jbyteArray b=va_arg(a,jbyteArray);pwLength=va_arg(a,jint);
        jintArray map=va_arg(a,jintArray);pwCount=va_arg(a,jint);pwCritical=va_arg(a,jint);va_end(a);
        if(!b||!map||pending||map!=PyroWavePacketBuffer)invalid++;return DR_OK;
    }
    ++submissions;va_list a;va_start(a,m);jbyteArray b=va_arg(a,jbyteArray);va_end(a);
    if(!b||pending)invalid++;return DR_OK;
}
static void JNICALL callVoid(JNIEnv* e,jclass c,jmethodID m,...){++cleanups;}
static jboolean JNICALL hasException(JNIEnv* e){return pending;}
static void JNICALL clearException(JNIEnv* e){pending=0;}
static jint JNICALL detach(JavaVM* vm){return JNI_OK;}
static const struct JNINativeInterface_ table={
    .NewByteArray=makeArray,.NewShortArray=makeShortArray,.NewIntArray=makeIntArray,.NewGlobalRef=makeGlobal,.DeleteGlobalRef=dropGlobal,
    .DeleteLocalRef=dropLocal,.GetArrayLength=length,.SetByteArrayRegion=setBytes,.SetIntArrayRegion=setInts,
    .CallStaticIntMethod=callInt,.CallStaticVoidMethod=callVoid,
    .ExceptionCheck=hasException,.ExceptionClear=clearException
};
static JNIEnv environment=&table;
static const struct JNIInvokeInterface_ vmTable={.DetachCurrentThread=detach};
static JavaVM vm=&vmTable;static JavaVM* JVM=&vm;
static JNIEnv* GetThreadEnv(void){return &environment;}
static void reset(void){
    original=(Array){32,0,1};allocated=(Array){0};DecodedFrameBuffer=(jbyteArray)&original;
    failArray=failGlobal=failWithException=pending=invalid=creates=setups=submissions=cleanups=setupResult=setupThrows=0;
    arInits=arInitResult=arInitThrows=decoderCreates=decoderDestroys=0;Decoder=NULL;DecodedAudioBuffer=NULL;
    PyroWaveStream=false;PyroWavePacketBuffer=NULL;free(PyroWavePacketStaging);PyroWavePacketStaging=NULL;PyroWavePacketStagingCount=0;
    intOriginal=(Array){0};intAllocated=(Array){0};intCreates=pwSubmissions=pwLength=pwCount=pwCritical=stagedCount=0;
}
'''
suffix = r'''
int main(void){
    unsigned char bytes[64]={0};LENTRY entry={.data=(char*)bytes,.length=64,.bufferType=BUFFER_TYPE_PICDATA};
    DECODE_UNIT unit={.fullLength=64,.bufferList=&entry};
    reset();failArray=1;
    int result=BridgeDrSubmitDecodeUnit(&unit);
    check(result==DR_NEED_IDR&&DecodedFrameBuffer==(jbyteArray)&original&&original.globals==1&&!pending&&!invalid&&!submissions,
          "array allocation failure preserves the current buffer and requests an IDR");
    for(int exception=0;exception<2;exception++){
        reset();failGlobal=1;failWithException=exception;
        result=BridgeDrSubmitDecodeUnit(&unit);
        check(result==DR_NEED_IDR&&DecodedFrameBuffer==(jbyteArray)&original&&original.globals==1&&allocated.locals==0&&!pending&&!invalid&&!submissions,
              exception?"global-reference failure with an exception is recoverable":"global-reference failure without an exception is recoverable");
        failGlobal=0;result=BridgeDrSubmitDecodeUnit(&unit);
        check(result==DR_OK&&DecodedFrameBuffer==(jbyteArray)&allocated&&allocated.globals==1&&allocated.locals==0&&original.globals==0&&!invalid,
              "a subsequent frame can grow the buffer after allocation failure");
    }
    reset();unit.fullLength=entry.length=16;result=BridgeDrSubmitDecodeUnit(&unit);
    check(result==DR_OK&&creates==0&&submissions==1&&!invalid,"a fitting frame reuses the buffer without allocation");
    for(int failure=0;failure<2;failure++){
        reset();DecodedFrameBuffer=NULL;original.globals=0;failArray=failure==0;failGlobal=failure==1;failWithException=1;
        result=BridgeDrSetup(1,1920,1080,60,NULL,0);
        check(result!=0&&!setups&&DecodedFrameBuffer==NULL&&allocated.locals==0&&allocated.globals==0&&!pending&&!invalid,
              failure?"setup stops cleanly if the global reference cannot be allocated":"setup stops before decoder initialization if the array cannot be allocated");
    }
    reset();DecodedFrameBuffer=NULL;original.globals=0;result=BridgeDrSetup(1,1920,1080,60,NULL,0);
    check(result==0&&setups==1&&DecodedFrameBuffer==(jbyteArray)&allocated&&allocated.globals==1&&allocated.locals==0,
          "successful setup owns one global reference and no local reference");
    BridgeDrCleanup();
    check(DecodedFrameBuffer==NULL&&allocated.globals==0&&cleanups==1,"cleanup releases and clears the shared frame buffer");
    for(int throws=0;throws<2;throws++){
        reset();DecodedFrameBuffer=NULL;original.globals=0;setupResult=throws?0:-3;setupThrows=throws;
        result=BridgeDrSetup(1,1920,1080,60,NULL,0);
        check(result!=0&&DecodedFrameBuffer==NULL&&allocated.locals==0&&allocated.globals==0,
              throws?"decoder setup exception does not leak the new global reference":"decoder setup rejection does not leak the new global reference");
    }
    // PyroWave streams deliver every packet buffer in order with a packet map instead of
    // splitting by buffer type; lost stand-ins keep their length so offsets stay aligned.
    {
        unsigned char zeros[8]={0};
        LENTRY third={.next=NULL,.data=(char*)bytes+16,.length=8,.bufferType=BUFFER_TYPE_PICDATA};
        LENTRY lost={.next=&third,.data=(char*)zeros,.length=8,.bufferType=BUFFER_TYPE_LOST};
        LENTRY first={.next=&lost,.data=(char*)bytes,.length=8,.bufferType=BUFFER_TYPE_RECORD_START};
        DECODE_UNIT pw={.fullLength=24,.bufferList=&first,.pyrowaveCriticalPackets=2};
        jint expected[]={8,PYROWAVE_PACKET_RECORD_START,8,PYROWAVE_PACKET_LOST,8,0};
        reset();PyroWaveStream=true;result=BridgeDrSubmitDecodeUnit(&pw);
        check(result==DR_OK&&pwSubmissions==1&&submissions==0&&pwLength==24&&pwCount==3&&pwCritical==2&&stagedCount==6&&
              memcmp(stagedInts,expected,sizeof(expected))==0&&PyroWavePacketBuffer==(jintArray)&intAllocated&&intAllocated.size==6&&
              intAllocated.globals==1&&intAllocated.locals==0&&!invalid&&!pending,
              "a PyroWave frame is delivered whole with its packet map, lost stand-ins included");
        // The map only grows; a fitting frame reuses it, a larger one replaces it and releases the old reference
        reset();PyroWaveStream=true;intOriginal=(Array){6,0,1};PyroWavePacketBuffer=(jintArray)&intOriginal;
        result=BridgeDrSubmitDecodeUnit(&pw);
        check(result==DR_OK&&intCreates==0&&pwSubmissions==1&&PyroWavePacketBuffer==(jintArray)&intOriginal&&!invalid,
              "a fitting packet map is reused without allocation");
        reset();PyroWaveStream=true;intOriginal=(Array){2,0,1};PyroWavePacketBuffer=(jintArray)&intOriginal;
        result=BridgeDrSubmitDecodeUnit(&pw);
        check(result==DR_OK&&intCreates==1&&PyroWavePacketBuffer==(jintArray)&intAllocated&&intAllocated.globals==1&&intOriginal.globals==0&&!invalid,
              "a larger frame grows the packet map and releases the old reference");
        // Map allocation failure keeps the old map and asks for another frame, like the frame buffer
        reset();PyroWaveStream=true;intOriginal=(Array){2,0,1};PyroWavePacketBuffer=(jintArray)&intOriginal;failArray=1;
        result=BridgeDrSubmitDecodeUnit(&pw);
        check(result==DR_NEED_IDR&&pwSubmissions==0&&PyroWavePacketBuffer==(jintArray)&intOriginal&&intOriginal.globals==1&&!pending&&!invalid,
              "packet map allocation failure preserves the current map and requests a new frame");
        failArray=0;result=BridgeDrSubmitDecodeUnit(&pw);
        check(result==DR_OK&&pwSubmissions==1&&PyroWavePacketBuffer==(jintArray)&intAllocated&&intOriginal.globals==0,
              "a subsequent frame can grow the packet map after allocation failure");
        // An empty buffer carries no bytes: it leaves the map and shrinks the critical prefix it was counted in
        LENTRY tail={.next=NULL,.data=(char*)zeros,.length=8,.bufferType=BUFFER_TYPE_LOST};
        LENTRY empty={.next=&tail,.data=(char*)bytes+8,.length=0,.bufferType=BUFFER_TYPE_PICDATA};
        LENTRY head={.next=&empty,.data=(char*)bytes,.length=8,.bufferType=BUFFER_TYPE_RECORD_START};
        DECODE_UNIT sparse={.fullLength=16,.bufferList=&head,.pyrowaveCriticalPackets=2};
        jint expectedSparse[]={8,PYROWAVE_PACKET_RECORD_START,8,PYROWAVE_PACKET_LOST};
        reset();PyroWaveStream=true;result=BridgeDrSubmitDecodeUnit(&sparse);
        check(result==DR_OK&&pwLength==16&&pwCount==2&&pwCritical==1&&stagedCount==4&&memcmp(stagedInts,expectedSparse,sizeof(expectedSparse))==0&&!invalid,
              "an empty buffer is left out of the packet map and the critical prefix");
        // Setup remembers the negotiated format; cleanup releases the map with the frame buffer
        reset();DecodedFrameBuffer=NULL;original.globals=0;result=BridgeDrSetup(0x10000,1920,1080,60,NULL,0);
        check(result==0&&PyroWaveStream,"setup of a PyroWave stream selects per-packet delivery");
        result=BridgeDrSubmitDecodeUnit(&pw);
        BridgeDrCleanup();
        check(result==DR_OK&&PyroWavePacketBuffer==NULL&&intAllocated.globals==0&&PyroWavePacketStaging==NULL&&!PyroWaveStream&&cleanups==1,
              "cleanup releases the packet map and staging with the frame buffer");
        reset();DecodedFrameBuffer=NULL;original.globals=0;result=BridgeDrSetup(1,1920,1080,60,NULL,0);
        check(result==0&&!PyroWaveStream,"setup of an H.264 stream keeps the split-by-type path");
        BridgeDrCleanup();
    }
    // The decoded-audio buffer follows the same ownership rules as the frame buffer.
    OPUS_MULTISTREAM_CONFIGURATION opus={.sampleRate=48000,.channelCount=2,.streams=1,.coupledStreams=1,.samplesPerFrame=240};
    for(int failure=0;failure<2;failure++){
        reset();failArray=failure==0;failGlobal=failure==1;failWithException=1;
        result=BridgeArInit(0,&opus,NULL,0);
        check(result!=0&&arInits==1&&decoderCreates==1&&decoderDestroys==1&&Decoder==NULL&&DecodedAudioBuffer==NULL&&allocated.locals==0&&allocated.globals==0&&!pending&&cleanups==1,
              failure?"audio init stops cleanly if the global reference cannot be allocated":"audio init stops cleanly if the array cannot be allocated");
    }
    reset();arInitResult=-2;result=BridgeArInit(0,&opus,NULL,0);
    check(result!=0&&decoderCreates==0&&DecodedAudioBuffer==NULL&&creates==0,"audio init rejection allocates nothing");
    reset();result=BridgeArInit(0,&opus,NULL,0);
    check(result==0&&DecodedAudioBuffer==(jshortArray)&allocated&&allocated.globals==1&&allocated.locals==0&&Decoder!=NULL&&allocated.size==480,
          "successful audio init owns one global reference, no local reference, and the decoder");
    BridgeArCleanup();
    check(DecodedAudioBuffer==NULL&&Decoder==NULL&&allocated.globals==0&&decoderDestroys==1&&cleanups==1,"audio cleanup releases and clears the buffer and the decoder");
    printf("%d checks, %d failures\n",checks,errors);return errors!=0;
}
'''
helpers = [m for m in ['static jbyteArray createDecodedFrameBuffer(', 'static jintArray createPacketBuffer(',
                       'static int BridgeDrSubmitPyroWaveFrame('] if m in source]
code = prefix + '\n'.join(block(m) for m in helpers + ['int BridgeDrSetup(', 'void BridgeDrCleanup(', 'int BridgeDrSubmitDecodeUnit(', 'int BridgeArInit(', 'void BridgeArCleanup(']) + suffix
props = subprocess.run(['java', '-XshowSettings:properties', '-version'], capture_output=True, text=True, check=True).stderr
jdk = Path(re.search(r'java.home = (.+)', props).group(1).strip())
jni_include = Path(os.environ.get('AUDIT_JNI_INCLUDE', str(jdk / 'include')))
with tempfile.TemporaryDirectory(prefix='jni-frame-buffer-') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(code)
    command = ['cc', '-std=c11', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
               '-I' + str(jni_include), '-I' + str(jni_include / 'linux'),
               '-I' + str(ROOT / 'app/src/main/jni/moonlight-core/moonlight-common-c/src'),
               str(work / 'test.c'), '-o', str(work / 'test')]
    subprocess.run(command, check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=os.environ).returncode)
