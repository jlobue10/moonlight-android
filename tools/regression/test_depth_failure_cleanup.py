#!/usr/bin/env python3
"""Actual failed-model cleanup/stop methods, with native close held across teardown."""
import pathlib,subprocess,tempfile,sys
ROOT=pathlib.Path(__file__).resolve().parents[2];path='app/src/main/java/com/limelight/utils/Stereo3DRenderer.java'
s=subprocess.check_output(['git','show','HEAD:'+path],cwd=ROOT,text=True) if '--baseline' in sys.argv else (ROOT/path).read_text()
def block(marker):
 start=s.index(marker);end=s.index('{',start);depth=1
 while depth:
  end+=1;depth+=(s[end]=='{')-(s[end]=='}')
 return s[start:end+1]
pre=r'''
import java.util.concurrent.*;
public class DepthFailureCleanup {
 final ExecutorService inferenceExecutor=Executors.newSingleThreadExecutor(),executorService=Executors.newSingleThreadExecutor();
 Future<?> inferenceTask;volatile boolean stopped;final Object depthReady=new Object();
 Object tflite=new Object();String backend="GPU";
 final CountDownLatch entered=new CountDownLatch(1),finish=new CountDownLatch(1),returned=new CountDownLatch(1);
 volatile boolean result,closed;boolean blockClose=true;
 static class LimeLog{static void warning(String s){}}
 void closeTfLite(){entered.countDown();if(blockClose){boolean done=false;while(!done){try{finish.await();done=true;}catch(InterruptedException ignored){}}}tflite=null;closed=true;}
 static int failures;
 static void check(boolean ok,String msg){System.out.println((ok?"PASS ":"FAIL ")+msg);if(!ok)failures++;}
'''
post=r'''
 public static void main(String[] args)throws Exception{
  DepthFailureCleanup d=new DepthFailureCleanup();Thread gl=new Thread(()->{d.result=d.discardModelAfterFailedInitialization();d.returned.countDown();});gl.start();
  if(!d.entered.await(5,TimeUnit.SECONDS))throw new AssertionError("close not entered");d.stop();
  boolean woke=d.returned.await(1,TimeUnit.SECONDS);check(woke&&!d.result&&!d.closed,"stop releases the GL waiter while native model close is still blocked");
  d.finish.countDown();gl.join(5000);d.inferenceExecutor.awaitTermination(5,TimeUnit.SECONDS);
  check(d.closed,"native cleanup still completes on the owner executor");
  DepthFailureCleanup retired=new DepthFailureCleanup();retired.blockClose=false;retired.stop();
  check(!retired.discardModelAfterFailedInitialization(),"retired sessions reject fallback cleanup");retired.inferenceExecutor.awaitTermination(5,TimeUnit.SECONDS);
  DepthFailureCleanup normal=new DepthFailureCleanup();normal.blockClose=false;
  check(normal.discardModelAfterFailedInitialization()&&normal.tflite==null&&normal.backend.equals("flat (model failed)"),"live session closes partial model and continues flat");normal.stop();normal.inferenceExecutor.awaitTermination(5,TimeUnit.SECONDS);
  if(failures>0)System.exit(1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='depth-close-') as d:
 p=pathlib.Path(d);(p/'DepthFailureCleanup.java').write_text(pre+block('        private synchronized void stop()')+block('        private boolean discardModelAfterFailedInitialization()')+post)
 subprocess.run(['java','com.sun.tools.javac.Main','-d',d,str(p/'DepthFailureCleanup.java')],check=True)
 raise SystemExit(subprocess.run(['java','-cp',d,'DepthFailureCleanup']).returncode)
