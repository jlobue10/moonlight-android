#!/usr/bin/env python3
"""Run the real BLE driver against deterministic Android boundary fakes (JDK 17+).

No device, SDK, Bluetooth connection, or native library is used. These tests cover
queue/lifecycle behavior; they do not validate radio timing or firmware support.
"""
import pathlib
import re
import subprocess
import tempfile
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
JAVA = ROOT / 'app/src/main/java'
STUBS = {
 'android/annotation/SuppressLint.java': 'package android.annotation; public @interface SuppressLint { String[] value(); }',
 'android/os/Build.java': 'package android.os; public class Build { public static class VERSION { public static int SDK_INT=35; } public static class VERSION_CODES { public static final int M=23, S=31, TIRAMISU=33; } }',
 'android/content/BroadcastReceiver.java': 'package android.content; public abstract class BroadcastReceiver { public abstract void onReceive(Context c,Intent i); }',
 'android/content/Intent.java': '''package android.content; public class Intent { public String action; public Object extra; public int state=-1;
 public Intent(String a){action=a;} public String getAction(){return action;} @SuppressWarnings("unchecked") public <T> T getParcelableExtra(String k){return (T)extra;}
 public int getIntExtra(String k,int d){return state<0?d:state;} }''',
 'android/content/IntentFilter.java': 'package android.content; public class IntentFilter { public IntentFilter(String a){} public void addAction(String a){} }',
 'android/bluetooth/BluetoothManager.java': 'package android.bluetooth; public class BluetoothManager { public BluetoothAdapter adapter=new BluetoothAdapter(); public BluetoothAdapter getAdapter(){return adapter;} }',
 'android/bluetooth/BluetoothAdapter.java': '''package android.bluetooth; import java.util.*; public class BluetoothAdapter {
 public static final String ACTION_STATE_CHANGED="android.bluetooth.adapter.action.STATE_CHANGED", EXTRA_STATE="android.bluetooth.adapter.extra.STATE"; public static final int STATE_ON=12;
 public boolean enabled=true, deny; public Set<BluetoothDevice> bonded=new HashSet<>();
 public boolean isEnabled(){return enabled;} public Set<BluetoothDevice> getBondedDevices(){if(deny)throw new SecurityException("policy");return bonded;} }''',
 'android/os/Looper.java': 'package android.os; public class Looper { public static Looper getMainLooper(){return new Looper();} }',
 'android/os/HandlerThread.java': '''package android.os; public class HandlerThread extends Thread { public static int quits;
 public HandlerThread(String n){} public HandlerThread(String n,int p){} public Looper getLooper(){return new Looper();}
 public boolean quitSafely(){++quits;return true;} }''',
 'android/os/Process.java': 'package android.os; public class Process { public static final int THREAD_PRIORITY_URGENT_DISPLAY=-8; }',
 'android/os/SystemClock.java': 'package android.os; public class SystemClock { public static long now=1000; public static long uptimeMillis(){return now;} }',
 'android/os/Handler.java': '''package android.os; import java.util.*;
 public class Handler { public final List<Runnable> delayed=new ArrayList<>(); public Handler(Looper l){}
 public boolean post(Runnable r){r.run();return true;} public boolean postDelayed(Runnable r,long delay){delayed.add(r);return true;}
 public void removeCallbacks(Runnable r){delayed.removeIf(x->x==r);} public void removeCallbacksAndMessages(Object o){delayed.clear();} }''',
 'android/content/Context.java': '''package android.content; import android.bluetooth.*; public class Context { public static final int MODE_PRIVATE=0, RECEIVER_EXPORTED=2; public static final String BLUETOOTH_SERVICE="bluetooth";
 public static int registrations, unregistrations; public static BroadcastReceiver receiver; public static BluetoothManager manager=new BluetoothManager();
 public Context getApplicationContext(){return this;} public SharedPreferences getSharedPreferences(String n,int m){return new SharedPreferences();}
 public Object getSystemService(String n){return manager;}
 public Intent registerReceiver(BroadcastReceiver r,IntentFilter f){receiver=r;++registrations;return null;}
 public Intent registerReceiver(BroadcastReceiver r,IntentFilter f,int flags){return registerReceiver(r,f);}
 public void unregisterReceiver(BroadcastReceiver r){if(receiver!=r)throw new IllegalArgumentException("not registered");receiver=null;++unregistrations;} }''',
 'android/content/SharedPreferences.java': '''package android.content; public class SharedPreferences {
 public float getFloat(String k,float d){return d;} public Editor edit(){return new Editor();}
 public static class Editor { public static Runnable onApply; public Editor putFloat(String k,float v){return this;}
  public void apply(){if(onApply!=null){Runnable r=onApply;onApply=null;r.run();}} } }''',
 'android/bluetooth/BluetoothProfile.java': 'package android.bluetooth; public interface BluetoothProfile { int STATE_CONNECTED=2, STATE_DISCONNECTED=0; }',
 'android/bluetooth/BluetoothDevice.java': '''package android.bluetooth; import android.content.Context; public class BluetoothDevice {
 public static final int TRANSPORT_LE=2; public static final String ACTION_ACL_CONNECTED="android.bluetooth.device.action.ACL_CONNECTED", EXTRA_DEVICE="android.bluetooth.device.extra.DEVICE";
 public boolean deny; public Runnable onConnect; public BluetoothGatt next = new BluetoothGatt(); public String address="00:00:00:00:00:01", name="Steam Controller";
 public String getAddress(){return address;} public String getName(){return name;}
 public BluetoothGatt connectGatt(Context c,boolean a,BluetoothGattCallback cb){if(deny)throw new SecurityException();if(onConnect!=null){Runnable r=onConnect;onConnect=null;r.run();}return next;}
 public BluetoothGatt connectGatt(Context c,boolean a,BluetoothGattCallback cb,int transport){return connectGatt(c,a,cb);} }''',
 'android/bluetooth/BluetoothGatt.java': '''package android.bluetooth; import java.util.*; public class BluetoothGatt {
 public static final int GATT_SUCCESS=0, CONNECTION_PRIORITY_HIGH=1; public boolean acceptDescriptor=true, acceptWrite=true, closed;
 public BluetoothGattService service=new BluetoothGattService(); public List<byte[]> writes=new ArrayList<>();
 public boolean requestConnectionPriority(int p){return true;} public boolean requestMtu(int m){return true;}
 public boolean discoverServices(){return true;} public BluetoothGattService getService(UUID u){return service;}
 public boolean setCharacteristicNotification(BluetoothGattCharacteristic c,boolean b){return true;}
 public boolean writeDescriptor(BluetoothGattDescriptor d){return acceptDescriptor;}
 public boolean writeCharacteristic(BluetoothGattCharacteristic c){if(!acceptWrite)return false;writes.add(c.value.clone());return true;}
 public boolean readCharacteristic(BluetoothGattCharacteristic c){return true;}
 public void close(){closed=true;} public void disconnect(){} }''',
 'android/bluetooth/BluetoothGattService.java': '''package android.bluetooth; import java.util.*; public class BluetoothGattService {
 public List<BluetoothGattCharacteristic> chars=new ArrayList<>(); public List<BluetoothGattCharacteristic> getCharacteristics(){return chars;} }''',
 'android/bluetooth/BluetoothGattCharacteristic.java': '''package android.bluetooth; import java.util.*; public class BluetoothGattCharacteristic {
 public static final int PROPERTY_NOTIFY=16, PROPERTY_WRITE=8, PROPERTY_WRITE_NO_RESPONSE=4, WRITE_TYPE_NO_RESPONSE=1, WRITE_TYPE_DEFAULT=2;
 public UUID uuid; public int props; public byte[] value; public BluetoothGattDescriptor descriptor;
 public BluetoothGattCharacteristic(String u,int p){uuid=UUID.fromString(u);props=p;}
 public UUID getUuid(){return uuid;} public int getProperties(){return props;} public BluetoothGattDescriptor getDescriptor(UUID u){return descriptor;}
 public void setWriteType(int t){} public boolean setValue(byte[] v){value=v;return true;} public byte[] getValue(){return value;} }''',
 'android/bluetooth/BluetoothGattDescriptor.java': '''package android.bluetooth; public class BluetoothGattDescriptor {
 public static final byte[] ENABLE_NOTIFICATION_VALUE={1,0}; public BluetoothGattCharacteristic ch;
 public BluetoothGattDescriptor(BluetoothGattCharacteristic c){ch=c;} public BluetoothGattCharacteristic getCharacteristic(){return ch;}
 public boolean setValue(byte[] b){return true;} }''',
 'android/bluetooth/BluetoothGattCallback.java': '''package android.bluetooth; public class BluetoothGattCallback {
 public void onConnectionStateChange(BluetoothGatt g,int s,int n){} public void onMtuChanged(BluetoothGatt g,int m,int s){}
 public void onServicesDiscovered(BluetoothGatt g,int s){} public void onDescriptorWrite(BluetoothGatt g,BluetoothGattDescriptor d,int s){}
 public void onCharacteristicWrite(BluetoothGatt g,BluetoothGattCharacteristic c,int s){}
 public void onCharacteristicRead(BluetoothGatt g,BluetoothGattCharacteristic c,int s){}
 public void onCharacteristicChanged(BluetoothGatt g,BluetoothGattCharacteristic c){} }''',
 'com/limelight/LimeLog.java': 'package com.limelight; public class LimeLog { public static void info(String s){} public static void warning(String s){} }',
}
TEST = r'''import java.lang.reflect.*; import java.util.*; import android.bluetooth.*; import android.content.*; import android.os.*;
import com.limelight.binding.input.driver.*; import com.limelight.binding.input.driver.ble.*;
public class BleRegression {
 static int failed,checks,states,added;
 static void check(boolean ok,String name){checks++;System.out.println((ok?"PASS ":"FAIL ")+name);if(!ok)failed++;}
 static Object get(Object o,String name)throws Exception{Field f=o.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(o);}
 static void set(Object o,String name,Object v)throws Exception{Field f=o.getClass().getDeclaredField(name);f.setAccessible(true);f.set(o,v);}
 static SteamControllerBle create(BluetoothDevice device)throws Exception{
  SteamControllerBle d=new SteamControllerBle(1,new UsbDriverListener(){
   public void reportControllerState(int i,int b,float x,float y,float z,float w,float l,float r){states++;}
   public void reportControllerMotion(int i,byte t,float x,float y,float z){}
   public void deviceRemoved(AbstractController c){} public void deviceAdded(AbstractController c){added++;}
  },new Context(),device,false,false,0,0,0,false);return d;
 }
 static SteamControllerBle make()throws Exception{SteamControllerBle d=create(new BluetoothDevice());d.start();return d;}
 static BluetoothGattCharacteristic command(){return new BluetoothGattCharacteristic("100f6cb5-1735-4313-b402-38567131e5f3",8);}
 public static void main(String[] args)throws Exception{
  SteamControllerBle d=make();set(d,"writeChar",command());set(d,"writeBusy",true);
  for(int i=0;i<1000;i++)d.steamHaptic(new byte[]{(byte)0x81,(byte)(i%2),0,0,0,0,0,0});
  check(((Deque<?>)get(d,"writeQueue")).size()<=32,"stalled link has a bounded queue even for motors-off floods");
  d=make();BluetoothGatt old=(BluetoothGatt)get(d,"gatt");BluetoothGatt fresh=new BluetoothGatt();
  set(d,"gatt",fresh);set(d,"writeChar",command());set(d,"writeBusy",true);
  ((BluetoothGattCallback)get(d,"gattCallback")).onCharacteristicWrite(old,command(),0);
  check((boolean)get(d,"writeBusy"),"stale GATT completion cannot unlock a new connection's write");
  d=make();old=(BluetoothGatt)get(d,"gatt");set(d,"writeChar",command());d.rumble((short)10000,(short)10000);
  ((BluetoothGattCallback)get(d,"gattCallback")).onConnectionStateChange(old,0,0);
  check(get(d,"writeChar")==null && !(boolean)get(d,"rumbleActive"),"disconnect discards stale characteristic and infinite rumble");
  ((Runnable)get(d,"rumbleRefresh")).run();
  check(((Deque<?>)get(d,"writeQueue")).isEmpty(),"disconnected rumble does not enqueue old-session commands");
  d=make();old=(BluetoothGatt)get(d,"gatt");d.stop();int before=states;
  BluetoothGattCharacteristic notify=new BluetoothGattCharacteristic("100f6c75-1735-4313-b402-38567131e5f3",16);notify.value=new byte[45];
  ((BluetoothGattCallback)get(d,"gattCallback")).onCharacteristicChanged(old,notify);
  check(states==before,"late notifications after stop cannot resurrect controller input");
  d=make();old=(BluetoothGatt)get(d,"gatt");old.service.chars.add(command());old.service.chars.add(notify);before=added;
  ((BluetoothGattCallback)get(d,"gattCallback")).onServicesDiscovered(old,0);
  check(added==before+1,"subscriptions without CCCDs still complete initialization");
  d=make();old=(BluetoothGatt)get(d,"gatt");old.acceptDescriptor=false;
  notify.descriptor=new BluetoothGattDescriptor(notify);old.service.chars.add(command());old.service.chars.add(notify);
  ((BluetoothGattCallback)get(d,"gattCallback")).onServicesDiscovered(old,0);
  check(old.closed,"synchronously rejected subscription does not wait forever for a callback");
  d=make();old=(BluetoothGatt)get(d,"gatt");set(d,"writeChar",command());set(d,"writeBusy",true);
  ((Runnable)get(d,"writeWatchdog")).run();
  check(old.closed && get(d,"gatt")==null,"write timeout retires its GATT generation before another write");
  d=make();old=(BluetoothGatt)get(d,"gatt");old.service.chars.add(command());old.service.chars.add(notify);before=added;
  BluetoothGattCallback callback=(BluetoothGattCallback)get(d,"gattCallback");
  callback.onServicesDiscovered(old,0);
  callback.onDescriptorWrite(old,notify.descriptor,133);
  check(old.closed && added==before,"all failed notification subscriptions cannot announce a ready controller");
  d=make();old=(BluetoothGatt)get(d,"gatt");old.service.chars.add(command());old.service.chars.add(notify);
  BluetoothGattCharacteristic second=new BluetoothGattCharacteristic("100f6c76-1735-4313-b402-38567131e5f3",16);
  second.descriptor=new BluetoothGattDescriptor(second);old.service.chars.add(second);before=added;
  callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
  callback.onDescriptorWrite(old,notify.descriptor,133);callback.onDescriptorWrite(old,second.descriptor,0);
  check(!old.closed && added==before+1,"an optional subscription failure still permits a working notification channel");
  d=make();old=(BluetoothGatt)get(d,"gatt");set(d,"writeChar",command());old.acceptWrite=false;
  d.rumble((short)10000,(short)10000);
  for(int i=0;i<45;i++){SystemClock.now+=50;((Runnable)get(d,"retryFlush")).run();}
  check(old.closed && get(d,"gatt")==null && ((Deque<?>)get(d,"writeQueue")).isEmpty(),
        "persistent synchronous write rejection retires the unusable link");
  d=make();old=(BluetoothGatt)get(d,"gatt");set(d,"writeChar",command());old.acceptWrite=false;
  d.rumble((short)10000,(short)10000);SystemClock.now+=50;old.acceptWrite=true;
  ((Runnable)get(d,"retryFlush")).run();
  check(!old.closed && old.writes.size()==1 && (boolean)get(d,"writeBusy"),
        "a transient busy response retries without dropping the controller");
  // A zero-duration pulse is a stop even with a nonzero repeat count. It must
  // survive the final BLE queue just as it does the driver/host/native queues.
  d=make();set(d,"writeChar",command());set(d,"writeBusy",true);
  d.steamHaptic(new byte[]{(byte)0x81,1,0,0,20,0,5,0});
  for(int i=0;i<1000;i++)d.steamHaptic(new byte[]{(byte)0x81,0,10,0,20,0,5,0});
  boolean silentPulse=false;
  for(Object item:(Deque<?>)get(d,"writeQueue")){
   byte[] cmd=(byte[])item;
   silentPulse |= (cmd[0]&255)==0x8f && cmd[2]==1 && cmd[3]==0 && cmd[4]==0;
  }
  check(silentPulse && ((Deque<?>)get(d,"writeQueue")).size()<=32,
        "zero-duration stop survives BLE effect overload with nonzero repeat count");

  d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onConnectionStateChange(old,0,0);
  before=HandlerThread.quits;d.stop();check(HandlerThread.quits==before+1,"stop after disconnect quits the handler thread even without GATT");
  for(boolean denied:new boolean[]{false,true}){
   BluetoothDevice device=new BluetoothDevice();device.next=null;device.deny=denied;before=HandlerThread.quits;
   new ManagerHarness().start(device);
   check(HandlerThread.quits==before+1,"failed initial connection releases its unregistered driver thread; denied="+denied);
  }
  BluetoothDevice connecting=new BluetoothDevice();d=create(connecting);SteamControllerBle target=d;
  connecting.onConnect=target::stop;before=HandlerThread.quits;boolean started=d.start();
  check(!started&&get(d,"gatt")==null&&connecting.next.closed&&HandlerThread.quits==before+1,
        "stop during connect rejects and closes the late GATT result");
  check(!d.start()&&get(d,"gatt")==null,"a stopped driver cannot restart on its retired looper");

  // Firmware may deliver a state before all subscriptions complete. The listener
  // has no controller context until onReady announces it, so that state is ignored.
  d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");
  notify=new BluetoothGattCharacteristic("100f6c75-1735-4313-b402-38567131e5f3",16);
  notify.value=new byte[45];notify.value[1]=1; // A held
  callback.onCharacteristicChanged(old,notify);
  old.service.chars.add(command());old.service.chars.add(notify);
  callback.onServicesDiscovered(old,0);before=states;
  callback.onCharacteristicChanged(old,notify);
  check(states==before+1,"first state after announcement is delivered even if seen during subscription setup");
  before=states;
  for(int i=0;i<250;i++)callback.onCharacteristicChanged(old,notify);
  check(states==before,"250 identical states remain deduplicated within a live connection");

  callback.onConnectionStateChange(old,0,0);
  ((BluetoothDevice)get(d,"device")).next=new BluetoothGatt();d.start();
  fresh=(BluetoothGatt)get(d,"gatt");fresh.service.chars.clear();
  fresh.service.chars.add(command());fresh.service.chars.add(notify);
  callback.onServicesDiscovered(fresh,0);before=states;
  callback.onCharacteristicChanged(fresh,notify);
  check(states==before+1,"reconnect resends a held state identical to the previous connection's last state");
  before=states;notify.value[1]=0;callback.onCharacteristicChanged(fresh,notify);
  check(states==before+1,"button release remains observable after reconnect and deduplication");
  // A write completing while stop() runs must still drive the restore writes: the
  // completion used to be dropped between `stopped` and `closing`, so the motors-off and
  // default-mapping/settings restores never reached the controller.
  d=make();old=(BluetoothGatt)get(d,"gatt");set(d,"writeChar",command());set(d,"writeBusy",true);
  d.stop();callback=(BluetoothGattCallback)get(d,"gattCallback");
  for(int i=0;i<3;i++)callback.onCharacteristicWrite(old,command(),0);
  byte defaultMappings=(byte)get(d,"ID_SET_DEFAULT_DIGITAL_MAPPINGS"),loadDefaults=(byte)get(d,"ID_LOAD_DEFAULT_SETTINGS");
  check(old.writes.size()==3&&old.writes.get(1)[0]==defaultMappings&&old.writes.get(2)[0]==loadDefaults,
        "a write completing during stop still drains the motors-off and restore writes");
  // A completion landing while stop() is still running (here: from inside the stick-extent
  // save) used to see `closing` with an empty queue, close the link and null the GATT before
  // the restore commands were queued; the motors-off and restores never went out.
  d=make();old=(BluetoothGatt)get(d,"gatt");set(d,"writeChar",command());set(d,"writeBusy",true);set(d,"extentsDirty",true);
  callback=(BluetoothGattCallback)get(d,"gattCallback");
  {final BluetoothGattCallback cb=callback;final BluetoothGatt g=old;
   SharedPreferences.Editor.onApply=()->cb.onCharacteristicWrite(g,command(),0);}
  d.stop();
  check(SharedPreferences.Editor.onApply==null&&old.writes.size()==1&&!old.closed,
        "a completion landing inside stop() writes the motors-off instead of closing the link");
  for(int i=0;i<2;i++)callback.onCharacteristicWrite(old,command(),0);
  check(old.writes.size()==3&&old.writes.get(1)[0]==defaultMappings&&old.writes.get(2)[0]==loadDefaults&&!old.closed,
        "the restore writes drain behind the motors-off");
  callback.onCharacteristicWrite(old,command(),0);
  check(old.closed,"the link closes from the last restore completion");
  // The manager must watch for controllers even when Bluetooth is off (or the bonded list
  // is refused) at stream start: a controller switched on later, or the adapter turned on,
  // is picked up through the receiver, which used to be registered only after enumeration.
  {ManagerHarness m=new ManagerHarness();Context.manager.adapter.enabled=false;Context.manager.adapter.bonded.clear();Context.registrations=0;Context.receiver=null;
   m.start();check(Context.registrations==1&&Context.receiver!=null&&m.drivers.isEmpty(),"the receiver is registered while Bluetooth is off");
   Context.manager.adapter.enabled=true;Context.manager.adapter.bonded.add(new BluetoothDevice());
   Intent on=new Intent(BluetoothAdapter.ACTION_STATE_CHANGED);on.state=BluetoothAdapter.STATE_ON;Context.receiver.onReceive(m.context,on);
   check(m.drivers.size()==1,"the adapter turning on enumerates bonded controllers");
   m.start();check(Context.registrations==1&&m.drivers.size()==1,"a second start neither re-registers nor duplicates drivers");
   m.stop();check(Context.receiver==null&&m.drivers.isEmpty(),"stop unregisters the receiver and stops the drivers");}
  {ManagerHarness m=new ManagerHarness();Context.manager.adapter.enabled=true;Context.manager.adapter.deny=true;Context.manager.adapter.bonded.clear();Context.registrations=0;Context.receiver=null;
   m.start();check(Context.registrations==1&&Context.receiver!=null,"the receiver is registered when the bonded list is refused");
   Context.manager.adapter.deny=false;BluetoothDevice later=new BluetoothDevice();later.address="00:00:00:00:00:02";
   Intent acl=new Intent(BluetoothDevice.ACTION_ACL_CONNECTED);acl.extra=later;Context.receiver.onReceive(m.context,acl);
   check(m.drivers.size()==1,"a later ACL connection starts the driver");
   BluetoothDevice other=new BluetoothDevice();other.name="Keyboard";acl.extra=other;Context.receiver.onReceive(m.context,acl);
   check(m.drivers.size()==1,"an ACL connection from another device is ignored");m.stop();}
  System.out.println(checks+" checks, "+failed+" failures");if(failed!=0)System.exit(1);
 }
}'''

manager_path='app/src/main/java/com/limelight/binding/input/driver/ble/SteamControllerBleManager.java'
manager=(subprocess.check_output(['git','show','HEAD:'+manager_path],cwd=ROOT,text=True)
         if '--baseline' in sys.argv else (ROOT/manager_path).read_text())
def manager_block(marker):
    start=manager.index(marker);end=manager.index('{',start);depth=1
    while depth:
        end+=1;depth+=(manager[end]=='{')-(manager[end]=='}')
    return manager[start:end+1]
manager_methods=[manager_block('    private void startDriver('), manager_block('    public synchronized void start()'),
                 manager_block('    public synchronized void stop()'), manager_block('    public static boolean looksLikeSteamController(')]
if '    private void enumerateBonded()' in manager:
    manager_methods.append(manager_block('    private void enumerateBonded()'))
STUBS['com/limelight/binding/input/driver/ble/ManagerHarness.java'] = """package com.limelight.binding.input.driver.ble;
import java.util.*;import android.bluetooth.*;import android.content.*;import android.os.Build;import com.limelight.LimeLog;import com.limelight.binding.input.driver.*;
public class ManagerHarness {
 private static final String[] NAME_HINTS = {"steam controller", "steam ctrl", "steamcontroller"};
 public final Map<String,SteamControllerBle> drivers=new HashMap<>();int nextDeviceId;
 public Context context=new Context();UsbDriverListener listener;boolean motionEnabled,splitPads,stickRim;
 int gripsMode,rumbleHoldMs,rumbleMethod;BroadcastReceiver aclReceiver;
 static boolean hasPermission(Context c){return true;}
 public void start(BluetoothDevice d){startDriver(d);}
""" + '\n'.join(manager_methods).replace('SteamControllerBleManager.this', 'ManagerHarness.this') + '}'

with tempfile.TemporaryDirectory(prefix='ble-regression-') as directory:
    work = pathlib.Path(directory)
    constants = re.findall(r'public static final (?:byte|short|int) LI_(?:CCAP|CTYPE|TOUCH_EVENT|BATTERY_STATE|MOTION_TYPE)[^;]+;', (JAVA/'com/limelight/nvstream/jni/MoonBridge.java').read_text())
    STUBS['com/limelight/nvstream/jni/MoonBridge.java'] = 'package com.limelight.nvstream.jni; public class MoonBridge {' + '\n'.join(constants) + '}'
    STUBS['BleRegression.java'] = TEST
    baseline = '--baseline' in sys.argv
    if baseline:
        STUBS['com/limelight/binding/input/driver/ble/SteamControllerBle.java'] = subprocess.check_output(
            ['git','show','HEAD:app/src/main/java/com/limelight/binding/input/driver/ble/SteamControllerBle.java'],cwd=ROOT,text=True)
    for name, source in STUBS.items():
        path = work/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    sources = list(work.rglob('*.java')) + [JAVA/p for p in (
        'com/limelight/binding/input/driver/AbstractController.java',
        'com/limelight/binding/input/driver/UsbDriverListener.java',
        'com/limelight/nvstream/input/ControllerPacket.java')]
    if not baseline:
        sources.append(JAVA/'com/limelight/binding/input/driver/ble/SteamControllerBle.java')
    subprocess.run(['java','com.sun.tools.javac.Main','-d',str(work/'classes'),*map(str,sources)],check=True)
    raise SystemExit(subprocess.run(['java','-cp',str(work/'classes'),'BleRegression']).returncode)
