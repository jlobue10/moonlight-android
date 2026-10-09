#!/usr/bin/env python3
"""Exercise production surface initialization/teardown with a stalled model.

GL/Android and the model constructor are boundary fakes. Surface lifecycle and
DepthSession.stop are copied verbatim. --baseline reads the checked-out HEAD.
"""
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/utils/Stereo3DRenderer.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())


def method(name):
    match = re.search(r'^ +(?:public|private) (?:synchronized )?void ' + name + r'\(', source, re.MULTILINE)
    start = match.start()
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


PREFIX = r'''
import java.nio.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
public class StereoInitialization {
 volatile DepthSession depthSession; volatile boolean stopped; volatile long glGeneration;
 AtomicBoolean frameAvailable=new AtomicBoolean();
 ByteBuffer currentlyRenderingMap,previousFrameForComparison;
 boolean block,isActive,blockFirst=true,flatMapUploaded;
 long lastFpsTime,totalDrawTime; float drawDelay,calcFps,calcThreeDFps,fps,threeDFps;
 String renderer; int modelInputWidth=1,modelInputHeight=1;
 int videoTextureId,depthMapTextureId,filteredDepthMapTextureId,fboTextureId,intermediateTextureId;
 int simple3dProgram,bilateralBlurProgram,dibr3dProgram,fboHandle,intermediateFboHandle,filterFboHandle;
 int[] pboHandles=new int[2];
 Surface videoSurface; SurfaceTexture videoSurfaceTexture; Prefs prefConfig;
 final Host host=new Host();
 final CountDownLatch entered=new CountDownLatch(1),finish=new CountDownLatch(1);
 final List<DepthSession> sessions=new ArrayList<>();
 int notifications,workers;
 Listener onSurfaceReadyListener=s -> notifications++;
 interface Listener {void onStereo3DSurfaceReady(Surface surface);}
 static class Prefs {int width,height;}
 static class GL10 {} static class EGLConfig {}
 static class Surface {Surface(SurfaceTexture texture){}void release(){}}
 static class SurfaceTexture {
  SurfaceTexture(int id){}void release(){}void setDefaultBufferSize(int w,int h){}
  void setOnFrameAvailableListener(Object listener){}
 }
 static class Host {void queueEvent(Runnable task){}}
 static class GLES20 {
  static void glDeleteProgram(int p){}static void glDeleteTextures(int n,int[] a,int o){}
  static void glDeleteFramebuffers(int n,int[] a,int o){}
 }
 static class GLES30 {static void glDeleteBuffers(int n,int[] a,int o){}}
 static class LimeLog {static void info(String s){}static void severe(String s){}}
 static class ShaderUtils {
  static String SIMPLE_VERTEX_SHADER="",SIMPLE_FRAGMENT_SHADER="",VERTEX_SHADER="";
  static String OPTIMIZED_SINGLE_PASS_GAUSSIAN_BLUR_SHADER="",FRAGMENT_SHADER_3D="";
 }
 int createExternalOESTexture(){return 1;}int createEmptyTexture(int w,int h){return 1;}
 int createProgram(String v,String f){return 1;}
 void initializeFilterFbo(){}void initializeIntermediateFbo(){}void initializeFbo(){}void initializePBOs(){}
 class DepthSession {
  final ExecutorService inferenceExecutor=Executors.newSingleThreadExecutor();
  final ExecutorService executorService=Executors.newSingleThreadExecutor();
  Future<?> inferenceTask;volatile boolean stopped,modelCreated,modelClosed;
  final int ordinal=sessions.size();
  String backend="CPU";int modelInputWidth=1,modelInputHeight=1;
  DepthSession(){sessions.add(this);}
  void initializeTfLite(){
   if(blockFirst && ordinal==0){entered.countDown();awaitNative(finish);}
   modelCreated=true;
  }
  void adoptTensorLayout(){}void initBuffer(){}
  void startWorkers(){if(!stopped)workers++;}
  void closeTfLite(){modelClosed=modelCreated;}
  // PRODUCTION_STOP
 }
 static void awaitNative(CountDownLatch latch){
  boolean interrupted=false;
  while(true){try{latch.await();break;}catch(InterruptedException e){interrupted=true;}}
  if(interrupted)Thread.currentThread().interrupt();
 }
'''
SUFFIX = r'''
 static int failures;
 static void check(boolean ok,String label){System.out.println((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 public static void main(String[] args)throws Exception {
  StereoInitialization s=new StereoInitialization();
  Thread gl=new Thread(()->s.onSurfaceCreated(null,null));
  CountDownLatch stopped=new CountDownLatch(1);
  Thread ui=new Thread(()->{try{s.onSurfaceDestroyed();}finally{stopped.countDown();}});
  gl.start();
  try {
   if(!s.entered.await(2,TimeUnit.SECONDS))throw new AssertionError("initialization never entered");
   ui.start();
   check(stopped.await(500,TimeUnit.MILLISECONDS),"teardown does not wait for a native model constructor");
   gl.join(500);
   check(!gl.isAlive(),"cancellation wakes the GL initialization waiter before native work returns");
  } finally {
   s.finish.countDown();gl.join(2000);ui.join(2000);
  }
  check(s.notifications==0 && s.workers==0,"stopped initialization cannot publish a surface or start workers");
  DepthSession old=s.sessions.get(0);
  check(old.inferenceExecutor.awaitTermination(2,TimeUnit.SECONDS) && old.modelClosed,
        "late-created model closes on the retired executor");

  StereoInitialization reentrant=new StereoInitialization();reentrant.blockFirst=false;
  reentrant.onSurfaceReadyListener=surface->reentrant.onSurfaceDestroyed();
  reentrant.onSurfaceCreated(null,null);
  check(!reentrant.isActive && reentrant.workers==0,
        "surface callback teardown is not followed by worker activation");
  reentrant.sessions.get(0).inferenceExecutor.awaitTermination(2,TimeUnit.SECONDS);
  System.exit(failures==0?0:1);
 }
}
'''

with tempfile.TemporaryDirectory(prefix='stereo-initialization-') as directory:
    work = pathlib.Path(directory)
    methods = [method('onSurfaceCreated'), method('onSurfaceDestroyed')]
    if 'void stopFailedInitialization(' in source:
        methods.append(method('stopFailedInitialization'))
    java = PREFIX.replace('// PRODUCTION_STOP', method('stop')) + '\n'.join(methods) + SUFFIX
    (work / 'StereoInitialization.java').write_text(java)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(work / 'StereoInitialization.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'StereoInitialization']).returncode)
