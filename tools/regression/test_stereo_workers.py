#!/usr/bin/env python3
"""Exercise production stereo workers with fake inference/image operations.

Copies the workers verbatim into a host Java harness; image quality, delegate
performance, GL and model compatibility require a device. --baseline uses HEAD.
"""
import pathlib
import subprocess
import sys
import tempfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
path = 'app/src/main/java/com/limelight/utils/Stereo3DRenderer.java'
source = (subprocess.check_output(['git','show','HEAD:'+path],cwd=ROOT,text=True)
          if '--baseline' in sys.argv else (ROOT/path).read_text())

def block(marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end+1]

PREFIX = r'''
import java.nio.*; import java.io.*; import java.util.concurrent.*; import java.util.concurrent.atomic.*;
public class StereoWorkers {
 int depthRequests; void requestDepthRender(){depthRequests++;}
 final Object depthReady=new Object(); AtomicBoolean depthWaiting=new AtomicBoolean();
 boolean stopped=false,floatInput=false,floatOutput=false;
 int modelInputWidth=1,modelInputHeight=1,calcThreeDFps=0;float threeDFps=60;float ON_DRAW_CHANGE_TRESHOLD=2;
 Boolean isDebugMode=false; String renderer="CPU",backend="CPU"; DepthModel depthModel=DepthModel.MIDAS_V2_256;
 AtomicBoolean isAiRunning=new AtomicBoolean(true),isAiResultHandlingRunning=new AtomicBoolean(true),gpuDelegateFailed=new AtomicBoolean();
 AtomicInteger completedDepthFrames=new AtomicInteger();
 AtomicReference<ByteBuffer> latestDepthMap=new AtomicReference<>();
 BlockingQueue<ByteBuffer> freeInputBuffers=new ArrayBlockingQueue<>(10),freeOutputBuffers=new ArrayBlockingQueue<>(6),freeSmoothedBuffers=new ArrayBlockingQueue<>(3);
 BlockingQueue<RenderResult> inferenceInputQueue=new ArrayBlockingQueue<>(1);
 BlockingQueue<InferenceResult> filledOutputBuffers=new ArrayBlockingQueue<>(6);
 ByteBuffer previousPixelBuffer=ByteBuffer.allocate(4),tfliteInputBuffer=ByteBuffer.allocate(3);
 Interpreter tflite;GpuDelegate gpuDelegate;NnApiDelegate nnApiDelegate;
 static class RenderResult{ByteBuffer pixelBuffer;double imageDifference=10;RenderResult(ByteBuffer b){pixelBuffer=b;}}
 static class InferenceResult{ByteBuffer pixelBuffer,rawDepthBuffer;InferenceResult(ByteBuffer p,ByteBuffer r){pixelBuffer=p;rawDepthBuffer=r;}}
 enum DepthModel {MIDAS_V2_256; String shortName(){return "test";}}
 static class Interpreter{
  static class Options{Options addDelegate(Object d){return this;}Options setNumThreads(int n){return this;}void setUseNNAPI(boolean b){}}
  Interpreter(MappedByteBuffer m,Options o){} void run(Object i,Object o){throw new IllegalStateException("inference failed");}void close(){}
 }
 static class GpuDelegate{static class Options{void setQuantizedModelsAllowed(boolean b){}void setPrecisionLossAllowed(boolean b){}void setInferencePreference(int i){}}
 GpuDelegate(Options o){throw new IllegalStateException("GPU construction failed");}void close(){} }
 static class GpuDelegateFactory{static class Options{static final int INFERENCE_PREFERENCE_SUSTAINED_SPEED=1;}}
 static class NnApiDelegate{NnApiDelegate(){throw new IllegalStateException("NNAPI construction failed");}void close(){}}
 MappedByteBuffer loadModelFile()throws IOException{return null;}
 ByteBuffer createFlatDepthMap(){return ByteBuffer.allocate(1);}
 void convertRgbaToFloatRgb(ByteBuffer a,ByteBuffer b,int w,int h){}void convertRgbaToRgb(ByteBuffer a,ByteBuffer b,int w,int h){}
 static class DepthFrameDifference implements AutoCloseable {DepthFrameDifference(int w,int h){}double compare(ByteBuffer a,ByteBuffer b){return 1;}public void close(){}}
 double hasFrameChangedSignificantlyOCV(ByteBuffer a,ByteBuffer b){return 1;}
 static class ReflectivePaddingInt8Minimal{static void applyReflectedPadding(ByteBuffer b){}}
 static class LimeLog{static void severe(String s){}static void info(String s){}}
 static class Log{static void d(String a,String b){}}
 static class CvType{static final int CV_32FC1=1,CV_8UC1=2,CV_8U=2;}
 static class Mat{Mat(){}Mat(int a,int b,int c,ByteBuffer d){}public Mat clone(){return new Mat();}void release(){}void copyTo(Mat m,Mat mask){}void get(int a,int b,byte[] out){out[0]=42;}}
 static class Core{static final int NORM_MINMAX=1;static class MinMaxLocResult{double maxVal=1;}
 static void normalize(Mat a,Mat b,int c,int d,int e,int f){}static void absdiff(Mat a,Mat b,Mat c){}
 static MinMaxLocResult minMaxLoc(Mat a){return new MinMaxLocResult();}static void addWeighted(Mat a,double b,Mat c,double d,double e,Mat f){}static void bitwise_not(Mat a,Mat b){}}
 static class Imgproc{static final int THRESH_BINARY_INV=1;static void threshold(Mat a,Mat b,double c,int d,int e){}}
'''
SUFFIX = r'''
 static int failures=0;
 static void check(boolean ok,String name){System.out.println((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
 static Thread start(Runnable r){Thread t=new Thread(r);t.setDaemon(true);t.start();return t;}
 public static void main(String[] args)throws Exception{
  StereoWorkers s=new StereoWorkers();boolean initialized;
  try{s.initializeTfLite();initialized=s.tflite!=null && s.renderer.startsWith("CPU") && s.backend.startsWith("CPU");}catch(Exception e){initialized=false;}
  check(initialized,"GPU/NNAPI constructor failures reach a fresh CPU interpreter");
  s=new StereoWorkers();s.tflite=new Interpreter(null,new Interpreter.Options());
  ByteBuffer output=ByteBuffer.allocate(1);s.freeOutputBuffers.add(output);s.inferenceInputQueue.add(new RenderResult(ByteBuffer.allocate(4)));
  Thread worker=start(s.new AiTask());worker.join(200);worker.interrupt();worker.join(200);
  check(s.freeOutputBuffers.size()==1 && s.freeInputBuffers.size()==1,"failed inference returns both owned buffers");
  s=new StereoWorkers();ByteBuffer smooth=ByteBuffer.allocate(1);s.freeSmoothedBuffers.add(smooth);
  s.filledOutputBuffers.add(new InferenceResult(ByteBuffer.allocate(4),ByteBuffer.allocate(1)));
  worker=start(s.new AiResultHandling());long deadline=System.nanoTime()+1_000_000_000L;
  while(s.freeInputBuffers.isEmpty() && System.nanoTime()<deadline)Thread.yield();
  check(s.latestDepthMap.get()==smooth && s.freeSmoothedBuffers.isEmpty(),"published depth map remains exclusively owned by the consumer");
  check(s.depthRequests==1,"publishing a depth map requests a render");
  deadline=System.nanoTime()+1_000_000_000L;
  while(worker.getState()!=Thread.State.WAITING && System.nanoTime()<deadline)Thread.yield();
  worker.interrupt();worker.join(200);
  check(!worker.isAlive() && !s.isAiResultHandlingRunning.get() && s.freeInputBuffers.size()==1,
        "interruption stops the result worker without re-releasing its last buffers");
  System.exit(failures==0?0:1);
 }
}
'''
markers=['    private void initializeTfLite()', '    private void reinitializeTfLiteOnCpu()',
         '    private class AiTask', '    private class AiResultHandling']
if '    private static double hasSceneChangedFast(' in source:
    markers.insert(0, '    private static double hasSceneChangedFast(')
if '    private void closeTfLite()' in source: markers.insert(0,'    private void closeTfLite()')
if '    private static void closeModel(' in source: markers.insert(0,'    private static void closeModel(')
if '    private static void closeTfLite(' in source: markers.insert(0,'    private static void closeTfLite(')
if '    private void publishDepthMap(' in source: markers.insert(0,'    private void publishDepthMap(')
with tempfile.TemporaryDirectory(prefix='stereo-workers-') as directory:
    work=pathlib.Path(directory)
    (work/'StereoWorkers.java').write_text(PREFIX+'\n'.join(map(block,markers))+SUFFIX)
    subprocess.run(['java','com.sun.tools.javac.Main','-d',str(work),str(work/'StereoWorkers.java')],check=True)
    raise SystemExit(subprocess.run(['java','-cp',str(work),'StereoWorkers']).returncode)
