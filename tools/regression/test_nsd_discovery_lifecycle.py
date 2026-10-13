#!/usr/bin/env python3
"""Production Android NSD agent with delayed callbacks and failing API boundaries.

Runs without an Android SDK. --baseline compiles HEAD instead of the working tree.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
JAVA = ROOT / 'app/src/main/java'
STUBS = {
    'android/annotation/TargetApi.java': 'package android.annotation; public @interface TargetApi {int value();}',
    'android/os/Build.java': 'package android.os; public class Build {public static class VERSION_CODES {public static final int UPSIDE_DOWN_CAKE=34;}}',
    'android/content/Context.java': '''package android.content; import android.net.nsd.NsdManager;
public class Context {public final NsdManager manager=new NsdManager(); public <T>T getSystemService(Class<T> type){return type.cast(manager);}}''',
    'android/net/Network.java': '''package android.net; public class Network {final int id; public Network(int id){this.id=id;}
 public boolean equals(Object o){return o instanceof Network && ((Network)o).id==id;} public int hashCode(){return id;}}''',
    'android/net/nsd/NsdServiceInfo.java': '''package android.net.nsd; import java.net.*; import java.util.*;
public class NsdServiceInfo {private final String name; private final android.net.Network network; public NsdServiceInfo(String n){this(n,null);}
 public NsdServiceInfo(String n,android.net.Network net){name=n;network=net;} public android.net.Network getNetwork(){return network;}
 public String getServiceName(){return name;} public int getPort(){return 47989;}
 public List<InetAddress> getHostAddresses(){try{return List.of(InetAddress.getByName("192.0.2.1"));}catch(Exception e){throw new AssertionError(e);}}}''',
    'android/net/nsd/NsdManager.java': '''package android.net.nsd; import java.util.*; import java.util.concurrent.Executor;
public class NsdManager {
 public static final int PROTOCOL_DNS_SD=1;
 public interface DiscoveryListener {void onStartDiscoveryFailed(String t,int e); void onStopDiscoveryFailed(String t,int e);
  void onDiscoveryStarted(String t); void onDiscoveryStopped(String t);void onServiceFound(NsdServiceInfo s);void onServiceLost(NsdServiceInfo s);}
 public interface ServiceInfoCallback {void onServiceInfoCallbackRegistrationFailed(int e);void onServiceUpdated(NsdServiceInfo s);
  void onServiceLost();void onServiceInfoCallbackUnregistered();}
 public DiscoveryListener discovery; public final Set<ServiceInfoCallback> live=new HashSet<>();
 public ServiceInfoCallback last; public int registrations, invalidUnregisters; public boolean rejectRegister,rejectStop;
 public void discoverServices(String t,int p,DiscoveryListener l){discovery=l;}
 public void stopServiceDiscovery(DiscoveryListener l){if(rejectStop)throw new IllegalArgumentException("already stopped");}
 public void registerServiceInfoCallback(NsdServiceInfo s,Executor e,ServiceInfoCallback c){
  if(rejectRegister)throw new IllegalArgumentException("registration refused");live.add(c);last=c;registrations++;}
 public void unregisterServiceInfoCallback(ServiceInfoCallback c){if(!live.remove(c)){invalidUnregisters++;throw new IllegalArgumentException("not registered");}}
 public void fail(ServiceInfoCallback c){live.remove(c);c.onServiceInfoCallbackRegistrationFailed(3);}
}''',
    'com/limelight/LimeLog.java': 'package com.limelight; public class LimeLog {public static void info(String s){} public static void warning(String s){} public static void severe(String s){}}',
}
TEST = r'''
import android.content.Context;
import android.net.nsd.*;
import com.limelight.nvstream.mdns.*;
import java.lang.reflect.*;
import java.util.concurrent.*;
public class NsdLifecycle {
 static int checks, failures;
 static void check(boolean ok,String name){checks++;if(!ok)failures++;System.out.println((ok?"PASS ":"FAIL ")+name);}
 static class Fixture implements MdnsDiscoveryListener {
  final Context context=new Context();final NsdManager manager=context.manager;
  final NsdManagerDiscoveryAgent agent=new NsdManagerDiscoveryAgent(context,this);
  CountDownLatch entered,release;int added,failed;public void notifyComputerAdded(MdnsComputer c){added++;
   if(entered!=null){entered.countDown();try{release.await();}catch(InterruptedException e){throw new AssertionError(e);}}}public void notifyDiscoveryFailure(Exception e){failed++;}
  void cleanup(){try{agent.stopDiscovery();}catch(IllegalArgumentException ignored){}}
  void start(){agent.startDiscovery(1000);manager.discovery.onDiscoveryStarted("_nvstream._tcp");}
  NsdManager.ServiceInfoCallback found(String name){manager.discovery.onServiceFound(new NsdServiceInfo(name));return manager.last;}
  ThreadPoolExecutor executor()throws Exception{Field f=agent.getClass().getDeclaredField("executor");f.setAccessible(true);return (ThreadPoolExecutor)f.get(agent);}
 }
 public static void main(String[] args)throws Exception {
  Fixture f=new Fixture();f.start();var old=f.found("host");f.cleanup();
  old.onServiceUpdated(new NsdServiceInfo("late-stop"));old.onServiceInfoCallbackRegistrationFailed(3);
  check(f.added==0&&f.failed==0,"queued callbacks after stop cannot report PCs or errors");
  f.start();var current=f.found("host");old.onServiceUpdated(new NsdServiceInfo("old-session"));
  old.onServiceInfoCallbackUnregistered();current.onServiceUpdated(new NsdServiceInfo("new-session"));
  check(f.added==1,"old session callback cannot publish into a restarted discovery");
  f.cleanup();check(f.manager.live.isEmpty(),"late old unregistration cannot remove current callback ownership");

  f=new Fixture();f.start();old=f.found("host");f.found("host");
  check(f.manager.live.size()==1,"repeated found events retain only one registration per tracked service");
  f.cleanup();check(f.manager.live.isEmpty(),"stop unregisters every retained registration");

  f=new Fixture();f.start();old=f.found("lost");
  f.manager.discovery.onServiceLost(new NsdServiceInfo("lost"));old.onServiceUpdated(new NsdServiceInfo("late-loss"));
  check(f.added==0,"late service update after loss is ignored");
  f.cleanup();

  f=new Fixture();f.start();
  var wifi=new NsdServiceInfo("same-name",new android.net.Network(1));
  var ethernet=new NsdServiceInfo("same-name",new android.net.Network(2));
  f.manager.discovery.onServiceFound(wifi);old=f.manager.last;
  f.manager.discovery.onServiceFound(ethernet);current=f.manager.last;
  check(f.manager.live.size()==2,"same-name services on distinct networks retain separate registrations");
  f.manager.discovery.onServiceLost(wifi);
  old.onServiceUpdated(new NsdServiceInfo("lost-network"));current.onServiceUpdated(new NsdServiceInfo("live-network"));
  check(f.manager.live.size()==1&&f.added==1,"losing one network preserves input from the other service route");
  f.cleanup();check(f.manager.live.isEmpty(),"stop releases registrations on every network");

  f=new Fixture();f.start();old=f.found("failed");f.manager.fail(old);f.found("healthy");
  boolean stopped=true;try{f.agent.stopDiscovery();}catch(IllegalArgumentException e){stopped=false;}
  check(stopped&&f.manager.live.isEmpty()&&f.manager.invalidUnregisters==0,
        "failed registration is retired before stop and cannot abort remaining cleanup");
  f.start();old=f.found("retry");f.manager.fail(old);int n=f.manager.registrations;f.found("retry");
  check(f.manager.registrations==n+1,"failed registration may be retried by a later discovery event");f.cleanup();

  f=new Fixture();f.start();f.manager.rejectRegister=true;boolean found=true;
  try{f.found("refused");}catch(IllegalArgumentException e){found=false;}
  f.manager.rejectRegister=false;f.found("refused");
  check(found&&f.manager.live.size()==1&&f.failed==1,"synchronous registration failure is contained and retryable");f.cleanup();

  f=new Fixture();f.start();f.found("host");f.manager.rejectStop=true;stopped=true;
  try{f.agent.stopDiscovery();}catch(IllegalArgumentException e){stopped=false;}
  check(stopped&&f.manager.live.isEmpty(),"an already-stopped discovery cannot abort service callback cleanup");

  f=new Fixture();f.start();for(int i=0;i<300;i++)f.found("host-"+i);
  check(f.manager.live.size()<=256,"platform service registrations obey the discovery tracking bound");f.cleanup();

  f=new Fixture();f.start();old=f.found("host");f.manager.discovery.onDiscoveryStopped("_nvstream._tcp");
  old.onServiceUpdated(new NsdServiceInfo("late-platform-stop"));
  check(f.manager.live.isEmpty()&&f.added==0,"platform discovery stop also retires service callbacks");

  f=new Fixture();f.start();current=f.found("slow");f.entered=new CountDownLatch(1);f.release=new CountDownLatch(1);
  final Fixture slow=f;final NsdManager.ServiceInfoCallback cb=current;
  Thread worker=new Thread(()->cb.onServiceUpdated(new NsdServiceInfo("slow")));worker.start();
  check(f.entered.await(1,TimeUnit.SECONDS),"a current callback reaches the discovery consumer");
  Thread stopper=new Thread(slow::cleanup);stopper.start();stopper.join(500);
  check(!stopper.isAlive(),"stop does not wait for an admitted blocking server-info lookup");
  f.release.countDown();worker.join(1000);stopper.join(1000);

  f=new Fixture();ThreadPoolExecutor pool=f.executor();pool.setKeepAliveTime(20,TimeUnit.MILLISECONDS);
  try {
   pool.submit(()->{}).get(1,TimeUnit.SECONDS);
   long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(1);
   while(pool.getPoolSize()!=0&&System.nanoTime()<deadline)Thread.sleep(5);
   check(pool.getPoolSize()==0,"idle callback executor releases its worker thread");
   check(pool.submit(()->42).get(1,TimeUnit.SECONDS)==42,"idle retirement still permits later discovery callbacks");
  } finally {pool.shutdownNow();}
  System.out.println(checks+" checks, "+failures+" failures");if(failures!=0)System.exit(1);
 }
}'''
with tempfile.TemporaryDirectory(prefix='nsd-lifecycle-') as tmp:
    work = Path(tmp)
    STUBS['NsdLifecycle.java'] = TEST
    for name in ['NsdManagerDiscoveryAgent', 'MdnsDiscoveryAgent', 'MdnsComputer', 'MdnsDiscoveryListener']:
        path = 'com/limelight/nvstream/mdns/' + name + '.java'
        STUBS[path] = (subprocess.check_output(['git', 'show', 'HEAD:app/src/main/java/' + path], cwd=ROOT, text=True)
                       if '--baseline' in sys.argv else (JAVA / path).read_text())
    for name, source in STUBS.items():
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(source)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work / 'classes'),
                    *map(str, work.rglob('*.java'))], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work / 'classes'), 'NsdLifecycle'], timeout=15).returncode)
