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
 static int failed,checks,states,added,batteryState=-1,batteryPercent=-1;
 static void check(boolean ok,String name){checks++;System.out.println((ok?"PASS ":"FAIL ")+name);if(!ok)failed++;}
 static Field field(Class<?> c,String name)throws Exception{for(;c!=null;c=c.getSuperclass()){try{return c.getDeclaredField(name);}catch(NoSuchFieldException e){}}throw new NoSuchFieldException(name);}
 static Object get(Object o,String name)throws Exception{Field f=field(o.getClass(),name);f.setAccessible(true);return f.get(o);}
 static void set(Object o,String name,Object v)throws Exception{Field f=field(o.getClass(),name);f.setAccessible(true);f.set(o,v);}
 static Object tryGet(Object o,String name){try{return get(o,name);}catch(Exception e){return null;}}
 static void battery(SteamControllerBle d,int chargeState,int percent)throws Exception{
  Method m=null;for(Class<?> c=d.getClass();c!=null;c=c.getSuperclass()){try{m=c.getDeclaredMethod("handleBattery",byte[].class);break;}catch(NoSuchMethodException ignored){}}m.setAccessible(true);
  batteryState=-1;batteryPercent=-1;m.invoke(d,(Object)new byte[]{(byte)chargeState,(byte)percent});}
 static SteamControllerBle create(BluetoothDevice device)throws Exception{
  SteamControllerBle d=new SteamControllerBle(1,new UsbDriverListener(){
   public void reportControllerState(int i,int b,float x,float y,float z,float w,float l,float r){states++;}
   public void reportControllerMotion(int i,byte t,float x,float y,float z){}
   public void deviceRemoved(AbstractController c){} public void deviceAdded(AbstractController c){added++;}
   public void reportControllerBattery(int i,byte s,byte p){batteryState=s;batteryPercent=p;}
  },new Context(),device,false,false,0,0,0,false);return d;
 }
 static SteamControllerBle make()throws Exception{SteamControllerBle d=create(new BluetoothDevice());d.start();return d;}
 static BluetoothGattCharacteristic command(){return new BluetoothGattCharacteristic("100f6c34-1735-4313-b402-38567131e5f3",8);}
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
  BluetoothGattCharacteristic notify=new BluetoothGattCharacteristic("100f6c7a-1735-4313-b402-38567131e5f3",16);notify.value=new byte[45];
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
  BluetoothGattCharacteristic second=new BluetoothGattCharacteristic("100f6c78-1735-4313-b402-38567131e5f3",16);
  second.descriptor=new BluetoothGattDescriptor(second);old.service.chars.add(second);before=added;
  callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
  callback.onDescriptorWrite(old,notify.descriptor,0);callback.onDescriptorWrite(old,second.descriptor,133);
  check(!old.closed && added==before+1,"an optional subscription failure still permits a working notification channel");
  // Actual Valve service: B5 is rumble output, 34 is the feature-command endpoint.
  d=make();old=(BluetoothGatt)get(d,"gatt");
  BluetoothGattCharacteristic output=new BluetoothGattCharacteristic("100f6cb5-1735-4313-b402-38567131e5f3",8);
  BluetoothGattCharacteristic feature=command();notify.descriptor=null;
  old.service.chars.add(output);old.service.chars.add(feature);old.service.chars.add(notify);
  callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
  check(get(d,"writeChar")==feature && output.value==null && feature.value!=null,
        "setup uses feature characteristic 34 even when rumble output B5 appears first");
  check(feature.value!=null && feature.value.length>=2 && feature.value[0]==(byte)0x81 && feature.value[1]==0,
        "clear mappings includes the feature command and zero payload length without a HID report ID");
  callback.onCharacteristicWrite(old,feature,0);
  check(feature.value!=null && feature.value[0]==(byte)0x87 && feature.value[1]==9,
        "lizard/watchdog/IMU settings use feature framing on characteristic 34");
  d=make();old=(BluetoothGatt)get(d,"gatt");old.service.chars.add(output);old.service.chars.add(notify);before=added;
  ((BluetoothGattCallback)get(d,"gattCallback")).onServicesDiscovered(old,0);
  check(!old.closed && added==before+1 && get(d,"writeChar")==output,
        "firmware without feature characteristic 34 falls back to the B5..BE command endpoint");
  check(output.value!=null && output.value[0]==(byte)0x81,"fallback endpoint still receives the setup commands");
  // Firmware exposing neither 7A nor 7C: full-size reports on the 75..7A range still decode
  // (fork.92 behaviour), while battery/ping and short reports do not.
  d=make();old=(BluetoothGatt)get(d,"gatt");
  BluetoothGattCharacteristic legacy=new BluetoothGattCharacteristic("100f6c75-1735-4313-b402-38567131e5f3",16);
  BluetoothGattCharacteristic legacy2=new BluetoothGattCharacteristic("100f6c76-1735-4313-b402-38567131e5f3",16);
  old.service.chars.add(output);old.service.chars.add(legacy);old.service.chars.add(legacy2);before=added;
  callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
  check(!old.closed && added==before+1 && get(d,"stateChar")==null,"legacy notify range without 7A/7C still announces a controller");
  before=states;legacy2.value=new byte[45];legacy2.value[1]=1;callback.onCharacteristicChanged(old,legacy2);
  check(states==before+1,"legacy range decodes a full-size report from any notify characteristic");
  legacy2.value=new byte[8];callback.onCharacteristicChanged(old,legacy2);
  check(states==before+1,"legacy range ignores short acknowledgement reports");
  d=make();old=(BluetoothGatt)get(d,"gatt");old.service.chars.add(output);before=added;
  ((BluetoothGattCallback)get(d,"gattCallback")).onServicesDiscovered(old,0);
  check(old.closed && added==before,"a service without any input characteristic cannot announce a controller");
  // With 7A present the range is still subscribed (acks/pings stay visible) but only 7A decodes.
  d=make();old=(BluetoothGatt)get(d,"gatt");old.service.chars.add(command());old.service.chars.add(legacy);old.service.chars.add(notify);before=added;
  callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
  check(!old.closed && added==before+1 && get(d,"stateChar")==notify,"7A is selected over the legacy range");
  before=states;legacy.value=new byte[45];legacy.value[1]=1;callback.onCharacteristicChanged(old,legacy);
  check(states==before,"a full-size report outside the selected state characteristic is not decoded");
  notify.value=new byte[45];notify.value[1]=1;callback.onCharacteristicChanged(old,notify);
  check(states==before+1,"the selected 7A characteristic decodes");

  // Battery success cannot hide a failed state subscription.
  d=make();old=(BluetoothGatt)get(d,"gatt");notify.descriptor=new BluetoothGattDescriptor(notify);
  old.service.chars.add(command());old.service.chars.add(notify);old.service.chars.add(second);before=added;
  callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
  callback.onDescriptorWrite(old,notify.descriptor,133);callback.onDescriptorWrite(old,second.descriptor,0);
  check(old.closed && added==before,"battery-only subscription cannot announce a working controller");

  // New 0x47 input keeps a 45-byte payload but moves pads past a 16-bit timestamp.
  d=make();old=(BluetoothGatt)get(d,"gatt");notify.descriptor=null;
  BluetoothGattCharacteristic timestamped=new BluetoothGattCharacteristic("100f6c7c-1735-4313-b402-38567131e5f3",16);
  timestamped.value=new byte[45];timestamped.value[1]=1;
  old.service.chars.add(command());old.service.chars.add(notify);old.service.chars.add(timestamped);before=added;
  callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
  check(!old.closed && added==before+1,"new timestamped state characteristic completes setup");
  before=states;callback.onCharacteristicChanged(old,timestamped);
  check(states==before+1,"0x47 Bluetooth buttons reach the controller listener");
  // Bit 25 is left pad touch; timestamp deliberately differs from pad X.
  timestamped.value[4]=2;timestamped.value[17]=0x34;timestamped.value[18]=0x12;
  timestamped.value[19]=0;timestamped.value[20]=0x40;timestamped.value[23]=(byte)0xff;timestamped.value[24]=0x7f;
  callback.onCharacteristicChanged(old,timestamped);
  check(Math.abs(((float[])get(d,"lastTouchX"))[0]-0.7500076f)<0.0001f,
        "0x47 trackpad coordinate skips the sampling timestamp");
  before=states;notify.value=new byte[45];callback.onCharacteristicChanged(old,notify);
  output.value=new byte[45];callback.onCharacteristicChanged(old,output);
  check(states==before,"old and unrelated long reports cannot overwrite selected 0x47 input");
  timestamped.value=new byte[44];callback.onCharacteristicChanged(old,timestamped);
  check(states==before,"truncated state report cannot produce input");
  d=make();old=(BluetoothGatt)get(d,"gatt");old.service.chars.add(command());old.service.chars.add(timestamped);before=added;
  callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
  check(!old.closed && added==before+1,"firmware exposing only 0x47 input is supported");
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
  notify=new BluetoothGattCharacteristic("100f6c7a-1735-4313-b402-38567131e5f3",16);
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
  check(old.writes.size()==3&&old.writes.get(1).length==2&&old.writes.get(1)[1]==0
        &&old.writes.get(2).length==2&&old.writes.get(2)[1]==0,
        "restore feature commands carry zero payload lengths");
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
  // Battery report: byte 0 is the firmware's EChargeState (1 discharging, 2 charging,
  // 3 source validate, 4 done, 0 reset). Non-zero used to mean "charging".
  {d=make();set(d,"announced",true);
   battery(d,1,80);check(batteryState==com.limelight.nvstream.jni.MoonBridge.LI_BATTERY_STATE_DISCHARGING&&batteryPercent==80,"charge state 1 is reported as discharging");
   battery(d,1,100);check(batteryState==com.limelight.nvstream.jni.MoonBridge.LI_BATTERY_STATE_DISCHARGING,"a full battery on battery power is still discharging");
   battery(d,2,50);check(batteryState==com.limelight.nvstream.jni.MoonBridge.LI_BATTERY_STATE_CHARGING,"charge state 2 is charging");
   battery(d,3,90);check(batteryState==com.limelight.nvstream.jni.MoonBridge.LI_BATTERY_STATE_CHARGING,"source validate counts as charging");
   battery(d,4,100);check(batteryState==com.limelight.nvstream.jni.MoonBridge.LI_BATTERY_STATE_FULL,"charge state 4 (done) is full");
   battery(d,0,0);check(batteryState==com.limelight.nvstream.jni.MoonBridge.LI_BATTERY_STATE_UNKNOWN,"charge state 0 (reset) is unknown, not discharging");
   d.stop();}
  // Setup watchdog: an accepted requestMtu whose onMtuChanged never arrives used to leave the
  // link half-open forever (gatt set, nothing announced, manager restart disarmed).
  {d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");
   Handler h=(Handler)get(d,"handler");h.delayed.clear();
   callback.onConnectionStateChange(old,BluetoothGatt.GATT_SUCCESS,BluetoothProfile.STATE_CONNECTED);
   Runnable watchdog=(Runnable)tryGet(d,"setupWatchdog");
   check(watchdog!=null&&h.delayed.contains(watchdog),"a connection arms the setup watchdog");
   if(watchdog!=null){watchdog.run();}
   check(get(d,"gatt")==null&&old.closed,"a setup that never completes recycles the link");
   check(!h.delayed.isEmpty(),"the recycled link schedules a reconnect");
   d.stop();}
  {d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");
   old.service.chars.add(command());old.service.chars.add(notify);Handler h=(Handler)get(d,"handler");
   callback.onConnectionStateChange(old,BluetoothGatt.GATT_SUCCESS,BluetoothProfile.STATE_CONNECTED);
   callback.onServicesDiscovered(old,0);Runnable watchdog=(Runnable)tryGet(d,"setupWatchdog");
   check(watchdog!=null&&!h.delayed.contains(watchdog)&&get(d,"gatt")==old,"a completed setup disarms the watchdog");
   d.stop();}
  {d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");
   callback.onConnectionStateChange(old,133,BluetoothProfile.STATE_CONNECTED);
   check(get(d,"gatt")==null&&old.closed,"CONNECTED with an error status is a lost link, not a half-open one");
   d.stop();}
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
        'com/limelight/binding/input/driver/SteamControllerInput.java',
        'com/limelight/binding/input/driver/UsbDriverListener.java',
        'com/limelight/nvstream/input/ControllerPacket.java')]
    if not baseline:
        sources.append(JAVA/'com/limelight/binding/input/driver/ble/SteamControllerBle.java')
    subprocess.run(['java','com.sun.tools.javac.Main','-d',str(work/'classes'),*map(str,sources)],check=True)
    raise SystemExit(subprocess.run(['java','-cp',str(work/'classes'),'BleRegression']).returncode)
