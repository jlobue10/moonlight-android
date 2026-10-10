#!/usr/bin/env python3
"""Execute the production draw method with fake pixels/GL and a deterministic clock.

Checks scheduling, input submissions, map upload ownership, and overlay units.
No assertion measures physical GPU/AI time. --baseline uses current HEAD source.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/utils/Stereo3DRenderer.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())


def method(marker):
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
public class StereoDemand {
 Object frameLock=new Object(); AtomicBoolean frameAvailable=new AtomicBoolean();
 DepthSession depthSession=new DepthSession(); boolean stopped,block,isMovieMode,isDebugMode,flatMapUploaded;
 ByteBuffer createFlatDepthMap(){return ByteBuffer.allocate(1);}
 String renderer; Host host=new Host(); Texture videoSurfaceTexture=new Texture();
 ByteBuffer previousFrameForComparison=ByteBuffer.allocate(4),currentlyRenderingMap;
 long lastFpsTime,totalDrawTime; float fps=60,calcFps,threeDFps,calcThreeDFps,drawDelay;
 int depthMapResultCount,readbacks,uploads,draws; static final long MOVIE_MODE_MAX_DEPTH_WAIT_NS=100_000_000L;
 static class System {static long now=1_000_000_000L;static boolean realClock;static long nanoTime(){if(realClock)return java.lang.System.nanoTime();long t=now;now+=1_000_000L;return t;}}
 static class GL10 {} static class Log {static void d(String a,String b){}static void w(String a,String b,Exception e){}}
 static class Build {static class VERSION {static final int SDK_INT=35;}static class VERSION_CODES {static final int N=24;}}
 static class GLES20 {static final int GL_COLOR_BUFFER_BIT=1;static void glClear(int i){}}
 static class Host {boolean continuous;int requests;void setContinuousRendering(boolean b){continuous=b;}void requestRender(){requests++;}}
 static class Texture {int updates;void updateTexImage(){updates++;}}
 static class RenderResult {ByteBuffer pixelBuffer;RenderResult(ByteBuffer b,double d){pixelBuffer=b;}RenderResult(ByteBuffer b){pixelBuffer=b;}}
 class DepthSession {
  String backend="CPU";Object tflite=new Object();boolean stopped;
  AtomicInteger completedDepthFrames=new AtomicInteger();
  AtomicReference<ByteBuffer> latestDepthMap=new AtomicReference<>();AtomicBoolean isAiRunning=new AtomicBoolean(true);
  ArrayBlockingQueue<ByteBuffer> freeInputBuffers=new ArrayBlockingQueue<>(10),freeSmoothedBuffers=new ArrayBlockingQueue<>(3);
  ArrayBlockingQueue<ByteBuffer> filledOutputBuffers=new ArrayBlockingQueue<>(6);
  ArrayBlockingQueue<RenderResult> inferenceInputQueue=new ArrayBlockingQueue<>(1);
  final Object depthReady=new Object(); WaitingFlag depthWaiting=new WaitingFlag();
  DEPTH_REQUEST_METHOD
  DEPTH_PUBLISH_METHOD
 }
 boolean readbackWorks=true;
 boolean readPixelsForAI_Async(ByteBuffer b){readbacks++;return readbackWorks;}
 boolean readPixelsForAI(ByteBuffer b){readbacks++;return readbackWorks;}
 double hasSceneChangedFast(ByteBuffer a,ByteBuffer b){return 10;}
 void uploadLatestDepthMapToGpu(ByteBuffer b){uploads++;}
 void applyTwoPassGaussianBlur(){}void drawWithShader(){draws++;}
 void recycle(){RenderResult r=depthSession.inferenceInputQueue.poll();if(r!=null)depthSession.freeInputBuffers.offer(r.pixelBuffer);}
 static class WaitingFlag {
  volatile boolean value;Runnable beforeClear;
  boolean get(){return value;}
  void set(boolean b){if(!b && beforeClear!=null)beforeClear.run();value=b;}
 }
 static int failures;
 static void check(boolean ok,String message){java.lang.System.out.println((ok?"PASS ":"FAIL ")+message);if(!ok)failures++;}
'''
SUFFIX = r'''
 public static void main(String[] args)throws Exception{
  StereoDemand r=new StereoDemand();r.depthSession.freeInputBuffers.add(ByteBuffer.allocate(4));
  for(int i=0;i<100;i++){r.onDrawFrame(null);r.recycle();}
  check(r.readbacks==0 && r.videoSurfaceTexture.updates==0 && !r.host.continuous,
        "100 redraws without a video frame perform no readback/AI submission and do not enable continuous rendering");

  r=new StereoDemand();r.depthSession.freeInputBuffers.add(ByteBuffer.allocate(4));
  r.frameAvailable.set(true);r.onDrawFrame(null);r.recycle();
  r.depthSession.latestDepthMap.set(ByteBuffer.allocate(1));r.onDrawFrame(null);r.recycle();
  r.onDrawFrame(null);r.recycle();
  check(r.readbacks==1 && r.videoSurfaceTexture.updates==1 && r.uploads==1,
        "one video frame and a late depth map cause one input submission and one depth upload");

  r=new StereoDemand();r.isMovieMode=true;r.depthSession.latestDepthMap.set(ByteBuffer.allocate(1));
  r.onDrawFrame(null);
  check(r.uploads==1 && r.readbacks==0 && r.draws==1,
        "synced mode consumes a late map without waiting for another video frame");

  r=new StereoDemand();r.frameAvailable.set(true);r.isMovieMode=true;
  r.depthSession.freeInputBuffers.add(ByteBuffer.allocate(4));r.depthSession.latestDepthMap.set(ByteBuffer.allocate(1));
  r.onDrawFrame(null);
  check(r.readbacks==1 && r.uploads==1 && r.draws==1,"synced mode still reads and draws a new video frame");

  r=new StereoDemand();r.lastFpsTime=1;System.now=1_000_000_000L;r.onDrawFrame(null);
  check(Math.abs(r.drawDelay-3.0f)<0.001f && r.fps>0.9f && r.fps<1.1f,
        "overlay averages actual draw duration in milliseconds and includes the reporting draw");

  // A new map arrives exactly when the draw withdraws from its synced wait.
  r=new StereoDemand();r.frameAvailable.set(true);r.isMovieMode=true;
  r.depthSession.freeInputBuffers.add(ByteBuffer.allocate(4));r.depthSession.latestDepthMap.set(ByteBuffer.allocate(1));
  final StereoDemand current=r;final Thread[] producer=new Thread[1];
  r.depthSession.depthWaiting.beforeClear=()->{
    producer[0]=new Thread(()->current.depthSession.publishDepthMap(ByteBuffer.allocate(1)));
    producer[0].start();
    if(!Thread.holdsLock(current.depthSession.depthReady)) {
      try {producer[0].join(1000);}catch(InterruptedException e){throw new RuntimeException(e);}
    }
  };
  r.onDrawFrame(null);producer[0].join(1000);
  check(!producer[0].isAlive() && r.depthSession.latestDepthMap.get()!=null && r.host.requests==1,
        "publication during waiter withdrawal always schedules the unconsumed map");

  System.realClock=true;
  for(int failure=0;failure<3;failure++) {
    r=new StereoDemand();r.frameAvailable.set(true);r.isMovieMode=true;r.depthSession.isAiRunning.set(true);
    if(failure!=0)r.depthSession.freeInputBuffers.add(ByteBuffer.allocate(4));
    if(failure==1)r.depthSession.inferenceInputQueue.add(new RenderResult(ByteBuffer.allocate(4)));
    if(failure==2)r.readbackWorks=false;
    long before=java.lang.System.nanoTime();r.onDrawFrame(null);
    check(java.lang.System.nanoTime()-before<70_000_000L,
          "synced draw does not wait 100 ms when no input was submitted (case "+failure+")");
  }
  System.realClock=false;
  DEPTH_REQUEST_TEST
  if(failures!=0)java.lang.System.exit(1);
 }
}
'''
request_marker = '        private void requestDepthRender()'
request = method(request_marker) if request_marker in source else ''
request_test = r'''
  r=new StereoDemand();r.depthSession.requestDepthRender();
  StereoDemand.DepthSession old=r.depthSession;r.depthSession=r.new DepthSession();old.requestDepthRender();
  r.depthSession.stopped=true;r.depthSession.requestDepthRender();
  check(r.host.requests==1,"only the current active depth session requests a redraw");
''' if request else ''
publish_marker = '        private void publishDepthMap('
if publish_marker in source:
    publish = method(publish_marker)
else:
    start = source.index('                            ByteBuffer superseded = latestDepthMap.getAndSet(resultBuffer);')
    end = source.index('                            previousPixelBuffer.rewind();', start)
    publish = 'private void publishDepthMap(ByteBuffer resultBuffer){' + source[start:end] + '}'
PREFIX = PREFIX.replace('DEPTH_PUBLISH_METHOD', publish)
with tempfile.TemporaryDirectory(prefix='stereo-demand-') as directory:
    work = pathlib.Path(directory)
    code = PREFIX.replace('DEPTH_REQUEST_METHOD', request) + method('    public void onDrawFrame(')
    if '    private void updatePerformanceStats(' in source:
        code += method('    private void updatePerformanceStats(')
    code += SUFFIX.replace('DEPTH_REQUEST_TEST', request_test)
    (work / 'StereoDemand.java').write_text(code)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(work / 'StereoDemand.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'StereoDemand']).returncode)
