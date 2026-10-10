#!/usr/bin/env python3
"""Run the actual inference worker against deterministic frame sequences.

Only model/pixel conversion and Android logging are faked. This checks cache
invalidation and buffer ownership, not model quality or native performance.
--baseline uses the committed renderer, permitting a before/after comparison.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/utils/Stereo3DRenderer.java'
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


PREFIX = r'''
import java.nio.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
public class StereoSceneCache {
 boolean stopped, floatInput, floatOutput;
 int modelInputWidth=1, modelInputHeight=1, calcThreeDFps;
 float ON_DRAW_CHANGE_TRESHOLD=2;
 Boolean isDebugMode=false;
 Object gpuDelegate,nnApiDelegate;
 AtomicBoolean isAiRunning=new AtomicBoolean(true); final Object depthReady=new Object();
 AtomicInteger completedDepthFrames=new AtomicInteger();
 BlockingQueue<RenderResult> inferenceInputQueue=new ArrayBlockingQueue<>(1);
 BlockingQueue<InferenceResult> filledOutputBuffers=new ArrayBlockingQueue<>(6);
 BlockingQueue<ByteBuffer> freeInputBuffers=new ArrayBlockingQueue<>(10);
 BlockingQueue<ByteBuffer> freeOutputBuffers=new ArrayBlockingQueue<>(6);
 ByteBuffer tfliteInputBuffer=ByteBuffer.allocateDirect(3);
 enum DepthModel {MIDAS_V2_256, OTHER}
 DepthModel depthModel=DepthModel.OTHER;
 Interpreter tflite=new Interpreter();
 static class Interpreter {
  int calls;
  void run(ByteBuffer in, ByteBuffer out) {calls++;out.put(0,in.get(0));}
 }
 static void convertRgbaToRgb(ByteBuffer a,ByteBuffer b,int w,int h){b.put(0,a.get(0));}
 static void convertRgbaToFloatRgb(ByteBuffer a,ByteBuffer b,int w,int h){throw new AssertionError();}
 void reinitializeTfLiteOnCpu(){throw new AssertionError("unexpected model error");}
 static class ReflectivePaddingInt8Minimal {static void applyReflectedPadding(ByteBuffer b){}}
 static class LimeLog {static void severe(String s){System.err.println(s);}}
 static class Log {static void d(String a,String b){}}
 static int failures;
 static void check(boolean ok,String label){System.out.println((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 int feed(int red, double previousFrameDifference)throws Exception {
  ByteBuffer input=freeInputBuffers.poll();
  if(input==null)input=ByteBuffer.allocateDirect(4);
  input.clear();input.put(0,(byte)red);
  inferenceInputQueue.put(makeResult(input,previousFrameDifference));
  InferenceResult result=filledOutputBuffers.poll(2,TimeUnit.SECONDS);
  if(result==null)throw new AssertionError("inference worker stalled");
  int value=result.rawDepthBuffer.get(0)&255;
  freeInputBuffers.add(result.pixelBuffer);
  freeOutputBuffers.add(result.rawDepthBuffer);
  return value;
 }
 static void finish(StereoSceneCache s,Thread worker)throws Exception {
  s.stopped=true;worker.interrupt();worker.join(2000);
  check(!worker.isAlive() && s.freeInputBuffers.size()==1 && s.freeOutputBuffers.size()==1,
        "cache processing returns all owned buffers and stops");
 }
 public static void main(String[] args)throws Exception {
  StereoSceneCache slow=new StereoSceneCache();slow.freeOutputBuffers.add(ByteBuffer.allocateDirect(1));
  Thread worker=new Thread(slow.new AiTask());worker.start();
  try {
   int depth=slow.feed(0,0);
   for(int red=1;red<=200;red++)depth=slow.feed(red,1);
   check(slow.tflite.calls>=67 && 200-depth<=2,
         "200 gradual changes refresh accumulated scene movement (calls="+slow.tflite.calls+", depth="+depth+")");
   int calls=slow.tflite.calls;
   for(int i=0;i<100;i++)slow.feed(200,0);
   check(slow.tflite.calls==calls,"identical frames retain the inference-saving cache");
   // The GL producer already sampled this image during a busy queue, so its
   // previous-frame difference is zero even though the last inferred frame differs.
   depth=slow.feed(240,0);
   check(depth==240 && slow.tflite.calls==calls+1,
         "a producer-side skipped frame cannot suppress a changed accepted frame");
  } finally {finish(slow,worker);}
  System.exit(failures==0?0:1);
 }
'''
result = block('    private static class RenderResult')
factory = ('return new RenderResult(b,d);' if 'double imageDifference' in result
           else 'return new RenderResult(b);')
sampling_marker = ('    private static double hasSceneChangedFast('
                   if 'private static double hasSceneChangedFast(' in source
                   else '    private double hasSceneChangedFast(')
java = (PREFIX + '\nstatic RenderResult makeResult(ByteBuffer b,double d){' + factory + '}\n'
        + result + block('    private static class InferenceResult')
        + block(sampling_marker) + block('    private class AiTask') + '\n}')
with tempfile.TemporaryDirectory(prefix='stereo-scene-cache-') as directory:
    work = pathlib.Path(directory)
    (work / 'StereoSceneCache.java').write_text(java)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(work / 'StereoSceneCache.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'StereoSceneCache']).returncode)
