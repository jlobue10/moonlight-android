#!/usr/bin/env python3
"""Run the production legacy mDNS agent with delayed resolver boundaries.

Uses real Java threads and the production address/reporting classes. Android
Wi-Fi and JmDNS are fakes; no network or SDK is required. --baseline reads HEAD.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
JAVA = ROOT / 'app/src/main/java'
STUBS = {
    'android/content/Context.java': '''package android.content; import android.net.wifi.WifiManager;
public class Context {public static final String WIFI_SERVICE="wifi";public final WifiManager wifi=new WifiManager();
 public Object getSystemService(String name){return wifi;}}''',
    'android/net/wifi/WifiManager.java': '''package android.net.wifi; public class WifiManager {
 public final MulticastLock lock=new MulticastLock();public MulticastLock createMulticastLock(String n){return lock;}
 public static class MulticastLock {public boolean held;public void setReferenceCounted(boolean b){}
 public void acquire(){held=true;}public void release(){held=false;}}}''',
    'com/limelight/LimeLog.java': 'package com.limelight;public class LimeLog {public static void info(String s){}public static void warning(String s){}}',
    'javax/jmdns/NetworkTopologyDiscovery.java': '''package javax.jmdns;public interface NetworkTopologyDiscovery {
 class Factory {public interface ClassDelegate {NetworkTopologyDiscovery newNetworkTopologyDiscovery();}
 public static void setClassDelegate(ClassDelegate d){}}}''',
    'javax/jmdns/impl/NetworkTopologyDiscoveryImpl.java': '''package javax.jmdns.impl;import java.net.*;
public class NetworkTopologyDiscoveryImpl implements javax.jmdns.NetworkTopologyDiscovery {
 public boolean useInetAddress(NetworkInterface n,InetAddress a){return true;}}''',
    'javax/jmdns/ServiceListener.java': '''package javax.jmdns;public interface ServiceListener {
 void serviceAdded(ServiceEvent e);void serviceRemoved(ServiceEvent e);void serviceResolved(ServiceEvent e);}''',
    'javax/jmdns/ServiceInfo.java': '''package javax.jmdns;import java.net.*;public class ServiceInfo {
 private final String name;public ServiceInfo(String n){name=n;}public String getName(){return name;}
 public int getPort(){return 47989;}public Inet4Address[] getInet4Addresses(){try{return new Inet4Address[]{(Inet4Address)InetAddress.getByName("192.0.2.1")};}catch(Exception e){throw new AssertionError(e);}}
 public Inet6Address[] getInet6Addresses(){return new Inet6Address[0];}}''',
    'javax/jmdns/JmDNS.java': '''package javax.jmdns;public class JmDNS {
 public Runnable beforeResolve;public ServiceInfo result;public int calls;
 public ServiceInfo getServiceInfo(String type,String name,long timeout){++calls;if(beforeResolve!=null)beforeResolve.run();return result;}}''',
    'javax/jmdns/ServiceEvent.java': '''package javax.jmdns;public class ServiceEvent {
 private final ServiceInfo info;private final JmDNS dns;public ServiceEvent(String n,JmDNS d){info=new ServiceInfo(n);dns=d;}
 public ServiceInfo getInfo(){return info;}public JmDNS getDNS(){return dns;}}''',
    'javax/jmdns/JmmDNS.java': '''package javax.jmdns;import java.io.*;import java.util.concurrent.*;
public class JmmDNS {
 public volatile ServiceListener listener;public volatile CountDownLatch requested=new CountDownLatch(1);
 public volatile Runnable beforeRetry;public volatile ServiceInfo[] retryResult=new ServiceInfo[0];
 public void addServiceListener(String t,ServiceListener l){listener=l;}
 public void requestServiceInfo(String t,String n,long timeout){requested.countDown();}
 public ServiceInfo[] getServiceInfos(String t,String n,long timeout){Runnable r=beforeRetry;if(r!=null){beforeRetry=null;r.run();}return retryResult;}
 public static class Factory {public static final JmmDNS instance=new JmmDNS();
 public static JmmDNS getInstance(){return instance;}public static void close()throws IOException{}}
}''',
}
TEST = r'''
import android.content.Context;
import com.limelight.nvstream.mdns.*;
import javax.jmdns.*;
import java.lang.reflect.*;
import java.net.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
public class JmdnsLifecycle {
 static int checks,failures;static AtomicReference<Throwable> workerError=new AtomicReference<>();
 static void check(boolean ok,String name){++checks;if(!ok)++failures;System.out.println((ok?"PASS ":"FAIL ")+name);}
 static Object get(Object o,String name)throws Exception{Field f=JmDNSDiscoveryAgent.class.getDeclaredField(name);f.setAccessible(true);return f.get(o);}
 static void await(CountDownLatch latch)throws Exception{if(!latch.await(3,TimeUnit.SECONDS))throw new AssertionError("boundary not reached");}
 static void waitIgnoringInterrupt(CountDownLatch latch){boolean interrupted=false;for(;;){try{latch.await();break;}catch(InterruptedException e){interrupted=true;}}
  if(interrupted)Thread.currentThread().interrupt();}
 static Thread worker(Runnable task){Thread t=new Thread(()->{try{task.run();}catch(Throwable e){workerError.set(e);}});t.setDaemon(true);t.start();return t;}
 static void join(Thread t)throws Exception{t.join(3000);if(t.isAlive())throw new AssertionError("worker did not stop");
  if(workerError.get()!=null)throw new AssertionError(workerError.get());}
 static class SnapshotSet extends HashSet<MdnsComputer> {
  final CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);
  boolean pause;
  @Override public Object[] toArray(){
   if(!pause)return super.toArray();List<MdnsComputer> values=new ArrayList<>();Iterator<MdnsComputer> iterator=super.iterator();
   values.add(iterator.next());entered.countDown();waitIgnoringInterrupt(release);
   while(iterator.hasNext())values.add(iterator.next());return values.toArray();
  }
 }
 static ServiceInfo ipv6(String name){return new ServiceInfo(name){
  @Override public Inet4Address[] getInet4Addresses(){return new Inet4Address[0];}
  @Override public Inet6Address[] getInet6Addresses(){try{return new Inet6Address[]{(Inet6Address)InetAddress.getByName("2001:db8::1")};}catch(Exception e){throw new AssertionError(e);}}
 };}
 static class Fixture implements MdnsDiscoveryListener {
  final Context context=new Context();final JmDNSDiscoveryAgent agent=new JmDNSDiscoveryAgent(context,this);
  final JmmDNS resolver=JmmDNS.Factory.instance;final AtomicInteger added=new AtomicInteger();Thread discovery;
  CountDownLatch reportedEntered,reportedRelease;
  public void notifyComputerAdded(MdnsComputer c){added.incrementAndGet();if(reportedEntered!=null){reportedEntered.countDown();waitIgnoringInterrupt(reportedRelease);}}
  public void notifyDiscoveryFailure(Exception e){throw new AssertionError(e);}
  void start(int interval)throws Exception{resolver.requested=new CountDownLatch(1);agent.startDiscovery(interval);discovery=(Thread)get(agent,"discoveryThread");await(resolver.requested);}
  void start()throws Exception{start(1000000);}
  ServiceListener listener()throws Exception{Set<?> listeners=(Set<?>)get(null,"listeners");synchronized(listeners){return (ServiceListener)listeners.iterator().next();}}
  int pending()throws Exception{Set<?> pending=(Set<?>)get(agent,"pendingResolution");return pending.size();}
  void stop()throws Exception{agent.stopDiscovery();join(discovery);check(!context.wifi.lock.held,"stop releases the multicast lock");}
 }
 public static void main(String[] args)throws Exception {
  // A callback already resolving when discovery stops/restarts must not publish
  // a PC or refill the retry set after the old cycle has been retired.
  for(boolean restart:new boolean[]{false,true})for(boolean resolved:new boolean[]{false,true}){
   Fixture f=new Fixture();f.start();ServiceListener old=f.listener();JmDNS dns=new JmDNS();
   CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);
   dns.result=resolved?new ServiceInfo("late"):null;dns.beforeResolve=()->{entered.countDown();waitIgnoringInterrupt(release);};
   Thread callback=worker(()->old.serviceAdded(new ServiceEvent("late",dns)));await(entered);
   f.stop();if(restart)f.start();release.countDown();join(callback);
   check(f.added.get()==0&&f.pending()==0,"retired resolution has no effect; restart="+restart+", resolved="+resolved);
   if(restart){JmDNS current=new JmDNS();current.result=new ServiceInfo("current");f.listener().serviceAdded(new ServiceEvent("current",current));
    check(f.added.get()==1,"the current discovery cycle still reports a host");f.stop();}
  }
  // The static dispatcher snapshots its listener set before invoking callbacks.
  // An old snapshot must retain its cycle identity even if invoked after restart.
  {Fixture f=new Fixture();f.start();ServiceListener old=f.listener();f.stop();f.start();
   JmDNS dns=new JmDNS();dns.result=new ServiceInfo("old-snapshot");old.serviceAdded(new ServiceEvent("old-snapshot",dns));
   check(dns.calls==0&&f.added.get()==0,"a retired listener snapshot cannot resolve into a new cycle");f.stop();}
  // The consumer can perform a blocking server-info lookup. A callback already
  // admitted before stop may finish, but stop must not wait for that lookup.
  {Fixture f=new Fixture();f.start();f.reportedEntered=new CountDownLatch(1);f.reportedRelease=new CountDownLatch(1);
   JmDNS dns=new JmDNS();dns.result=new ServiceInfo("admitted");ServiceListener listener=f.listener();
   Thread callback=worker(()->listener.serviceAdded(new ServiceEvent("admitted",dns)));await(f.reportedEntered);
   CountDownLatch stopped=new CountDownLatch(1);Thread stopper=worker(()->{f.agent.stopDiscovery();stopped.countDown();});
   boolean stoppedBeforeRelease=stopped.await(1,TimeUnit.SECONDS);f.reportedRelease.countDown();
   join(callback);join(stopper);join(f.discovery);
   check(stoppedBeforeRelease,"stop does not wait for an admitted blocking discovery consumer");}
  // A polling retry can itself be blocked inside JmDNS when stop interrupts the
  // polling thread. Model a resolver that returns a result after that interrupt.
  {Fixture f=new Fixture();CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);
   f.resolver.retryResult=new ServiceInfo[]{new ServiceInfo("late-retry")};
   f.resolver.beforeRetry=()->{entered.countDown();waitIgnoringInterrupt(release);};f.start(10);
   f.listener().serviceAdded(new ServiceEvent("late-retry",new JmDNS()));await(entered);
   Thread oldThread=f.discovery;f.agent.stopDiscovery();f.start();release.countDown();join(oldThread);
   check(f.added.get()==0&&f.pending()==0,"a stopped polling retry cannot report into a restarted cycle");
   f.resolver.retryResult=new ServiceInfo[0];f.stop();}
  // Unresolved advertisements bypass the base agent's resolved-host cap. Keep
  // their retry set bounded too, and remove lost/stopped entries promptly.
  {Fixture f=new Fixture();f.start();ServiceListener listener=f.listener();JmDNS dns=new JmDNS();
   for(int i=0;i<1000;i++)listener.serviceAdded(new ServiceEvent("unresolved-"+i,dns));
   check(f.pending()==256,"1000 unresolved advertisements retain at most 256 retry names");
   listener.serviceRemoved(new ServiceEvent("unresolved-0",dns));
   check(f.pending()==255,"a lost unresolved service releases its retry slot");
   listener.serviceAdded(new ServiceEvent("replacement",dns));
   check(f.pending()==256,"a new advertisement can use the released retry slot");
   f.stop();check(f.pending()==0,"stop clears all pending resolution names");}
  // Force an IPv6-only result to overlap a computer-list snapshot. The set
  // iterator is real; pausing it makes an unguarded publication deterministic.
  {Fixture f=new Fixture();f.start();SnapshotSet computers=new SnapshotSet();
   Field field=MdnsDiscoveryAgent.class.getDeclaredField("computers");field.setAccessible(true);field.set(f.agent,computers);
   ServiceListener listener=f.listener();
   for(String name:new String[]{"v6-first","v6-second"}){JmDNS dns=new JmDNS();dns.result=ipv6(name);listener.serviceAdded(new ServiceEvent(name,dns));}
   computers.pause=true;FutureTask<List<MdnsComputer>> snapshot=new FutureTask<>(()->f.agent.getComputerSet());
   Thread reader=new Thread(snapshot);reader.setDaemon(true);reader.start();await(computers.entered);
   JmDNS dns=new JmDNS();dns.result=ipv6("v6-third");
   FutureTask<Void> publish=new FutureTask<>(()->{listener.serviceAdded(new ServiceEvent("v6-third",dns));return null;});
   Thread writer=new Thread(publish);writer.setDaemon(true);writer.start();long deadline=System.nanoTime()+3_000_000_000L;
   while(writer.isAlive()&&writer.getState()!=Thread.State.BLOCKED&&System.nanoTime()<deadline)Thread.yield();
   boolean serialized=writer.getState()==Thread.State.BLOCKED;computers.release.countDown();join(reader);join(writer);publish.get();
   boolean snapshotOk;try{snapshotOk=snapshot.get().size()==2;}catch(ExecutionException e){snapshotOk=false;}
   check(serialized&&snapshotOk,"IPv6 publication cannot mutate a set while a computer-list snapshot reads it");
   computers.pause=false;check(f.agent.getComputerSet().size()==3,"the IPv6 result is retained after the snapshot completes");f.stop();}
  System.out.println(checks+" mDNS lifecycle checks, "+failures+" failures");if(failures!=0)System.exit(1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='jmdns-lifecycle-') as directory:
    work = Path(directory)
    for name in ['JmDNSDiscoveryAgent.java', 'MdnsDiscoveryAgent.java', 'MdnsComputer.java', 'MdnsDiscoveryListener.java']:
        relative = 'com/limelight/nvstream/mdns/' + name
        STUBS[relative] = (subprocess.check_output(['git', 'show', 'HEAD:app/src/main/java/' + relative], cwd=ROOT, text=True)
                           if '--baseline' in sys.argv else (JAVA / relative).read_text())
    STUBS['JmdnsLifecycle.java'] = TEST
    for name, source in STUBS.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    sources = [str(p) for p in work.rglob('*.java')]
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), *sources], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'JmdnsLifecycle'], timeout=30).returncode)
