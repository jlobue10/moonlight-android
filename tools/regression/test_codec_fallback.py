#!/usr/bin/env python3
"""Run the production low-latency option builder and retry loop with fake codecs.

No Android codec is loaded. A rejecting codec models configure() failure; the
guard fails the test after 12 attempts instead of allowing a broken loop to hang.
--baseline reads production sources from HEAD.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]

def read(name):
    path = 'app/src/main/java/com/limelight/binding/video/' + name + '.java'
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
            if '--baseline' in sys.argv else (ROOT / path).read_text())

def block(source, marker):
    # Ignore braces in comments and quoted text while finding the method boundary.
    tokens = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/'
    scan = re.sub(tokens, lambda m: ' ' * len(m.group()), source)
    start = source.index(marker)
    end = scan.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (scan[end] == '{') - (scan[end] == '}')
    return source[start:end + 1]

helper = read('MediaCodecHelper')
renderer = read('MediaCodecDecoderRenderer')
options = block(helper, 'public static boolean setDecoderLowLatencyOptions(')
retry = block(renderer, 'for (int tryNumber = 0;; tryNumber++)')
prefix = r'''
import java.util.*;
public class CodecFallbackRegression {
 static class Build {
  static String MANUFACTURER="test";
  static class VERSION {static int SDK_INT=33;}
  static class VERSION_CODES {static final int M=23,O=26;}
 }
 static class LimeLog {static void info(String s) {}}
 static class MediaFormat {
  static final String KEY_MIME="mime",KEY_OPERATING_RATE="operating-rate",KEY_PRIORITY="priority";
  final Map<String,Integer> keys=new HashMap<>();
  void setInteger(String k,int v){keys.put(k,v);}
  String getString(String k){return "video/hevc";}
 }
 static class MediaCodecInfo {
  final String name;boolean official;
  MediaCodecInfo(String name){this.name=name;}
  String getName(){return name;}
 }
 static class MediaCodecHelper {
  static final List<String> tegraDecoderPrefixes=List.of("omx.nvidia"),
   qualcommDecoderPrefixes=List.of("omx.qcom","c2.qti"),
   mtkDecoderPrefixes=List.of("omx.mtk","c2.mtk"),kirinDecoderPrefixes=List.of("omx.hisi"),
   exynosDecoderPrefixes=List.of("omx.exynos"),amlogicDecoderPrefixes=List.of("omx.amlogic","c2.amlogic");
  static boolean initialized=true;
  static final String AMLOGIC_C2_HEVC_DECODER_PREFIX="c2.amlogic.hevc.decoder";
  static boolean decoderSupportsAndroidRLowLatency(MediaCodecInfo info,String mime){return info.official;}
  static boolean decoderSupportsMaxOperatingRate(String name){return false;}
  static void safeSet(MediaFormat f,String k,int v){f.setInteger(k,v);}
'''
suffix = r'''
 }
 static class Prefs {boolean enableUltraLowLatency;}
 Prefs prefs=new Prefs();MediaCodecInfo selectedDecoderInfo;String mimeType="video/hevc";
 int attempts,failures;boolean rejectAll,rejectNvidia,throwOnCodecError;
 MediaFormat createBaseMediaFormat(String mime){return new MediaFormat();}
 boolean tryConfigureDecoder(MediaCodecInfo info,MediaFormat f,boolean last){
  if(++attempts>12)throw new AssertionError("retry budget exceeded");
  return !rejectAll && !(rejectNvidia && f.keys.containsKey("vendor.nvidia.disable-output-reorder"));
 }
 int initialize(){ RETRY_LOOP return 0; }
 static int errors,checks;
 static void check(boolean ok,String name){checks++;if(!ok)errors++;System.out.println((ok?"PASS ":"FAIL ")+name);}
 public static void main(String[] args){
  for(String name:List.of("omx.mtk.video.decoder.hevc","c2.mtk.hevc.decoder","omx.nvidia.h265.decode",
       "omx.qcom.video.decoder.hevc","c2.qti.hevc.decoder","omx.hisi.video.decoder.hevc",
       "omx.exynos.hevc.decoder","omx.amlogic.avc.decoder","c2.amlogic.hevc.decoder","generic.decoder")){
   for(boolean official:new boolean[]{false,true}){
    CodecFallbackRegression r=new CodecFallbackRegression();r.selectedDecoderInfo=new MediaCodecInfo(name);
    r.selectedDecoderInfo.official=official;r.rejectAll=true;
    boolean stopped=false;
    try{stopped=r.initialize()==-5&&r.attempts<=6;}catch(AssertionError ignored){}
    check(stopped,name+" permanent configure failure terminates (official="+official+")");
   }
  }
  CodecFallbackRegression r=new CodecFallbackRegression();r.selectedDecoderInfo=new MediaCodecInfo("omx.nvidia.h265.decode");
  r.rejectNvidia=true;
  check(r.initialize()==0&&r.attempts>1,"NVIDIA rejection falls back without vendor options");
  r=new CodecFallbackRegression();r.selectedDecoderInfo=new MediaCodecInfo("c2.mtk.hevc.decoder");
  check(r.initialize()==0&&r.attempts==1,"successful codec keeps the first low-latency attempt");
  System.out.println(checks+" checks; "+errors+" failures");if(errors!=0)System.exit(1);
 }
}
'''.replace('RETRY_LOOP', retry)

code = prefix + '\n'.join(block(helper, m) for m in [
    'private static boolean isDecoderInList(', 'public static boolean isNvidiaDecoder(',
    'private static boolean isAmlogicC2HevcDecoder(']) + options + suffix
with tempfile.TemporaryDirectory(prefix='codec-fallback-') as directory:
    path = Path(directory) / 'CodecFallbackRegression.java'
    path.write_text(code)
    subprocess.run(['java', 'com.sun.tools.javac.Main', str(path)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', directory, 'CodecFallbackRegression'], timeout=20).returncode)
