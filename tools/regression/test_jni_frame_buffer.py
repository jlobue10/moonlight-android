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
#include <string.h>
#include "Limelight.h"
typedef struct {int size, locals, globals;} Array;
static Array original, allocated;
static jbyteArray DecodedFrameBuffer;
static jclass GlobalBridgeClass;
static jmethodID BridgeDrSetupMethod=(jmethodID)1, BridgeDrSubmitDecodeUnitMethod=(jmethodID)2, BridgeDrCleanupMethod=(jmethodID)3;
static int failArray, failGlobal, failWithException, pending, invalid, creates, setups, submissions, cleanups, setupResult, setupThrows;
static int errors, checks;
static void check(int ok,const char* text){++checks;printf("%s %s\n",ok?"PASS":"FAIL",text);errors+=!ok;}
static jbyteArray JNICALL makeArray(JNIEnv* e,jsize n){
    ++creates;if(failArray){pending=1;return NULL;}
    allocated=(Array){n,1,0};return (jbyteArray)&allocated;
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
    ++submissions;va_list a;va_start(a,m);jbyteArray b=va_arg(a,jbyteArray);va_end(a);
    if(!b||pending)invalid++;return DR_OK;
}
static void JNICALL callVoid(JNIEnv* e,jclass c,jmethodID m,...){++cleanups;}
static jboolean JNICALL hasException(JNIEnv* e){return pending;}
static void JNICALL clearException(JNIEnv* e){pending=0;}
static jint JNICALL detach(JavaVM* vm){return JNI_OK;}
static const struct JNINativeInterface_ table={
    .NewByteArray=makeArray,.NewGlobalRef=makeGlobal,.DeleteGlobalRef=dropGlobal,
    .DeleteLocalRef=dropLocal,.GetArrayLength=length,.SetByteArrayRegion=setBytes,
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
    printf("%d checks, %d failures\n",checks,errors);return errors!=0;
}
'''
helper = block('static jbyteArray createDecodedFrameBuffer(') if 'static jbyteArray createDecodedFrameBuffer(' in source else ''
code = prefix + helper + '\n'.join(block(m) for m in ['int BridgeDrSetup(', 'void BridgeDrCleanup(', 'int BridgeDrSubmitDecodeUnit(']) + suffix
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
