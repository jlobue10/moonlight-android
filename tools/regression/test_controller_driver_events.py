#!/usr/bin/env python3
"""Execute the production driver-event reporters with a recording connection.

Battery and touch events from a driver (BLE/USB) must go through the controller
number reservation (and its arrival event) like state reports do; the first
notification after a BLE announce can be a battery level. Attribute callbacks
must belong to the currently cached link, even when publication pauses while a
BLE driver disconnects and reconnects. No device is used.
--baseline reads production sources from HEAD.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
            if '--baseline' in sys.argv else (ROOT / path).read_text())

source = read('app/src/main/java/com/limelight/binding/input/ControllerHandler.java')
driver_source = read('app/src/main/java/com/limelight/binding/input/driver/SteamControllerInput.java')

def method(marker, source=source):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]

prefix = r"""
import java.util.*;
import java.util.concurrent.*;
public class DriverEvents {
 static class LimeLog {
  static java.util.function.Consumer<String> infoHook;
  static void info(String s){if(infoHook!=null)infoHook.accept(s);}
  static void warning(String s){}
 }
 static class SteamControllerAttributes {
  final String identity;
  SteamControllerAttributes(String identity){this.identity=identity;}
  boolean isEmpty(){return false;}
  String describe(){return identity;}
 }
 static class AttributeController {
  private volatile SteamControllerAttributes steamAttributes;
  final DriverEvents listener;final int controllerId;
  final String transportName="BLE", FIRMWARE_UPDATE_WARNING="test warning";
  AttributeController(DriverEvents listener,int controllerId){this.listener=listener;this.controllerId=controllerId;}
  void reportSteamAttributes(SteamControllerAttributes attributes){listener.reportControllerSteamAttributes(controllerId,attributes);}
  @DRIVER_METHODS@
 }
 static class GenericControllerContext {int id; short controllerNumber; boolean assignedControllerNumber, reservedControllerNumber;}
 static class InputDeviceContext extends GenericControllerContext {}
 static class UsbDeviceContext extends InputDeviceContext {AttributeController device;}
 static class Connection {
  final List<String> events=new ArrayList<>();
  void sendControllerBatteryEvent(byte n,byte state,byte pct){events.add("battery:"+n+":"+pct);}
  void sendControllerTouchEvent2(byte n,byte type,byte pad,int pointer,float x,float y,float p){events.add("touch:"+n+":"+pad);}
  void sendControllerArrivalEvent(byte n){events.add("arrival:"+n);}
  void sendSteamControllerAttributesEvent(byte n,SteamControllerAttributes a){events.add("attributes:"+n+":"+a.identity);}
 }
 boolean stopped;
 final Object controllerMaskLock=new Object();
 final Connection conn=new Connection();
 final ConcurrentHashMap<Integer,UsbDeviceContext> usbDeviceContexts=new ConcurrentHashMap<>();
 int assignments;
 void assignControllerNumberIfNeeded(GenericControllerContext context){
  if(context.assignedControllerNumber)return;
  ++assignments;context.controllerNumber=5;context.assignedControllerNumber=true;conn.sendControllerArrivalEvent((byte)5);
  // Model UsbDeviceContext.sendControllerArrival(): cached attributes follow the arrival.
  AttributeController device=((UsbDeviceContext)context).device;
  if(device!=null&&device.getSteamAttributes()!=null)conn.sendSteamControllerAttributesEvent((byte)5,device.getSteamAttributes());
 }
 static int failures;
 static void check(boolean ok,String name){System.out.println((ok?"PASS ":"FAIL ")+name);if(!ok)++failures;}
 static void await(CountDownLatch latch){try{if(!latch.await(3,TimeUnit.SECONDS))throw new AssertionError("latch timed out");}catch(InterruptedException e){throw new AssertionError(e);}}
"""
suffix = r"""
 public static void main(String[] args)throws Exception{
  DriverEvents h=new DriverEvents();UsbDeviceContext ctx=new UsbDeviceContext();ctx.id=7;h.usbDeviceContexts.put(7,ctx);
  h.reportControllerBattery(7,(byte)1,(byte)80);
  check(h.conn.events.equals(Arrays.asList("arrival:5","battery:5:80")),"a battery event reserves the number and announces the controller first");
  h.reportControllerTouch(7,(byte)1,(byte)1,0,0.5f,0.5f,1.0f);
  check(h.conn.events.size()==3&&h.conn.events.get(2).equals("touch:5:1")&&h.assignments==1,"a touch event uses the reserved number without a second arrival");
  h.reportControllerBattery(99,(byte)1,(byte)80);h.reportControllerTouch(99,(byte)0,(byte)1,0,0,0,0);
  check(h.conn.events.size()==3,"events for an unknown controller are dropped");
  h.stopped=true;h.reportControllerBattery(7,(byte)1,(byte)10);
  check(h.conn.events.size()==3,"events after stop are dropped");

  DriverEvents a=new DriverEvents();AttributeController device=new AttributeController(a,7);
  UsbDeviceContext first=new UsbDeviceContext();first.device=device;a.usbDeviceContexts.put(7,first);
  SteamControllerAttributes old=new SteamControllerAttributes("old");device.publishSteamAttributes(old);
  check(a.conn.events.equals(Arrays.asList("arrival:5","attributes:5:old")),"current attributes trigger arrival before delivery and are sent once");
  SteamControllerAttributes fresh=new SteamControllerAttributes("fresh");device.publishSteamAttributes(fresh);
  check(a.conn.events.size()==3&&a.conn.events.get(2).equals("attributes:5:fresh"),"new attributes reach an already assigned controller");
  a.reportControllerSteamAttributes(7,old);
  check(a.conn.events.size()==3,"an older callback cannot overwrite a newer attribute reading");
  int beforeIgnored=a.conn.events.size();
  a.reportControllerSteamAttributes(7,null);a.reportControllerSteamAttributes(99,fresh);
  a.stopped=true;a.reportControllerSteamAttributes(7,fresh);a.stopped=false;
  check(a.conn.events.size()==beforeIgnored,"null, unknown-controller and stopped attribute callbacks are dropped");

  // The BLE transport caches under its lifetime lock, then announces outside it.
  // Pause that production announcement before the listener, retire the link and
  // reuse the same driver ID. A battery report reserves the replacement's slot.
  CountDownLatch publishing=new CountDownLatch(1),resume=new CountDownLatch(1);
  device.cacheSteamAttributes(old);a.conn.events.clear();
  LimeLog.infoHook=message->{if(message.equals("Steam Controller BLE: old")){publishing.countDown();await(resume);}};
  Thread publisher=new Thread(()->device.announceSteamAttributes(old));publisher.setDaemon(true);
  try{
   publisher.start();await(publishing);
   device.clearSteamAttributes();a.usbDeviceContexts.remove(7);
   UsbDeviceContext replacement=new UsbDeviceContext();replacement.device=device;a.usbDeviceContexts.put(7,replacement);
   a.reportControllerBattery(7,(byte)1,(byte)90);
  }finally{resume.countDown();publisher.join(3000);LimeLog.infoHook=null;}
  check(!publisher.isAlive()&&a.conn.events.equals(Arrays.asList("arrival:5","battery:5:90")),"a delayed announcement from a retired BLE link cannot reach its replacement slot");
  check(device.getSteamAttributes()==null,"rejecting a retired callback leaves the replacement's cache empty");
  device.publishSteamAttributes(fresh);
  check(a.conn.events.get(a.conn.events.size()-1).equals("attributes:5:fresh"),"the replacement's own completed reading is delivered");
  if(failures!=0)System.exit(1);
 }
}
"""
driver_methods = '\n'.join(method(m, driver_source) for m in (
    '    public SteamControllerAttributes getSteamAttributes()',
    '    protected void clearSteamAttributes()',
    '    protected void publishSteamAttributes(',
    '    protected void cacheSteamAttributes(',
    '    protected void announceSteamAttributes(',
))
code = prefix.replace('@DRIVER_METHODS@', driver_methods) + '\n'.join(method(m) for m in (
    '    public void reportControllerTouch(int controllerId',
    '    public void reportControllerBattery(int controllerId',
    '    public void reportControllerSteamAttributes(int controllerId',
)) + suffix
with tempfile.TemporaryDirectory(prefix='driver-events-') as directory:
    p = pathlib.Path(directory)
    (p / 'DriverEvents.java').write_text(code)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(p), str(p / 'DriverEvents.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(p), 'DriverEvents']).returncode)
