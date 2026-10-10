#!/usr/bin/env python3
"""Execute the production USB driver service state machine with fake USB services.

A deferred attach (or a permission result) that lands after stop() must not open
and claim a device on a destroyed service, and stopping must survive a controller
removing itself from another thread. No USB device is used; --baseline reads
UsbDriverService.java from HEAD.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/binding/input/driver/UsbDriverService.java'
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

uses_main_handler = 'mainHandler' in source
prefix = r"""
import java.util.*;
import java.util.concurrent.*;
public class UsbDriverService {
 static class LimeLog {static void info(String s){} static void warning(String s){}}
 static class R {static class string {static final int error_usb_prohibited=1;}}
 static class Toast {static final int LENGTH_LONG=1; static Toast makeText(Object c,CharSequence t,int d){return new Toast();} void show(){}}
 static class Build {static class VERSION {static int SDK_INT=35;} static class VERSION_CODES {static final int S=31;}}
 static class Intent {Intent(String a){} void setPackage(String p){}}
 static class PendingIntent {static final int FLAG_MUTABLE=1; static PendingIntent getBroadcast(Object c,int r,Intent i,int f){return new PendingIntent();}}
 static class UsbDevice {String name="dev"; String getDeviceName(){return name;}}
 static class UsbDeviceConnection {int closes; void close(){++closes;}}
 static class UsbManager {
  boolean permission=true; int opens, requests;
  boolean hasPermission(UsbDevice d){return permission;}
  UsbDeviceConnection openDevice(UsbDevice d){++opens;return new UsbDeviceConnection();}
  void requestPermission(UsbDevice d,PendingIntent p){++requests;}
 }
 static class Looper {static Object getMainLooper(){return null;}}
 static class Handler {
  final List<Runnable> queue=new ArrayList<>();
  Handler(){} Handler(Object looper){}
  void postDelayed(Runnable r,long ms){queue.add(r);}
  void removeCallbacksAndMessages(Object token){queue.clear();}
  void drain(){List<Runnable> run=new ArrayList<>(queue);queue.clear();for(Runnable r:run)r.run();}
 }
 interface UsbDriverListener {void deviceRemoved(AbstractController c); void deviceAdded(AbstractController c);}
 interface UsbDriverStateListener {void onUsbPermissionPromptStarting(); void onUsbPermissionPromptCompleted();}
 static abstract class AbstractController {
  final UsbDriverService service; int stops; boolean startResult=true;
  AbstractController(UsbDriverService s){service=s;}
  boolean start(){return startResult;}
  void stop(){++stops; service.deviceRemoved(this);}
 }
 static class XboxOneController extends AbstractController {
  static boolean claim=true;
  XboxOneController(UsbDevice d,UsbDeviceConnection c,int id,UsbDriverService s){super(s);}
  static boolean canClaimDevice(UsbDevice d){return claim;}
 }
 static class Xbox360Controller extends AbstractController {
  Xbox360Controller(UsbDevice d,UsbDeviceConnection c,int id,UsbDriverService s){super(s);}
  static boolean canClaimDevice(UsbDevice d){return false;}
 }
 static class Xbox360WirelessDongle extends AbstractController {
  Xbox360WirelessDongle(UsbDevice d,UsbDeviceConnection c,int id,UsbDriverService s){super(s);}
  static boolean canClaimDevice(UsbDevice d){return false;}
 }
 static class ProConController extends AbstractController {
  ProConController(UsbDevice d,UsbDeviceConnection c,int id,UsbDriverService s){super(s);}
  static boolean canClaimDevice(UsbDevice d){return false;}
 }
 static class Prefs {boolean bindAllUsb;}
 static boolean shouldClaimDevice(UsbDevice d,boolean all){return true;}
 final UsbManager usbManager=new UsbManager();
 final Prefs prefConfig=new Prefs();
 boolean started=true;
 final Object receiver=new Object();
 int unregistrations;
 void unregisterReceiver(Object r){++unregistrations;}
 String getPackageName(){return "test";}
 CharSequence getText(int id){return "";}
 final List<AbstractController> controllers=new CopyOnWriteArrayList<>();
 final Handler mainHandler=new Handler();
 UsbDriverListener listener;
 UsbDriverStateListener stateListener;
 int nextDeviceId;
 static int failures;
 static void check(boolean ok,String name){System.out.println((ok?"PASS ":"FAIL ")+name);if(!ok)++failures;}
"""
suffix = r"""
 public static void main(String[] args) throws Exception {
  {UsbDriverService s=new UsbDriverService();UsbDevice dev=new UsbDevice();
   s.handleUsbDeviceState(dev);
   check(s.usbManager.opens==1&&s.controllers.size()==1,"a permitted device is opened and claimed while started");
   s.mainHandler.postDelayed(()->s.handleUsbDeviceState(new UsbDevice()),1000);
   s.stop();
   check(s.controllers.isEmpty()&&s.unregistrations==1&&s.mainHandler.queue.isEmpty(),"stop stops the controllers and drops deferred attach handling");
   s.mainHandler.drain();
   s.handleUsbDeviceState(new UsbDevice());
   check(s.usbManager.opens==1&&s.controllers.isEmpty(),"a late attach after stop neither opens nor claims a device");
   s.usbManager.permission=false;s.handleUsbDeviceState(new UsbDevice());
   check(s.usbManager.requests==0,"a late attach after stop asks for no permission");
   s.stop();check(s.unregistrations==1,"a second stop is a no-op");}
  {UsbDriverService s=new UsbDriverService();
   s.handleUsbDeviceState(new UsbDevice());s.handleUsbDeviceState(new UsbDevice());
   AbstractController first=s.controllers.get(0), second=s.controllers.get(1);
   Thread t=new Thread(()->first.stop());t.start();t.join();
   check(s.controllers.size()==1&&first.stops==1,"a controller removing itself from its input thread leaves the list consistent");
   s.stop();
   check(s.controllers.isEmpty()&&first.stops==1&&second.stops==1,"stop stops each remaining controller exactly once");}
  if(failures!=0)System.exit(1);
 }
}
"""
if not uses_main_handler:
    print('UsbDriverService has no mainHandler; the baseline would claim devices after stop()')
code = prefix + '\n'.join(method(m) for m in ['    private void handleUsbDeviceState(UsbDevice device)', '    private void stop()', '    public void deviceRemoved(AbstractController controller)']).replace('@Override', '') + suffix
with tempfile.TemporaryDirectory(prefix='usb-driver-') as directory:
    p = pathlib.Path(directory)
    (p / 'UsbDriverService.java').write_text(code)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(p), str(p / 'UsbDriverService.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(p), 'UsbDriverService']).returncode)
