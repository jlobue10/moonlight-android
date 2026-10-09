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
 'android/os/Build.java': 'package android.os; public class Build { public static class VERSION { public static int SDK_INT=35; } public static class VERSION_CODES { public static final int M=23; } }',
 'android/os/Looper.java': 'package android.os; public class Looper { public static Looper getMainLooper(){return new Looper();} }',
 'android/os/SystemClock.java': 'package android.os; public class SystemClock { public static long uptimeMillis(){return 1000;} }',
 'android/os/Handler.java': '''package android.os; import java.util.*;
 public class Handler { public final List<Runnable> delayed=new ArrayList<>(); public Handler(Looper l){}
 public boolean post(Runnable r){r.run();return true;} public boolean postDelayed(Runnable r,long delay){delayed.add(r);return true;}
 public void removeCallbacks(Runnable r){delayed.removeIf(x->x==r);} public void removeCallbacksAndMessages(Object o){delayed.clear();} }''',
 'android/content/Context.java': '''package android.content; public class Context { public static final int MODE_PRIVATE=0;
 public Context getApplicationContext(){return this;} public SharedPreferences getSharedPreferences(String n,int m){return new SharedPreferences();} }''',
 'android/content/SharedPreferences.java': '''package android.content; public class SharedPreferences {
 public float getFloat(String k,float d){return d;} public Editor edit(){return new Editor();}
 public static class Editor { public Editor putFloat(String k,float v){return this;} public void apply(){} } }''',
 'android/bluetooth/BluetoothProfile.java': 'package android.bluetooth; public interface BluetoothProfile { int STATE_CONNECTED=2, STATE_DISCONNECTED=0; }',
 'android/bluetooth/BluetoothDevice.java': '''package android.bluetooth; import android.content.Context; public class BluetoothDevice {
 public static final int TRANSPORT_LE=2; public BluetoothGatt next = new BluetoothGatt(); public String getAddress(){return "00:00:00:00:00:01";}
 public BluetoothGatt connectGatt(Context c,boolean a,BluetoothGattCallback cb){return next;}
 public BluetoothGatt connectGatt(Context c,boolean a,BluetoothGattCallback cb,int transport){return next;} }''',
 'android/bluetooth/BluetoothGatt.java': '''package android.bluetooth; import java.util.*; public class BluetoothGatt {
 public static final int GATT_SUCCESS=0, CONNECTION_PRIORITY_HIGH=1; public boolean acceptDescriptor=true, closed;
 public BluetoothGattService service=new BluetoothGattService(); public List<byte[]> writes=new ArrayList<>();
 public boolean requestConnectionPriority(int p){return true;} public boolean requestMtu(int m){return true;}
 public boolean discoverServices(){return true;} public BluetoothGattService getService(UUID u){return service;}
 public boolean setCharacteristicNotification(BluetoothGattCharacteristic c,boolean b){return true;}
 public boolean writeDescriptor(BluetoothGattDescriptor d){return acceptDescriptor;}
 public boolean writeCharacteristic(BluetoothGattCharacteristic c){writes.add(c.value.clone());return true;}
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
 static SteamControllerBle make()throws Exception{
  SteamControllerBle d=new SteamControllerBle(1,new UsbDriverListener(){
   public void reportControllerState(int i,int b,float x,float y,float z,float w,float l,float r){states++;}
   public void reportControllerMotion(int i,byte t,float x,float y,float z){}
   public void deviceRemoved(AbstractController c){} public void deviceAdded(AbstractController c){added++;}
  },new Context(),new BluetoothDevice(),false,false,0,0,0,false);d.start();return d;
 }
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
  System.out.println(checks+" checks, "+failed+" failures");if(failed!=0)System.exit(1);
 }
}'''

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
