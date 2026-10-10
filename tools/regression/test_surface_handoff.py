#!/usr/bin/env python3
"""Execute the surface registration/readiness/destruction methods with a UI queue.

The callback-entry latch controls the GL/main interleaving; no Android UI is used.
"""
import pathlib, subprocess, sys, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
PATH='app/src/main/java/com/limelight/ui/StreamContainer.java'
source=(subprocess.check_output(['git','show','HEAD:'+PATH],cwd=ROOT,text=True)
        if '--baseline' in sys.argv else (ROOT/PATH).read_text())
def method(marker):
 start=source.index(marker);end=source.index('{',start);depth=1
 while depth:
  end+=1;depth+=(source[end]=='{')-(source[end]=='}')
 return source[start:end+1]
prefix=r'''
import java.util.concurrent.*;import java.util.concurrent.atomic.*;
public class SurfaceHandoff {
 final Object surfaceReadyLock=new Object();
 volatile boolean destroyed;boolean isSurfaceReady,xrStereo;
 Runnable onSurfaceAvailable;Surface mCurrentSurface;
 enum StreamMode{MODE_2D,MODE_AI_3D}StreamMode renderMode=StreamMode.MODE_AI_3D;
 static class Surface{}
 static class Resource{void onSurfaceDestroyed(){}void shutdown(){}void stop(){}}
 Resource mStereoRenderer,xrGlThread,xrTestPattern,xrPresenter;
 final ConcurrentLinkedQueue<Runnable> mainQueue=new ConcurrentLinkedQueue<>();
 void post(Runnable task){mainQueue.add(task);}
 void drain(){Runnable r;while((r=mainQueue.poll())!=null)r.run();}
 static int failures;
 static void check(boolean ok,String name){System.out.println((ok?"PASS ":"FAIL ")+name);if(!ok)++failures;}
'''
suffix=r'''
 public static void main(String[] args)throws Exception{
  for(boolean readyFirst:new boolean[]{false,true}){
   SurfaceHandoff c=new SurfaceHandoff();AtomicInteger calls=new AtomicInteger();
   if(readyFirst)c.onStereo3DSurfaceReady(new Surface());
   c.setOnSurfaceAvailable(calls::incrementAndGet);
   if(!readyFirst)c.onStereo3DSurfaceReady(new Surface());
   c.drain();check(calls.get()==1,"surface handoff is delivered once; readyFirst="+readyFirst);
  }
  SurfaceHandoff repeated=new SurfaceHandoff();AtomicInteger repeatedCalls=new AtomicInteger();
  repeated.setOnSurfaceAvailable(repeatedCalls::incrementAndGet);
  repeated.onStereo3DSurfaceReady(new Surface());repeated.onStereo3DSurfaceReady(new Surface());
  repeated.drain();check(repeatedCalls.get()==1,"duplicate readiness cannot invoke one registration twice");
  SurfaceHandoff c=new SurfaceHandoff();AtomicInteger starts=new AtomicInteger();
  CountDownLatch entered=new CountDownLatch(1),resume=new CountDownLatch(1);
  Thread main=Thread.currentThread();
  c.setOnSurfaceAvailable(()->{
   if(Thread.currentThread()!=main){entered.countDown();try{resume.await();}catch(InterruptedException e){throw new RuntimeException(e);}}
   starts.incrementAndGet();
  });
  Thread gl=new Thread(()->c.onStereo3DSurfaceReady(new Surface()));gl.start();
  // The old implementation enters on GL; the corrected implementation queues on UI.
  entered.await(100,TimeUnit.MILLISECONDS);
  c.onDestroy();resume.countDown();gl.join();c.drain();
  check(starts.get()==0 && c.getSurface()==null,"GL readiness racing destruction never starts a dead session");
  c.setOnSurfaceAvailable(starts::incrementAndGet);c.onStereo3DSurfaceReady(new Surface());c.drain();
  check(starts.get()==0 && c.getSurface()==null,"late registration and readiness after destruction are ignored");
  if(failures!=0)System.exit(1);
 }
}
'''
markers=['    public void setOnSurfaceAvailable(', '    private void notifySurfaceReady(',
         '    public Surface getSurface()', '    public void onStereo3DSurfaceReady(', '    public void onDestroy()']
if '    private void dispatchSurfaceReady()' in source:markers.append('    private void dispatchSurfaceReady()')
with tempfile.TemporaryDirectory(prefix='surface-handoff-') as directory:
 work=pathlib.Path(directory);(work/'SurfaceHandoff.java').write_text(prefix+'\n'.join(method(m) for m in markers)+suffix)
 subprocess.run(['java','com.sun.tools.javac.Main','-d',str(work),str(work/'SurfaceHandoff.java')],check=True)
 raise SystemExit(subprocess.run(['java','-cp',str(work),'SurfaceHandoff']).returncode)
