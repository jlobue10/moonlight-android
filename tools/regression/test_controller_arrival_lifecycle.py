#!/usr/bin/env python3
"""Execute USB listener replay and production controller arrival/slot allocation.

A puck reader may announce and report input after setListener() publishes its
listener but before its attached-slot replay. Also exercise duplicate arrivals
and admission racing stop. Android discovery and the connection are fakes.
--baseline reads production methods from HEAD; JDK 17+, no Android SDK required.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
            if '--baseline' in sys.argv else (ROOT / path).read_text())


def method(source, marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


base = 'app/src/main/java/com/limelight/binding/input/'
handler = read(base + 'ControllerHandler.java')
service = read(base + 'driver/UsbDriverService.java')
puck = read(base + 'driver/SteamControllerUsb.java')

code = r'''
import java.util.*;
import java.util.concurrent.*;
public class ArrivalLifecycle implements UsbDriverListener {
 static class LimeLog {static void info(String s){}}
 static class AbstractController {
  final int id; final java.util.concurrent.atomic.AtomicInteger creations=new java.util.concurrent.atomic.AtomicInteger(); CountDownLatch entered, resume;
  AbstractController(int id){this.id=id;}
  int getControllerId(){return id;}
  int getVendorId(){creations.incrementAndGet();if(entered!=null){entered.countDown();await(resume);}return 0x28de;}
  int getProductId(){return 0x1304;}
 }
 class GenericControllerContext {
  int id,vendorId,productId,destroys;short controllerNumber;
  boolean external,assignedControllerNumber,reservedControllerNumber;
  float leftStickDeadzoneRadius,rightStickDeadzoneRadius,triggerDeadzone;
  void sendControllerArrival(){conn.arrivals.add((int)controllerNumber);}
  void destroy(){++destroys;}
 }
 class InputDeviceContext extends GenericControllerContext {
  String name="test"; boolean hasJoystickAxes;Object sensorManager;InputDevice inputDevice;
 }
 class UsbDeviceContext extends InputDeviceContext {AbstractController device;}
 static class InputDevice {static InputDevice getDevice(int id){return null;}int getId(){return 0;}}
 static class InputManager {void unregisterInputDeviceListener(Object o){}}
 static class Vibrator {void cancel(){}}
 static class Contexts extends ArrayList<InputDeviceContext> {
  public InputDeviceContext get(int id){return null;}void put(int id,InputDeviceContext c){}
  InputDeviceContext valueAt(int i){return super.get(i);}
 }
 static class Preferences {boolean multiController=true,onscreenController;
  boolean gamepadMotionSensorsFallbackToDevice,forceMotionSensorsFallbackToDevice;}
 static class Connection {
  final List<Integer> arrivals=new ArrayList<>(),masks=new ArrayList<>(),batterySlots=new ArrayList<>();
  void sendControllerInput(short slot,short mask,short b,byte l,byte r,short lx,short ly,short rx,short ry){masks.add((int)mask);}
  void sendControllerBatteryEvent(byte slot,byte state,byte pct){batterySlots.add((int)slot);}
 }
 private volatile boolean stopped=false;
 final Object controllerMaskLock=new Object();short currentControllers,initialControllers;
 final Preferences prefConfig=new Preferences();final Connection conn=new Connection();
 final InputManager inputManager=new InputManager();final Vibrator deviceVibrator=new Vibrator();
 final Contexts inputDeviceContexts=new Contexts();final InputDeviceContext defaultContext=new InputDeviceContext();
 final ConcurrentHashMap<Integer,UsbDeviceContext> usbDeviceContexts=new ConcurrentHashMap<>();
 Object deviceSensorManager;double stickDeadzone;static final int MAX_GAMEPADS=16;
 boolean hasOwnMotionSensors(InputDeviceContext c){return true;}
 boolean isAssociatedJoystick(InputDevice a,InputDevice b){return false;}
 InputDeviceContext createInputDeviceContextForDevice(InputDevice d){throw new AssertionError();}
 void refreshInputDeviceContextSnapshot(){}
 METHODS
 static class SteamControllerUsb {
  boolean stopped; final List<Slot> slots=new ArrayList<>();
  static class Slot extends AbstractController {
   boolean connected=true;int resets;Slot(int id){super(id);}void resetInputState(){++resets;}
  }
  REPLAY
 }
 static class UsbDriverService {
  volatile UsbDriverListener listener;
  final List<AbstractController> controllers=new ArrayList<>();
  Runnable afterPublish;
  final List<SteamControllerUsb> steamUsbDevices=new ArrayList<>() {
   @Override public Iterator<SteamControllerUsb> iterator(){
    if(afterPublish!=null){Runnable hook=afterPublish;afterPublish=null;hook.run();}
    return super.iterator();
   }
  };
  SERVICE_METHODS
  class UsbDriverBinder { SET_LISTENER }
 }
 static int checks,failures;
 static void check(boolean ok,String name){++checks;failures+=ok?0:1;System.out.println((ok?"PASS ":"FAIL ")+name);}
 static void await(CountDownLatch latch){try{if(!latch.await(2,TimeUnit.SECONDS))throw new AssertionError("timeout");}catch(InterruptedException e){throw new RuntimeException(e);}}
 public static void main(String[] args)throws Exception {
  ArrivalLifecycle h=new ArrivalLifecycle();UsbDriverService service=new UsbDriverService();
  SteamControllerUsb puck=new SteamControllerUsb();SteamControllerUsb.Slot slot=new SteamControllerUsb.Slot(7);
  puck.slots.add(slot);service.steamUsbDevices.add(puck);
  final GenericControllerContext[] first={null};
  // The reader holds the slot lock while connecting and reporting input. Replay
  // obtains that lock only after the reader's first report has allocated slot 0.
  service.afterPublish=()->{synchronized(slot){service.deviceAdded(slot);service.reportControllerBattery(7,(byte)1,(byte)80);first[0]=h.usbDeviceContexts.get(7);}};
  service.new UsbDriverBinder().setListener(h);
  service.reportControllerBattery(7,(byte)1,(byte)79);
  check(h.usbDeviceContexts.get(7)==first[0],"puck replay retains the live context");
  check(h.conn.arrivals.equals(List.of(0))&&h.currentControllers==1,"a live arrival followed by replay reserves exactly one host slot");
  check(h.conn.batterySlots.equals(List.of(0,0)),"input stays on the original player number");
  service.deviceRemoved(slot);
  check(h.currentControllers==0&&h.conn.masks.equals(List.of(0))&&first[0].destroys==1,"disconnect releases the only reservation and retires the original context");
  service.deviceAdded(slot);service.reportControllerBattery(7,(byte)1,(byte)78);
  check(h.conn.arrivals.equals(List.of(0,0))&&h.currentControllers==1,"a real reconnect of the same puck slot creates a fresh context at player zero");
  GenericControllerContext reconnected=h.usbDeviceContexts.get(7);
  service.deviceAdded(slot);service.deviceAdded(slot);
  check(h.usbDeviceContexts.get(7)==reconnected,"repeated arrivals do not reset controller state");
  h.stop();service.deviceAdded(new AbstractController(8));
  check(!h.usbDeviceContexts.containsKey(8),"arrivals after stop are ignored");

  ArrivalLifecycle race=new ArrivalLifecycle();AbstractController blocked=new AbstractController(9);
  blocked.entered=new CountDownLatch(1);blocked.resume=new CountDownLatch(1);
  Thread add=new Thread(()->race.deviceAdded(blocked));add.start();await(blocked.entered);
  CountDownLatch stopping=new CountDownLatch(1),finished=new CountDownLatch(1);
  Thread stop=new Thread(()->{stopping.countDown();race.stop();finished.countDown();});stop.start();await(stopping);
  check(!finished.await(150,TimeUnit.MILLISECONDS),"stop waits for an admitted arrival to finish publishing");
  blocked.resume.countDown();add.join(2000);stop.join(2000);
  check(!add.isAlive()&&!stop.isAlive()&&race.usbDeviceContexts.get(9).destroys==1,"an arrival racing stop is retired, never published after cleanup");

  ArrivalLifecycle concurrent=new ArrivalLifecycle();AbstractController same=new AbstractController(10);
  same.entered=new CountDownLatch(1);same.resume=new CountDownLatch(1);
  Thread a=new Thread(()->concurrent.deviceAdded(same));a.start();await(same.entered);
  Thread b=new Thread(()->concurrent.deviceAdded(same));b.start();same.resume.countDown();a.join(2000);b.join(2000);
  concurrent.reportControllerBattery(10,(byte)1,(byte)80);
  check(!a.isAlive()&&!b.isAlive()&&same.creations.get()==1&&concurrent.currentControllers==1&&concurrent.conn.arrivals.equals(List.of(0)),"concurrent duplicate arrivals keep one usable controller");
  System.out.println(checks+" checks; "+failures+" failures");System.exit(failures==0?0:1);
 }
}
interface UsbDriverListener {
 void deviceAdded(ArrivalLifecycle.AbstractController c);void deviceRemoved(ArrivalLifecycle.AbstractController c);
 void reportControllerBattery(int id,byte state,byte percentage);
}
'''

markers = ['    public void deviceAdded(', '    public void deviceRemoved(', '    public void reportControllerBattery(',
           '    private UsbDeviceContext createUsbDeviceContextForDevice(', '    private void assignControllerNumberIfNeeded(',
           '    private void releaseControllerNumber(', '    private short getActiveControllerMask(', '    public void stop()']
code = code.replace('SERVICE_METHODS', '\n'.join(method(service, marker) for marker in
                    ['    public void deviceAdded(', '    public void deviceRemoved(', '    public void reportControllerBattery(']))
code = code.replace('METHODS', '\n'.join(method(handler, marker) for marker in markers))
code = code.replace('REPLAY', method(puck, '    public void reportAttachedControllers('))
code = code.replace('SET_LISTENER', method(service, '        public void setListener('))
with tempfile.TemporaryDirectory(prefix='controller-arrival-') as directory:
    work = Path(directory)
    (work / 'ArrivalLifecycle.java').write_text(code)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(work / 'ArrivalLifecycle.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'ArrivalLifecycle'], timeout=15).returncode)
