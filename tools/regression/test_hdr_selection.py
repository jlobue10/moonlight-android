#!/usr/bin/env python3
"""Compare the launch HDR decision with production RTSP codec selection.

Compiles the selection block from the pinned common-C submodule and the actual
Java launch decision. SDP advertises the codecs in serverinfo and a compatible
PyroWave revision. This does not model a server changing capabilities after launch.
--baseline reads NvConnection from HEAD.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/nvstream/NvConnection.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())
common = ROOT / 'app/src/main/jni/moonlight-core/moonlight-common-c/src'
native = (common / 'RtspConnection.c').read_text()
selection = native[native.index('        int pyroWaveFormat ='):native.index('        // Look for the SDP attribute')]
bridge = (ROOT / 'app/src/main/java/com/limelight/nvstream/jni/MoonBridge.java').read_text()
constants = '\n'.join(re.findall(r'public static final int VIDEO_FORMAT_[A-Z_0-9]+\s*=.*?;', bridge, re.S))
if 'private static boolean shouldRequestHdr(' in source:
    start = source.index('private static boolean shouldRequestHdr(')
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    decision = source[start:end + 1]
else:
    start = source.index('        int formats = context.streamConfig.getSupportedVideoFormats();', source.index('private boolean startApp('))
    end = source.index('        if (wantedHdr', start)
    body = source[start:end].replace('int formats = context.streamConfig.getSupportedVideoFormats();', '')
    body = body.replace('int scm = context.serverCodecModeSupport;', '')
    body = body.replace('context.negotiatedHdr =', 'return')
    decision = 'private static boolean shouldRequestHdr(int formats,int scm){' + body + '}'

c_code = r'''
#include <stdio.h>
#include <string.h>
#include "VideoFormat.h"
#define Limelog(...) ((void)0)
static int choose(int formats, int modes) {
 STREAM_CONFIGURATION StreamConfig={.supportedVideoFormats=formats,.width=1920,.height=1080};
 SERVER_INFORMATION info={.serverCodecModeSupport=modes};SERVER_INFORMATION *serverInfo=&info;
 char sdp[512];
 snprintf(sdp,sizeof(sdp),"%s%s%s",
  modes&SCM_MASK_AV1?"a=rtpmap:98 AV1/90000\r\n":"",
  modes&SCM_MASK_HEVC?"a=fmtp:97 sprop-parameter-sets=AAAAAU\r\n":"",
  modes&SCM_PYROWAVE?"a=rtpmap:99 PYROWAVE/90000\r\na=x-ss-pyrowave.bitstream:" LI_PYROWAVE_BITSTREAM_ID "\r\n":"");
 struct {char *payload;int payloadLength;} response={sdp,(int)strlen(sdp)};
 int NegotiatedVideoFormat=0;
 NATIVE_SELECTION
 return NegotiatedVideoFormat;
}
int main(void){
 int ch[]={0,VIDEO_FORMAT_H265,VIDEO_FORMAT_H265|VIDEO_FORMAT_H265_MAIN10};
 int ca[]={0,VIDEO_FORMAT_AV1_MAIN8,VIDEO_FORMAT_AV1_MAIN8|VIDEO_FORMAT_AV1_MAIN10};
 int cp[]={0,VIDEO_FORMAT_PYROWAVE,VIDEO_FORMAT_PYROWAVE|VIDEO_FORMAT_PYROWAVE_HDR10,
  VIDEO_FORMAT_PYROWAVE|VIDEO_FORMAT_PYROWAVE_444,VIDEO_FORMAT_MASK_PYROWAVE};
 int sh[]={0,SCM_HEVC,SCM_HEVC|SCM_HEVC_MAIN10};
 int sa[]={0,SCM_AV1_MAIN8,SCM_AV1_MAIN8|SCM_AV1_MAIN10};
 int sp[]={0,SCM_PYROWAVE,SCM_PYROWAVE|SCM_PYROWAVE_HDR10,
  SCM_PYROWAVE|SCM_PYROWAVE_444,SCM_MASK_PYROWAVE};
 for(int h=0;h<3;h++)for(int a=0;a<3;a++)for(int p=0;p<5;p++)
 for(int x=0;x<3;x++)for(int y=0;y<3;y++)for(int z=0;z<5;z++){
  int formats=VIDEO_FORMAT_H264|ch[h]|ca[a]|cp[p],modes=SCM_H264|sh[x]|sa[y]|sp[z];
  printf("%d %d %d\n",formats,modes,choose(formats,modes));
 }
}
'''.replace('NATIVE_SELECTION', selection)
java_code = r'''
import java.nio.file.*;
public class HdrSelectionRegression {
 static class MoonBridge { CONSTANTS }
 DECISION
 public static void main(String[] args)throws Exception {
  int errors=0,count=0;
  for(String line:Files.readAllLines(Path.of(args[0]))){
   String[] s=line.split(" ");int f=Integer.parseInt(s[0]),m=Integer.parseInt(s[1]),selected=Integer.parseInt(s[2]);
   boolean actual=shouldRequestHdr(f,m),expected=(selected&MoonBridge.VIDEO_FORMAT_MASK_10BIT)!=0;
   ++count;
   if(actual!=expected){if(errors<10)System.out.printf("FAIL client=%x server=%x RTSP=%x launchHDR=%s%n",f,m,selected,actual);++errors;}
  }
  System.out.println(count+" codec combinations; "+errors+" HDR mismatches");if(errors!=0)System.exit(1);
 }
}
'''.replace('CONSTANTS', constants).replace('DECISION', decision)
with tempfile.TemporaryDirectory(prefix='hdr-selection-') as directory:
    work = Path(directory)
    (work/'select.c').write_text(c_code)
    (work/'HdrSelectionRegression.java').write_text(java_code)
    subprocess.run(['cc','-std=c11','-I'+str(common),str(work/'select.c'),'-o',str(work/'select')],check=True)
    (work/'matrix.txt').write_bytes(subprocess.check_output([str(work/'select')]))
    subprocess.run(['java','com.sun.tools.javac.Main',str(work/'HdrSelectionRegression.java')],check=True)
    raise SystemExit(subprocess.run(['java','-cp',directory,'HdrSelectionRegression',str(work/'matrix.txt')]).returncode)
