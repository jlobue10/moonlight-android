#!/usr/bin/env python3
"""Execute the production driver-event reporters with a recording connection.

Battery and touch events from a driver (BLE/USB) must go through the controller
number reservation (and its arrival event) like state reports do; the first
notification after a BLE announce can be a battery level. No device is used.
--baseline reads ControllerHandler.java from HEAD.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/binding/input/ControllerHandler.java'
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

prefix = r"""
import java.util.*;
import java.util.concurrent.*;
public class DriverEvents {
 static class LimeLog {static void info(String s){}}
 static class GenericControllerContext {int id; short controllerNumber; boolean assignedControllerNumber, reservedControllerNumber;}
 static class InputDeviceContext extends GenericControllerContext {}
 static class UsbDeviceContext extends InputDeviceContext {}
 static class Connection {
  final List<String> events=new ArrayList<>();
  void sendControllerBatteryEvent(byte n,byte state,byte pct){events.add("battery:"+n+":"+pct);}
  void sendControllerTouchEvent2(byte n,byte type,byte pad,int pointer,float x,float y,float p){events.add("touch:"+n+":"+pad);}
  void sendControllerArrivalEvent(byte n){events.add("arrival:"+n);}
 }
 boolean stopped;
 final Connection conn=new Connection();
 final ConcurrentHashMap<Integer,UsbDeviceContext> usbDeviceContexts=new ConcurrentHashMap<>();
 int assignments;
 void assignControllerNumberIfNeeded(GenericControllerContext context){
  if(context.assignedControllerNumber)return;
  ++assignments;context.controllerNumber=5;context.assignedControllerNumber=true;conn.sendControllerArrivalEvent((byte)5);
 }
 static int failures;
 static void check(boolean ok,String name){System.out.println((ok?"PASS ":"FAIL ")+name);if(!ok)++failures;}
"""
suffix = r"""
 public static void main(String[] args){
  DriverEvents h=new DriverEvents();UsbDeviceContext ctx=new UsbDeviceContext();ctx.id=7;h.usbDeviceContexts.put(7,ctx);
  h.reportControllerBattery(7,(byte)1,(byte)80);
  check(h.conn.events.equals(Arrays.asList("arrival:5","battery:5:80")),"a battery event reserves the number and announces the controller first");
  h.reportControllerTouch(7,(byte)1,(byte)1,0,0.5f,0.5f,1.0f);
  check(h.conn.events.size()==3&&h.conn.events.get(2).equals("touch:5:1")&&h.assignments==1,"a touch event uses the reserved number without a second arrival");
  h.reportControllerBattery(99,(byte)1,(byte)80);h.reportControllerTouch(99,(byte)0,(byte)1,0,0,0,0);
  check(h.conn.events.size()==3,"events for an unknown controller are dropped");
  h.stopped=true;h.reportControllerBattery(7,(byte)1,(byte)10);
  check(h.conn.events.size()==3,"events after stop are dropped");
  if(failures!=0)System.exit(1);
 }
}
"""
code = prefix + '\n'.join(method(m) for m in ['    public void reportControllerTouch(int controllerId', '    public void reportControllerBattery(int controllerId']).replace('@Override', '') + suffix
with tempfile.TemporaryDirectory(prefix='driver-events-') as directory:
    p = pathlib.Path(directory)
    (p / 'DriverEvents.java').write_text(code)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(p), str(p / 'DriverEvents.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(p), 'DriverEvents']).returncode)
