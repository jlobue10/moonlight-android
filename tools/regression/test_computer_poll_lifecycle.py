#!/usr/bin/env python3
"""Run production app-list polling and DB reference methods on the JVM.

HTTP, cache and DB are boundary fakes. Real threads and latches retire workers
while HTTP/parsing is blocked. --baseline extracts the starting HEAD methods.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/computers/ComputerManagerService.java'
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


java = r'''
import java.io.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

public class ComputerManagerService {
 static final int APPLIST_POLLING_PERIOD_MS=1000000,APPLIST_FAILED_POLLING_RETRY_MS=1000000,EMPTY_LIST_THRESHOLD=3;
 final List<PollingTuple> pollingTuples=new ArrayList<>();
 volatile ComputerManagerListener listener;
 final IdentityManager idManager=new IdentityManager();
 final RefCount dbRefCount=new RefCount(); final Database dbManager=new Database();
 final AtomicInteger activePolls=new AtomicInteger();
 File getCacheDir(){return new File(".");}
 @@ACQUIRE@@
 @@RELEASE@@
 @@WAIT@@
 @@POLLER@@
 static int checks,failures;
 static void check(boolean ok,String text){++checks;if(!ok)++failures;System.out.println((ok?"PASS ":"FAIL ")+text);}
 static void await(CountDownLatch latch)throws Exception{if(!latch.await(3,TimeUnit.SECONDS))throw new AssertionError("boundary timeout");}
 static void waitIgnoringInterrupt(CountDownLatch latch){for(;;){try{latch.await();Thread.interrupted();return;}catch(InterruptedException ignored){}}}
 static void join(Thread t)throws Exception{t.join(3000);if(t.isAlive())throw new AssertionError("worker did not retire");}
 static void retire(Thread t)throws Exception{long limit=System.nanoTime()+3_000_000_000L;while(t.isAlive()&&System.nanoTime()<limit){t.interrupt();t.join(20);}join(t);}
 static Thread worker(ApplistPoller p)throws Exception{var field=p.getClass().getDeclaredField("thread");field.setAccessible(true);return (Thread)field.get(p);}
 static void awaitBlocked(Thread t)throws Exception{long limit=System.nanoTime()+3_000_000_000L;while(t.isAlive()&&t.getState()!=Thread.State.BLOCKED&&System.nanoTime()<limit)Thread.yield();if(t.getState()!=Thread.State.BLOCKED)throw new AssertionError("worker did not reach monitor");}
 static void awaitIdleOrExit(Thread t)throws Exception{long limit=System.nanoTime()+3_000_000_000L;while(t.isAlive()&&t.getState()!=Thread.State.TIMED_WAITING&&System.nanoTime()<limit)Thread.yield();if(t.isAlive()&&t.getState()!=Thread.State.TIMED_WAITING)throw new AssertionError("worker did not finish publication");}
 static class Fixture {
  final ComputerManagerService service=new ComputerManagerService();
  final ComputerDetails pc=new ComputerDetails();final PollingTuple tuple=new PollingTuple(pc);
  final ApplistPoller poller=service.new ApplistPoller(pc);final AtomicInteger notices=new AtomicInteger();
  final CountDownLatch currentPublished=new CountDownLatch(1);
  Fixture(){CacheHelper.writes.clear();NvHTTP.requests.set(0);NvHTTP.plans.clear();NvHTTP.parsed.clear();service.pollingTuples.add(tuple);
   service.listener=c->{notices.incrementAndGet();if("current".equals(c.rawAppList))currentPublished.countDown();};}
 }
 public static void main(String[] args)throws Exception {
  {Fixture f=new Fixture();Response old=new Response("old");NvHTTP.plans.add(old);f.poller.start();Thread t=worker(f.poller);await(old.entered);
   f.poller.stop();old.release.countDown();awaitIdleOrExit(t);
   check(f.pc.rawAppList==null&&CacheHelper.writes.isEmpty()&&f.notices.get()==0,"a response returned after stop cannot publish or cache an app list");
   check(!t.isAlive(),"a retired HTTP worker exits without waiting another poll interval");t.interrupt();join(t);}
  {Fixture f=new Fixture();Response old=new Response("old");old.release.countDown();old.pauseParse=true;NvHTTP.plans.add(old);
   f.poller.start();Thread first=worker(f.poller);await(old.parseEntered);f.poller.stop();
   Response current=new Response("current");current.release.countDown();NvHTTP.plans.add(current);f.poller.start();Thread second=worker(f.poller);await(f.currentPublished);
   old.parseRelease.countDown();awaitIdleOrExit(first);
   check("current".equals(f.pc.rawAppList)&&CacheHelper.writes.equals(List.of("current")),"a retired parser cannot overwrite the replacement worker's cache or state");
   check(f.notices.get()==1,"only the current worker notifies the app view");
   f.poller.stop();first.interrupt();join(first);join(second);}
  {Fixture f=new Fixture();Response response=new Response("current");NvHTTP.plans.add(response);f.poller.start();Thread first=worker(f.poller);await(response.entered);
   f.poller.start();Thread second=worker(f.poller);check(first==second,"repeated start keeps one active polling worker");
   // A second baseline worker can already be waiting on the network monitor.
   Response extra=new Response("extra");extra.release.countDown();NvHTTP.plans.add(extra);
   f.poller.stop();response.release.countDown();retire(first);if(second!=first)retire(second);}
  {Fixture f=new Fixture();Response response=new Response("old");response.release.countDown();NvHTTP.plans.add(response);Thread t;
   synchronized(f.tuple.networkLock){f.poller.start();t=worker(f.poller);awaitBlocked(t);f.poller.stop();}
   awaitIdleOrExit(t);check(NvHTTP.requests.get()==0,"a worker stopped while waiting for the network lock never starts HTTP");t.interrupt();join(t);}
  {ComputerManagerService service=new ComputerManagerService();
   check(service.getLocalDatabaseReference()&&service.dbRefCount.value.get()==2,"a live database admits another reference");
   service.releaseLocalDatabaseReference();check(service.dbManager.closes.get()==0,"a held reference keeps the database open");
   service.releaseLocalDatabaseReference();check(service.dbManager.closes.get()==1&&!service.getLocalDatabaseReference(),"the last release closes once and rejects future acquisition");}
  {ComputerManagerService service=new ComputerManagerService();service.activePolls.set(1);AtomicBoolean interrupted=new AtomicBoolean();
   Thread t=new Thread(()->{service.waitForPollingStopped();interrupted.set(Thread.currentThread().isInterrupted());});t.start();awaitIdleOrExit(t);t.interrupt();t.join(1000);
   check(!t.isAlive(),"interrupting a polling-shutdown wait returns while a network poll is still blocked");
   service.activePolls.set(0);join(t);check(interrupted.get(),"polling-shutdown cancellation preserves the caller's interrupt flag");}
  {ComputerManagerService service=new ComputerManagerService();service.dbRefCount.pause=true;AtomicBoolean acquired=new AtomicBoolean();
   Thread t=new Thread(()->acquired.set(service.getLocalDatabaseReference()));t.start();await(service.dbRefCount.entered);
   service.releaseLocalDatabaseReference();service.dbRefCount.resume.countDown();join(t);
   check(!acquired.get(),"an acquisition racing the last release cannot return a closed database");
   check(service.dbRefCount.value.get()==0&&service.dbManager.closes.get()==1,"a losing acquisition cannot resurrect the reference count");}
  System.out.println(checks+" lifecycle checks, "+failures+" failures");if(failures!=0)System.exit(1);
 }
}
interface ComputerManagerListener {void notifyComputerUpdated(ComputerDetails c);}
class ComputerDetails {enum State{ONLINE} State state=State.ONLINE;Object pairState=PairingManager.PairState.PAIRED;String uuid="host",name="pc",rawAppList;int httpsPort;Object serverCert;}
class PairingManager {enum PairState{PAIRED}}
class PollingTuple {final ComputerDetails computer;final Object networkLock=new Object();PollingTuple(ComputerDetails c){computer=c;}}
class IdentityManager {String getUniqueId(){return "id";}}
class PlatformBinding {static Object getCryptoProvider(Object o){return null;}}
class ServerHelper {static Object getCurrentAddressFromComputer(Object o){return null;}}
class LimeLog {static void warning(String s){}}
class NvApp {}
class XmlPullParserException extends Exception {}
class Response {final String value;final CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1),parseEntered=new CountDownLatch(1),parseRelease=new CountDownLatch(1);boolean pauseParse;Response(String s){value=s;}}
class NvHTTP {
 static final BlockingQueue<Response> plans=new LinkedBlockingQueue<>();static final Map<String,Response> parsed=new ConcurrentHashMap<>();static final AtomicInteger requests=new AtomicInteger();
 NvHTTP(Object a,int b,String c,Object d,Object e){}
 String getAppListRaw()throws IOException{requests.incrementAndGet();Response r=plans.poll();if(r==null)throw new AssertionError("unexpected HTTP request");r.entered.countDown();ComputerManagerService.waitIgnoringInterrupt(r.release);parsed.put(r.value,r);return r.value;}
 static List<NvApp> getAppListByReader(StringReader reader)throws IOException,XmlPullParserException{char[] chars=new char[64];String s=new String(chars,0,reader.read(chars));Response r=parsed.get(s);if(r.pauseParse){r.parseEntered.countDown();ComputerManagerService.waitIgnoringInterrupt(r.parseRelease);}return List.of(new NvApp());}
}
class CacheHelper {static final List<String> writes=new CopyOnWriteArrayList<>();static void writeStringToCacheFileAtomically(File f,String value,String... path)throws IOException{writes.add(value);}}
class Database {final AtomicInteger closes=new AtomicInteger();void close(){closes.incrementAndGet();}}
// Delegate every atomic operation to a real AtomicInteger. Pause just after an
// observed get(), modeling preemption before acquisition commits its reference.
class RefCount {
 final AtomicInteger value=new AtomicInteger(1);volatile boolean pause;final CountDownLatch entered=new CountDownLatch(1),resume=new CountDownLatch(1);
 int get(){int result=value.get();if(pause){pause=false;entered.countDown();ComputerManagerService.waitIgnoringInterrupt(resume);}return result;}
 int incrementAndGet(){return value.incrementAndGet();}int decrementAndGet(){return value.decrementAndGet();}
 boolean compareAndSet(int before,int after){return value.compareAndSet(before,after);}
}
'''
java = (java.replace('@@ACQUIRE@@', block('private boolean getLocalDatabaseReference()'))
        .replace('@@RELEASE@@', block('private void releaseLocalDatabaseReference()'))
        .replace('@@WAIT@@', block('public void waitForPollingStopped()'))
        .replace('@@POLLER@@', block('public class ApplistPoller')))
with tempfile.TemporaryDirectory(prefix='computer-poll-') as directory:
    work = Path(directory)
    (work / 'ComputerManagerService.java').write_text(java)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(work / 'ComputerManagerService.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'ComputerManagerService'], timeout=35).returncode)
