#!/usr/bin/env python3
"""Race production feedback forwarding against ControllerHandler.stop().

Pause an admitted callback during context iteration, before the driver call.
Fake devices record writes; Android discovery and hardware I/O are not used.
--baseline reads production methods from HEAD. Requires JDK 17+.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/binding/input/ControllerHandler.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())


def method(name):
    match = re.search(r'    public (?:synchronized )?void ' + name + r'\(', source)
    assert match, name
    start = match.start()
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


code = r'''
import java.util.*;
import java.util.concurrent.*;
public class FeedbackStop {
 static class Build {static class VERSION {static int SDK_INT=0;}static class VERSION_CODES {static final int S=31;}}
 static class MoonBridge {static final byte LI_MOTION_TYPE_ACCEL=1,LI_MOTION_TYPE_GYRO=2;}
 static class Sensor {static final int TYPE_ACCELEROMETER=1,TYPE_GYROSCOPE=4;}
 interface SensorEventListener {}
 static class SensorManager {
  final Set<SensorEventListener> registered=ConcurrentHashMap.newKeySet();Runnable beforeRegister=()->{};
  Sensor getDefaultSensor(int type){return new Sensor();}
  void registerListener(SensorEventListener listener,Sensor sensor,int period){beforeRegister.run();registered.add(listener);}
  void unregisterListener(SensorEventListener listener){registered.remove(listener);}
 }
 static class Handler {
  final Set<Runnable> pending=ConcurrentHashMap.newKeySet();Runnable beforePost=()->{};
  void post(Runnable callback){beforePost.run();pending.add(callback);}
  void removeCallbacks(Runnable callback){pending.remove(callback);}
 }
 static class InputManager {void unregisterInputDeviceListener(Object listener){}}
 static class Vibrator {void cancel(){}}
 static class SceManager {boolean rumble(Object device,short low,short high){return false;}}
 static class Preferences {boolean onscreenController,onlyL3R3,vibrateOsc,vibrateFallbackToDevice;int vibrateFallbackToDeviceStrength=100;}
 static class InputDeviceContext {
  short controllerNumber,lowFreqMotor,highFreqMotor,leftTriggerMotor,rightTriggerMotor;
  boolean assignedControllerNumber=true,quadVibrators,hasRgbLed;Object inputDevice;
  short gyroReportRateHz,accelReportRateHz;long nextGyroReportNs,nextAccelReportNs;int ledArgbValue;
  SensorManager sensorManager;SensorEventListener gyroListener,accelListener;
  Runnable enableSensorRunnable=()->{},setLedStateRunnable=()->{};Handler handler;
  Vibrator vibrator,vibratorManager;
  void destroy(){
   if(sensorManager!=null){if(gyroListener!=null)sensorManager.unregisterListener(gyroListener);if(accelListener!=null)sensorManager.unregisterListener(accelListener);}
   if(handler!=null){handler.removeCallbacks(enableSensorRunnable);handler.removeCallbacks(setLedStateRunnable);}
  }
 }
 static class Device {
  final List<String> writes=new CopyOnWriteArrayList<>();
  Runnable beforeWrite=()->{};
  void rumble(short low,short high){beforeWrite.run();writes.add("rumble:"+low+":"+high);}
  void rumbleTriggers(short left,short right){beforeWrite.run();writes.add("triggers:"+left+":"+right);}
  void steamHaptic(byte[] report){beforeWrite.run();writes.add("haptic");}
  void steamSettings(byte command,byte[] triples){beforeWrite.run();writes.add("settings");}
 }
 static class UsbDeviceContext extends InputDeviceContext {
  final Device device=new Device();
  @Override void destroy(){device.rumble((short)0,(short)0);device.rumbleTriggers((short)0,(short)0);}
 }
 static class Contexts extends ArrayList<InputDeviceContext> {InputDeviceContext valueAt(int i){return get(i);}}
 static class PausedContexts extends ConcurrentHashMap<Integer,UsbDeviceContext> {
  final CountDownLatch entered=new CountDownLatch(1),resume=new CountDownLatch(1);
  @Override public Collection<UsbDeviceContext> values(){
   Collection<UsbDeviceContext> live=super.values();
   if(!Thread.currentThread().getName().equals("feedback"))return live;
   return new AbstractCollection<>() {
    public int size(){return live.size();}
    public Iterator<UsbDeviceContext> iterator(){
     Iterator<UsbDeviceContext> delegate=live.iterator();
     return new Iterator<>() {
      public boolean hasNext(){return delegate.hasNext();}
      public UsbDeviceContext next(){UsbDeviceContext next=delegate.next();entered.countDown();await(resume);return next;}
     };
    }
   };
  }
 }
 volatile boolean stopped;
 final Object controllerMaskLock=new Object();
 final InputManager inputManager=new InputManager();final Vibrator deviceVibrator=new Vibrator();
 final SceManager sceManager=new SceManager();final Preferences prefConfig=new Preferences();
 final Contexts inputDeviceContexts=new Contexts();
 InputDeviceContext[] inputDeviceContextSnapshot={};
 final InputDeviceContext defaultContext=new InputDeviceContext();
 final PausedContexts usbDeviceContexts=new PausedContexts();
 final Handler backgroundThreadHandler=new Handler();final SensorManager deviceSensorManager=new SensorManager();
 SensorEventListener createSensorListener(short id,byte type,boolean local){return new SensorEventListener(){};}
 void rumbleQuadVibrators(Vibrator v,short a,short b,short c,short d){}
 void rumbleDualVibrators(Vibrator v,short a,short b){}
 void rumbleSingleVibrator(Vibrator v,short a,short b){}
 METHODS
 void feedback(String kind){
  switch(kind){
   case "rumble":handleRumble((short)0,(short)100,(short)200);break;
   case "triggers":handleRumbleTriggers((short)0,(short)100,(short)200);break;
   case "haptic":handleSteamHaptic((short)0,new byte[]{(byte)0x82,0,3,0});break;
   case "settings":handleSteamSettings((short)0,(byte)0x87,new byte[]{76,1,0});break;
  }
 }
 static int checks,failures;
 static void check(boolean ok,String label){++checks;if(!ok)++failures;System.out.println((ok?"PASS ":"FAIL ")+label);}
 static void await(CountDownLatch latch){try{if(!latch.await(3,TimeUnit.SECONDS))throw new AssertionError("timeout");}catch(InterruptedException e){throw new AssertionError(e);}}
 public static void main(String[] args)throws Exception {
  for(String kind:List.of("rumble","triggers","haptic","settings")){
   FeedbackStop h=new FeedbackStop();UsbDeviceContext ctx=new UsbDeviceContext();h.usbDeviceContexts.put(7,ctx);
   Thread callback=new Thread(()->h.feedback(kind),"feedback");callback.setDaemon(true);callback.start();await(h.usbDeviceContexts.entered);
   CountDownLatch stopStarted=new CountDownLatch(1),stopDone=new CountDownLatch(1);
   Thread shutdown=new Thread(()->{stopStarted.countDown();h.stop();stopDone.countDown();},"stop");shutdown.setDaemon(true);
   shutdown.start();await(stopStarted);
   boolean early=stopDone.await(150,TimeUnit.MILLISECONDS);
   check(!early,kind+": stop waits for feedback already admitted before retirement");
   // A USB reader may need this lifecycle lock while feedback waits for its
   // transport. The stop barrier must release it before waiting for feedback.
   CountDownLatch inputReturned=new CountDownLatch(1);
   Thread input=new Thread(()->{synchronized(h.controllerMaskLock){inputReturned.countDown();}});input.setDaemon(true);input.start();
   check(inputReturned.await(1,TimeUnit.SECONDS),kind+": waiting stop releases the input lifecycle lock");
   h.usbDeviceContexts.resume.countDown();callback.join(2000);shutdown.join(2000);input.join(2000);
   check(!callback.isAlive()&&!shutdown.isAlive(),kind+": callback and stop both finish");
   List<String> expected=List.of(kind.equals("rumble")?"rumble:100:200":kind.equals("triggers")?"triggers:100:200":kind,"rumble:0:0","triggers:0:0");
   check(ctx.device.writes.equals(expected),kind+": motors-off follows the last admitted feedback");
   int count=ctx.device.writes.size();h.feedback(kind);h.stop();
   check(ctx.device.writes.size()==count,kind+": late callbacks and repeated stop produce no writes");
  }
  for(String kind:List.of("gyro","accel","led")){
   FeedbackStop h=new FeedbackStop();InputDeviceContext ctx=new InputDeviceContext();
   ctx.sensorManager=h.deviceSensorManager;ctx.handler=h.backgroundThreadHandler;ctx.hasRgbLed=true;
   h.inputDeviceContexts.add(ctx);h.inputDeviceContextSnapshot=new InputDeviceContext[]{ctx};Build.VERSION.SDK_INT=31;
   CountDownLatch entered=new CountDownLatch(1),resume=new CountDownLatch(1),done=new CountDownLatch(1);
   Runnable pause=()->{entered.countDown();await(resume);};
   ctx.sensorManager.beforeRegister=pause;h.backgroundThreadHandler.beforePost=pause;
   Runnable feedback=()->{if(kind.equals("led"))h.handleSetControllerLED((short)0,(byte)1,(byte)2,(byte)3);else h.handleSetMotionEventState((short)0,kind.equals("gyro")?(byte)2:(byte)1,(short)100);};
   Thread callback=new Thread(feedback);callback.setDaemon(true);callback.start();await(entered);
   Thread shutdown=new Thread(()->{h.stop();done.countDown();});shutdown.setDaemon(true);shutdown.start();
   check(!done.await(150,TimeUnit.MILLISECONDS),kind+": stop waits for admitted resource setup");
   resume.countDown();callback.join(2000);shutdown.join(2000);
   check(!callback.isAlive()&&!shutdown.isAlive(),kind+": setup and stop both finish");
   check(ctx.sensorManager.registered.isEmpty()&&h.backgroundThreadHandler.pending.isEmpty(),kind+": stopped contexts retain no sensor listener or queued LED work");
   int sensors=ctx.sensorManager.registered.size(),leds=h.backgroundThreadHandler.pending.size();feedback.run();
   check(ctx.sensorManager.registered.size()==sensors&&h.backgroundThreadHandler.pending.size()==leds,kind+": late configuration produces no new resource setup");
  }
  System.out.println(checks+" checks, "+failures+" failures");
  System.exit(failures==0?0:1);
 }
}
'''
code = code.replace('METHODS', '\n'.join(method(name) for name in
                    ['stop', 'handleRumble', 'handleRumbleTriggers', 'handleSteamHaptic', 'handleSteamSettings', 'handleSetMotionEventState', 'handleSetControllerLED']))
with tempfile.TemporaryDirectory(prefix='controller-feedback-stop-') as directory:
    work = Path(directory)
    path = work / 'FeedbackStop.java'
    path.write_text(code)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(path)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'FeedbackStop'], timeout=20).returncode)
