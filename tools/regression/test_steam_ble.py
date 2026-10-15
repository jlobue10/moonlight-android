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
 public boolean requestConnectionPriority(int p){return true;} public int mtuRequests; public boolean discovered,acceptMtu=true; public Runnable beforeMtuReturn;
 public boolean requestMtu(int m){++mtuRequests;boolean accepted=acceptMtu;if(beforeMtuReturn!=null){Runnable r=beforeMtuReturn;beforeMtuReturn=null;r.run();}return accepted;}
 public boolean discoverServices(){discovered=true;return true;} public BluetoothGattService getService(UUID u){return service;}
 public boolean setCharacteristicNotification(BluetoothGattCharacteristic c,boolean b){return true;}
 public boolean writeDescriptor(BluetoothGattDescriptor d){return acceptDescriptor;}
 public List<BluetoothGattCharacteristic> targets=new ArrayList<>();
 public Runnable beforeWriteReturn;
 public boolean writeCharacteristic(BluetoothGattCharacteristic c){if(!acceptWrite)return false;writes.add(c.value.clone());targets.add(c);
  if(beforeWriteReturn!=null){Runnable r=beforeWriteReturn;beforeWriteReturn=null;r.run();}return true;}
 public boolean readCharacteristic(BluetoothGattCharacteristic c){return true;}
 public void close(){closed=true;} public void disconnect(){} }''',
 'android/bluetooth/BluetoothGattService.java': '''package android.bluetooth; import java.util.*; public class BluetoothGattService {
 public List<BluetoothGattCharacteristic> chars=new ArrayList<>(); public List<BluetoothGattCharacteristic> getCharacteristics(){return chars;} }''',
 'android/bluetooth/BluetoothGattCharacteristic.java': '''package android.bluetooth; import java.util.*; public class BluetoothGattCharacteristic {
 public static final int PROPERTY_NOTIFY=16, PROPERTY_WRITE=8, PROPERTY_WRITE_NO_RESPONSE=4, WRITE_TYPE_NO_RESPONSE=1, WRITE_TYPE_DEFAULT=2;
 public UUID uuid; public int props; public byte[] value; public BluetoothGattDescriptor descriptor;
 public BluetoothGattCharacteristic(String u,int p){uuid=UUID.fromString(u);props=p;}
 public UUID getUuid(){return uuid;} public int getProperties(){return props;} public BluetoothGattDescriptor getDescriptor(UUID u){return descriptor;}
 public int writeType; public java.util.List<Integer> writeTypes=new java.util.ArrayList<>();
 public void setWriteType(int t){writeType=t;} public boolean setValue(byte[] v){value=v;writeTypes.add(writeType);return true;} public byte[] getValue(){return value;} }''',
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
 'com/limelight/LimeLog.java': '''package com.limelight; public class LimeLog {
 public static volatile java.util.function.Consumer<String> warningHook, infoHook;
 public static void info(String s){if(infoHook!=null)infoHook.accept(s);} public static void warning(String s){if(warningHook!=null)warningHook.accept(s);} }''',
}
TEST = r'''import java.lang.reflect.*; import java.util.*; import android.bluetooth.*; import android.content.*; import android.os.*;
import com.limelight.binding.input.driver.*; import com.limelight.binding.input.driver.ble.*;
public class BleRegression {
 static int failed,checks,states,added,batteryState=-1,batteryPercent=-1,touchDowns,touchMoves,touchUps;
 static void check(boolean ok,String name){checks++;System.out.println((ok?"PASS ":"FAIL ")+name);if(!ok)failed++;}
 static Field field(Class<?> c,String name)throws Exception{for(;c!=null;c=c.getSuperclass()){try{return c.getDeclaredField(name);}catch(NoSuchFieldException e){}}throw new NoSuchFieldException(name);}
 static Object get(Object o,String name)throws Exception{Field f=field(o.getClass(),name);f.setAccessible(true);return f.get(o);}
 static void set(Object o,String name,Object v)throws Exception{Field f=field(o.getClass(),name);f.setAccessible(true);f.set(o,v);}
 static Object tryGet(Object o,String name){try{return get(o,name);}catch(Exception e){return null;}}
 static void battery(SteamControllerBle d,int chargeState,int percent)throws Exception{
  Method m=null;for(Class<?> c=d.getClass();c!=null;c=c.getSuperclass()){try{m=c.getDeclaredMethod("handleBattery",byte[].class);break;}catch(NoSuchMethodException ignored){}}m.setAccessible(true);
  batteryState=-1;batteryPercent=-1;m.invoke(d,(Object)new byte[]{(byte)chargeState,(byte)percent});}
 static SteamControllerBle create(BluetoothDevice device)throws Exception{return createWith(device,0,100,0);}
 static SteamControllerBle createWith(BluetoothDevice device,int boostDb,int pulseScale,int masterGainDb)throws Exception{return createWith(device,boostDb,pulseScale,masterGainDb,false);}
 static SteamControllerBle createWith(BluetoothDevice device,int boostDb,int pulseScale,int masterGainDb,boolean selfTest)throws Exception{
  SteamControllerBle d=new SteamControllerBle(1,new UsbDriverListener(){
   public void reportControllerState(int i,int b,float x,float y,float z,float w,float l,float r){states++;}
   public void reportControllerMotion(int i,byte t,float x,float y,float z){}
   public void reportControllerTouch(int i,byte pad,byte type,int pointer,float x,float y,float p){
    if(type==com.limelight.nvstream.jni.MoonBridge.LI_TOUCH_EVENT_DOWN)touchDowns++;else if(type==com.limelight.nvstream.jni.MoonBridge.LI_TOUCH_EVENT_UP)touchUps++;else touchMoves++;}
   public void deviceRemoved(AbstractController c){} public void deviceAdded(AbstractController c){added++;}
   public void reportControllerBattery(int i,byte s,byte p){batteryState=s;batteryPercent=p;}
  },new Context(),device,false,false,0,0,0,boostDb,pulseScale,masterGainDb,selfTest,false);return d;
 }
 static SteamControllerBle make()throws Exception{SteamControllerBle d=create(new BluetoothDevice());d.start();return d;}
 static BluetoothGattCharacteristic command(){return new BluetoothGattCharacteristic("100f6c34-1735-4313-b402-38567131e5f3",8);}
 static void completeOnWorker(BluetoothGattCallback cb,BluetoothGatt g,BluetoothGattCharacteristic ch,int status){
  Thread t=new Thread(()->cb.onCharacteristicWrite(g,ch,status));t.setDaemon(true);t.start();
  try{t.join(3000);}catch(InterruptedException e){throw new AssertionError(e);}
  if(t.isAlive())throw new AssertionError("write completion blocked behind the GATT call");
 }
 static void refuse(BluetoothGattCallback cb,BluetoothGatt g,BluetoothGattCharacteristic ch,int status){
  // A peer can only refuse a Write Request; a Write Command completes locally with GATT_SUCCESS.
  if(status>0&&status<0x80&&ch.writeTypes.get(ch.writeTypes.size()-1)!=BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)
   throw new AssertionError("the harness refused a write-without-response");
  cb.onCharacteristicWrite(g,ch,status);}
 static void await(java.util.concurrent.CountDownLatch latch){
  try{if(!latch.await(3,java.util.concurrent.TimeUnit.SECONDS))throw new AssertionError("callback did not reach its boundary");}
  catch(InterruptedException e){throw new AssertionError(e);}
 }
 static void staleWriteCompletion(boolean afterDrain)throws Exception{
  SteamControllerBle driver=make();BluetoothGatt retired=(BluetoothGatt)get(driver,"gatt");
  BluetoothGattCharacteristic oldCommand=command();set(driver,"writeChar",oldCommand);
  byte[] pulse={(byte)0x81,0,1,0,1,0,1,0};driver.steamHaptic(pulse);
  BluetoothGattCallback cb=(BluetoothGattCallback)get(driver,"gattCallback");
  java.util.concurrent.CountDownLatch entered=new java.util.concurrent.CountDownLatch(1), resume=new java.util.concurrent.CountDownLatch(1);
  if(afterDrain)com.limelight.LimeLog.warningHook=message->{
   if(message.contains("write failed")){entered.countDown();await(resume);}
  };
  Thread completion=new Thread(()->cb.onCharacteristicWrite(retired,oldCommand,afterDrain?133:0));
  completion.setDaemon(true);
  BluetoothGatt replacement=new BluetoothGatt();Object pending;
  try{
   if(afterDrain){completion.start();await(entered);}
   synchronized(driver){
    if(!afterDrain){
     completion.start();long deadline=System.nanoTime()+3_000_000_000L;
     while(completion.getState()!=Thread.State.BLOCKED&&System.nanoTime()<deadline)Thread.yield();
     if(completion.getState()!=Thread.State.BLOCKED)throw new AssertionError("callback did not reach the driver monitor");
    }
    cb.onConnectionStateChange(retired,0,BluetoothProfile.STATE_DISCONNECTED);
    ((BluetoothDevice)get(driver,"device")).next=replacement;driver.start();
    set(driver,"writeChar",command());driver.steamHaptic(pulse);pending=get(driver,"inFlight");
   }
   resume.countDown();completion.join(3000);
   if(completion.isAlive())throw new AssertionError("old write completion did not finish");
   String where=afterDrain?"after draining the retired write":"before taking the driver lock";
   check(pending!=null&&get(driver,"inFlight")==pending&&(boolean)get(driver,"writeBusy"),
         "reconnect "+where+" preserves the replacement's in-flight write");
   check(((Handler)get(driver,"handler")).delayed.contains(get(driver,"writeWatchdog")),
         "reconnect "+where+" preserves the replacement's write watchdog");
   driver.steamHaptic(pulse);
   check(replacement.writes.size()==1,"reconnect "+where+" cannot dispatch overlapping replacement writes");
   cb.onCharacteristicWrite(replacement,(BluetoothGattCharacteristic)get(driver,"writeChar"),0);
   check(replacement.writes.size()==2,"the replacement's own completion drains its queued pulse");
  }finally{
   resume.countDown();com.limelight.LimeLog.warningHook=null;driver.stop();
  }
 }
 public static void main(String[] args)throws Exception{
  staleWriteCompletion(false);staleWriteCompletion(true);
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
   byte[] cmd=(byte[])get(item,"bytes");
   silentPulse |= (cmd[0]&255)==0x8f && cmd[2]==1 && cmd[3]==0 && cmd[4]==0;
  }
  check(silentPulse && ((Deque<?>)get(d,"writeQueue")).size()<=32,
        "zero-duration stop survives BLE effect overload with nonzero repeat count");
  // Haptic output reports go to their own characteristics (B5 = 0x80, B6 = 0x81, B7 = 0x82)
  // without the report id, as the USB transport writes them to the wired unit; Steam's
  // trackpad clicks are 0x81/0x82 reports. The feature conversion (0xEB/0x8F/0xEA on 34),
  // which the firmware refused with GATT status 4 for 0x8F and 0xEA (fork.96 stream log), is
  // only for links without those endpoints (fork.116: a refused output report is never re-framed).
  byte[] click={(byte)0x81,1,(byte)0x90,1,0,0,1,0};
  BluetoothGattCharacteristic outRumble=new BluetoothGattCharacteristic("100f6cb5-1735-4313-b402-38567131e5f3",8);
  BluetoothGattCharacteristic outPulse=new BluetoothGattCharacteristic("100f6cb6-1735-4313-b402-38567131e5f3",12);
  BluetoothGattCharacteristic outCmd=new BluetoothGattCharacteristic("100f6cb7-1735-4313-b402-38567131e5f3",12);
  {d=make();old=(BluetoothGatt)get(d,"gatt");BluetoothGattCharacteristic feat=command();notify.descriptor=null;notify.value=new byte[45];
   old.service.chars.add(outRumble);old.service.chars.add(outPulse);old.service.chars.add(outCmd);old.service.chars.add(feat);old.service.chars.add(notify);
   callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
   check(get(d,"writeChar")==feat&&old.targets.get(0)==feat,"output characteristics do not displace the feature endpoint for setup commands");
   callback.onCharacteristicWrite(old,feat,0);callback.onCharacteristicWrite(old,feat,0);
   check(!(boolean)get(d,"writeBusy")&&old.writes.size()==2,"setup writes drained");
   d.steamHaptic(click);int w=old.writes.size()-1;
   check(w==2&&old.targets.get(w)==outPulse&&Arrays.equals(old.writes.get(w),new byte[]{1,(byte)0x90,1,0,0,1,0}),
         "a 0x81 pulse is written to B6 without its report id");
   callback.onCharacteristicWrite(old,outPulse,0);
   d.steamHaptic(new byte[]{(byte)0x82,0,2,(byte)0xf2});w=old.writes.size()-1;
   check(w==3&&old.targets.get(w)==outCmd&&Arrays.equals(old.writes.get(w),new byte[]{0,2,(byte)0xf2}),
         "a 0x82 command is written to B7 without its report id");
   callback.onCharacteristicWrite(old,outCmd,0);
   d.steamHaptic(new byte[]{(byte)0x80,0,0,0,0x10,0x27,0,0x10,0x27,0});w=old.writes.size()-1;
   check(w==4&&old.targets.get(w)==outRumble&&Arrays.equals(old.writes.get(w),new byte[]{0,0,0,0x10,0x27,0,0x10,0x27,0})&&(boolean)get(d,"rumbleActive"),
         "a 0x80 rumble is written to B5 without its report id and keeps the re-send chain");
   callback.onCharacteristicWrite(old,outRumble,0);
   d.steamHaptic(new byte[]{(byte)0x80,0,0,0,0,0,0,0,0,0});w=old.writes.size()-1;
   check(w==5&&old.targets.get(w)==outRumble&&Arrays.equals(old.writes.get(w),new byte[9])&&!(boolean)get(d,"rumbleActive"),
         "the host's zero 0x80 stops the chain with a zero report on B5");
   callback.onCharacteristicWrite(old,outRumble,0);
   d.rumble((short)20000,(short)0);w=old.writes.size()-1;
   check(w==6&&old.targets.get(w)==outRumble&&old.writes.get(w)[3]==0x20&&old.writes.get(w)[4]==0x4e&&old.writes.get(w)[6]==0,
         "plain host rumble is a 0x80 report on B5");
   callback.onCharacteristicWrite(old,outRumble,0);
   d.steamHaptic(new byte[]{(byte)0x83,1,2,3,4,5,6,7,8,9});
   check(old.writes.size()==7,"a report with neither an offered characteristic nor a feature form is dropped");
   d.stop();w=old.writes.size()-1;
   check(w==7&&old.targets.get(w)==outRumble&&Arrays.equals(old.writes.get(w),new byte[9]),"stop() sends its motors-off as a zero 0x80 on B5");}
  // A stack/link status (0x80 and up, here 133) on a write request switches the link to
  // write-without-response and re-sends; it is not a refusal by the controller.
  {d=make();old=(BluetoothGatt)get(d,"gatt");BluetoothGattCharacteristic feat=command();
   old.service.chars.add(outPulse);old.service.chars.add(feat);old.service.chars.add(notify);
   callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
   callback.onCharacteristicWrite(old,feat,0);callback.onCharacteristicWrite(old,feat,0);
   d.steamHaptic(click);callback.onCharacteristicWrite(old,outPulse,133);int w=old.writes.size()-1;
   check(w==3&&old.targets.get(w)==outPulse&&old.writes.get(w).length==7&&((int[])get(d,"outputRefusals"))[1]==0&&(boolean)get(d,"forceNoResponse"),
         "a link-level write status re-sends without response and counts no refusal");
   callback.onCharacteristicWrite(old,outPulse,0);d.stop();}
  // fork.116: an ATT refusal of a feature command (the firmware answers unknown messages with
  // status 4) is the controller's verdict, not a stack problem, even though the command
  // characteristic offers write-without-response: the command is dropped, the link keeps
  // writing feature commands as write requests, and nothing is re-queued.
  {d=make();old=(BluetoothGatt)get(d,"gatt");BluetoothGattCharacteristic feat=new BluetoothGattCharacteristic("100f6c34-1735-4313-b402-38567131e5f3",12);
   old.service.chars.add(feat);old.service.chars.add(notify);
   callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
   check(old.writes.size()==1&&(old.writes.get(0)[0]&255)==0x81,"setup starts with the clear-mappings command as a write request");
   refuse(callback,old,feat,4);
   check(old.writes.size()==2&&(old.writes.get(1)[0]&255)==0x87&&!(boolean)get(d,"forceNoResponse")
         &&feat.writeTypes.get(1)==BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT&&((Deque<?>)get(d,"writeQueue")).isEmpty(),
         "a feature command refused with ATT error 4 is dropped; the settings write still goes as a write request");
   refuse(callback,old,feat,4);
   check(old.writes.size()==2&&!(boolean)get(d,"writeBusy")&&!(boolean)get(d,"forceNoResponse")&&((Deque<?>)get(d,"writeQueue")).isEmpty(),
         "a refused settings write is not re-queued and leaves the write type alone");
   d.stop();}
  // fork.116: a refusal from the controller (an ATT error, status < 0x80) of an id-less output
  // report is retried once, id-less again and with a response, after the write retry delay; a
  // second refusal drops the report and the framing stays id-less (with the id the firmware
  // accepts and ignores the report, fork.112; the feature forms are refused, fork.96). refuse()
  // throws if the harness were to refuse a Write Command, which no peer can do.
  {d=make();old=(BluetoothGatt)get(d,"gatt");BluetoothGattCharacteristic feat=command();
   BluetoothGattCharacteristic pulse3=new BluetoothGattCharacteristic("100f6cb6-1735-4313-b402-38567131e5f3",14);
   BluetoothGattCharacteristic rumble3=new BluetoothGattCharacteristic("100f6cb5-1735-4313-b402-38567131e5f3",14);
   old.service.chars.add(rumble3);old.service.chars.add(pulse3);old.service.chars.add(feat);old.service.chars.add(notify);
   callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
   callback.onCharacteristicWrite(old,feat,0);callback.onCharacteristicWrite(old,feat,0);
   Handler h3=(Handler)get(d,"handler");Runnable retry3=(Runnable)get(d,"retryFlush");
   d.steamHaptic(click);refuse(callback,old,pulse3,4);
   check(old.writes.size()==3&&h3.delayed.contains(retry3)&&!(boolean)get(d,"writeBusy")&&!((boolean[])get(d,"outputProbed"))[1],
         "a refused output report is not re-sent at once: its retry waits for the write retry delay, as a probe again");
   h3.delayed.remove(retry3);retry3.run();int w=old.writes.size()-1;
   check(w==3&&old.targets.get(w)==pulse3&&Arrays.equals(old.writes.get(w),Arrays.copyOfRange(click,1,click.length))
         &&pulse3.writeTypes.get(1)==BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT,
         "the retry goes id-less again, with a response so the firmware's verdict is logged, never with the report id");
   d.steamHaptic(click); // queued behind the in-flight retry
   refuse(callback,old,pulse3,4);w=old.writes.size()-1;
   check(w==4&&old.targets.get(w)==pulse3&&old.writes.get(w).length==7&&pulse3.writeTypes.get(2)==BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
         &&!h3.delayed.contains(retry3)&&((int[])get(d,"outputRefusals"))[1]==2,
         "a second refusal drops the report; the next queued one goes id-less without response, not as a feature command");
   callback.onCharacteristicWrite(old,pulse3,0);
   d.steamHaptic(click);w=old.writes.size()-1;
   check(w==5&&old.targets.get(w)==pulse3&&old.writes.get(w).length==7,"later reports on that link keep the id-less output endpoint");
   callback.onCharacteristicWrite(old,pulse3,0);
   boolean noFeature=true;for(byte[] b:old.writes)if((b[0]&255)==0x8f)noFeature=false;
   check(noFeature,"no refused output report is replayed as a feature pulse");
   // Refusals are per report id: 0x80 is still probed on B5 with a response after 0x81's.
   d.steamHaptic(new byte[]{(byte)0x80,0,0,0,0x10,0x27,0,0x10,0x27,0});w=old.writes.size()-1;
   check(w==6&&old.targets.get(w)==rumble3&&old.writes.get(w).length==9&&((int[])get(d,"outputRefusals"))[0]==0
         &&rumble3.writeTypes.size()==1&&rumble3.writeTypes.get(0)==BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT,
         "refusals are counted per report id; 0x80 is still probed on B5");
   callback.onCharacteristicWrite(old,rumble3,0);
   List<String> framingInfos=new ArrayList<>();com.limelight.LimeLog.infoHook=framingInfos::add;
   ((Runnable)get(d,"hapticStats")).run();com.limelight.LimeLog.infoHook=null;
   check(framingInfos.stream().anyMatch(s->s.contains("id-less framing: 0x80 accepted, 0x81 refused x2")),
         "the 10 s summary shows each offered report's verdict, so a refusing link can be told from a silent one");
   d.stop();}
  // No output characteristics at all: the fork.27 feature conversion as before.
  {d=make();old=(BluetoothGatt)get(d,"gatt");BluetoothGattCharacteristic feat=command();
   old.service.chars.add(feat);old.service.chars.add(notify);
   callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
   callback.onCharacteristicWrite(old,feat,0);callback.onCharacteristicWrite(old,feat,0);
   d.steamHaptic(click);int w=old.writes.size()-1;
   check(w==2&&old.targets.get(w)==feat&&(old.writes.get(w)[0]&255)==0x8f&&old.writes.get(w)[2]==1,"links without output endpoints replay pulses as feature commands");
   callback.onCharacteristicWrite(old,feat,0);d.stop();w=old.writes.size()-1;
   check(old.targets.get(w)==feat&&(old.writes.get(w)[0]&255)==0xeb,"stop() on such a link sends the feature motors-off");}
  // The first write of each output report on a link goes with a response (the firmware's verdict
  // is logged); later ones go without. Each report id is probed on its own.
  {d=make();old=(BluetoothGatt)get(d,"gatt");BluetoothGattCharacteristic feat=command();
   BluetoothGattCharacteristic pulse2=new BluetoothGattCharacteristic("100f6cb6-1735-4313-b402-38567131e5f3",14);
   BluetoothGattCharacteristic rumble2=new BluetoothGattCharacteristic("100f6cb5-1735-4313-b402-38567131e5f3",14);
   old.service.chars.add(rumble2);old.service.chars.add(pulse2);old.service.chars.add(feat);old.service.chars.add(notify);
   callback=(BluetoothGattCallback)get(d,"gattCallback");callback.onServicesDiscovered(old,0);
   callback.onCharacteristicWrite(old,feat,0);callback.onCharacteristicWrite(old,feat,0);
   d.steamHaptic(click);
   check(pulse2.writeTypes.size()==1&&pulse2.writeTypes.get(0)==BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT,"the first 0x81 on a link is written with a response");
   callback.onCharacteristicWrite(old,pulse2,0);d.steamHaptic(click);
   check(pulse2.writeTypes.size()==2&&pulse2.writeTypes.get(1)==BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE,"later 0x81 writes go without response");
   callback.onCharacteristicWrite(old,pulse2,0);
   d.steamHaptic(new byte[]{(byte)0x80,0,0,0,0x10,0x27,0,0x10,0x27,0});
   check(rumble2.writeTypes.size()==1&&rumble2.writeTypes.get(0)==BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT,"each report id is probed with a response once");
   callback.onCharacteristicWrite(old,rumble2,0);d.steamHaptic(new byte[]{(byte)0x80,0,0,0,0,0,0,0,0,0});callback.onCharacteristicWrite(old,rumble2,0);
   // (A refusal of an already-probed report is a Write Command the peer cannot refuse: the
   // per-id retry is pinned in the refusal block above, where the probe itself is refused.)
   // 0x44 acknowledgements on 79 are counted; the summary line runs without a link error.
   BluetoothGattCharacteristic ackCh=new BluetoothGattCharacteristic("100f6c79-1735-4313-b402-38567131e5f3",16);ackCh.value=new byte[]{4,2,0,0,0};
   callback.onCharacteristicChanged(old,ackCh);
   check((int)get(d,"hapticAcks")==1,"a 0x44 acknowledgement is counted");
   ((Runnable)get(d,"hapticStats")).run();
   check((int)get(d,"hapticAcks")==0&&((Handler)get(d,"handler")).delayed.contains(get(d,"hapticStats")),"the haptic summary resets its counters and re-arms");
   d.stop();}
  // Output stops coalesce per side and survive a stalled link; the newest 0x80 supersedes older ones.
  {d=make();set(d,"writeChar",command());((BluetoothGattCharacteristic[])get(d,"outputChars"))[0]=outRumble;((BluetoothGattCharacteristic[])get(d,"outputChars"))[1]=outPulse;set(d,"writeBusy",true);
   for(int i=0;i<1000;i++)d.steamHaptic(new byte[]{(byte)0x81,(byte)(i%2),10,0,20,0,(byte)(i%3==0?0:5),0});
   int stops=0;for(Object item:(Deque<?>)get(d,"writeQueue")){byte[] b=(byte[])get(item,"bytes");if((b[0]&255)==0x81&&b[6]==0&&b[7]==0)stops++;}
   check(((Deque<?>)get(d,"writeQueue")).size()<=32&&stops==2,"output pulse stops are coalesced per side and kept under overload");
   for(int i=0;i<10;i++)d.steamHaptic(new byte[]{(byte)0x80,0,0,0,(byte)(i+1),0,0,(byte)(i+1),0,0});
   int rumbles=0;for(Object item:(Deque<?>)get(d,"writeQueue")){byte[] b=(byte[])get(item,"bytes");if((b[0]&255)==0x80)rumbles++;}
   check(rumbles==1,"only the newest 0x80 state stays queued");
   d.stop();}

  // Binder may deliver the completion before writeCharacteristic returns to its caller.
  // A refusal must still find its report and a returning older write cannot replace the
  // next report already dispatched by that completion.
  {d=make();old=(BluetoothGatt)get(d,"gatt");set(d,"writeChar",command());
   BluetoothGattCharacteristic pulse=new BluetoothGattCharacteristic("100f6cb6-1735-4313-b402-38567131e5f3",8);
   ((BluetoothGattCharacteristic[])get(d,"outputChars"))[1]=pulse;
   final BluetoothGatt g=old;final BluetoothGattCallback cb=(BluetoothGattCallback)get(d,"gattCallback");
   g.beforeWriteReturn=()->completeOnWorker(cb,g,pulse,4);
   d.steamHaptic(click);
   Handler eh=(Handler)get(d,"handler");Runnable eretry=(Runnable)get(d,"retryFlush");Object queuedRetry=((Deque<?>)get(d,"writeQueue")).peekFirst();
   check(g.writes.size()==1&&eh.delayed.contains(eretry)&&queuedRetry!=null&&Arrays.equals((byte[])get(queuedRetry,"bytes"),click)&&!(boolean)get(d,"writeBusy"),
         "an early failed completion queues the correct report for its delayed retry");
   check(!((boolean[])get(d,"outputProbed"))[1],"the early-refused report's retry is a probe again (A33-01)");
   eh.delayed.remove(eretry);eretry.run();
   check(g.writes.size()==2&&Arrays.equals(g.writes.get(1),Arrays.copyOfRange(click,1,click.length)),"the delayed retry re-sends the refused report id-less");
   cb.onCharacteristicWrite(g,pulse,0);
   check(((boolean[])get(d,"outputProbed"))[1],"the retry's completion records the actual with-response probe mode");
   check(get(d,"inFlight")==null&&!(boolean)get(d,"writeBusy"),"the early-completion retry drains without retaining the original write");
   d.stop();}
  {d=make();old=(BluetoothGatt)get(d,"gatt");set(d,"writeChar",command());
   BluetoothGattCharacteristic pulse=new BluetoothGattCharacteristic("100f6cb6-1735-4313-b402-38567131e5f3",8);
   BluetoothGattCharacteristic cmd=new BluetoothGattCharacteristic("100f6cb7-1735-4313-b402-38567131e5f3",8);
   ((BluetoothGattCharacteristic[])get(d,"outputChars"))[1]=pulse;((BluetoothGattCharacteristic[])get(d,"outputChars"))[2]=cmd;
   final SteamControllerBle driver=d;final BluetoothGatt g=old;final BluetoothGattCallback cb=(BluetoothGattCallback)get(d,"gattCallback");
   final byte[] next=new byte[]{(byte)0x82,0,2,(byte)0xf2};
   g.beforeWriteReturn=()->{driver.steamHaptic(next);completeOnWorker(cb,g,pulse,0);};
   d.steamHaptic(click);
   check(g.writes.size()==2&&g.targets.get(1)==cmd,"an early completion dispatches the next queued report");
   cb.onCharacteristicWrite(g,cmd,4);
   Object queuedNext=((Deque<?>)get(d,"writeQueue")).peekFirst();
   check(g.writes.size()==2&&queuedNext!=null&&Arrays.equals((byte[])get(queuedNext,"bytes"),next),
         "the earlier call returning cannot overwrite the next report's retry identity");
   Handler nh=(Handler)get(d,"handler");Runnable nretry=(Runnable)get(d,"retryFlush");nh.delayed.remove(nretry);nretry.run();
   check(g.writes.size()==3&&g.targets.get(2)==cmd&&Arrays.equals(g.writes.get(2),Arrays.copyOfRange(next,1,next.length)),"the delayed retry re-sends it id-less");
   cb.onCharacteristicWrite(g,cmd,0);d.stop();}
  {d=make();old=(BluetoothGatt)get(d,"gatt");BluetoothGattCharacteristic feat=command();set(d,"writeChar",feat);
   final BluetoothGatt g=old;final BluetoothGattCallback cb=(BluetoothGattCallback)get(d,"gattCallback");
   set(d,"writeRejectedSinceMs",SystemClock.uptimeMillis()-10000L);
   g.beforeWriteReturn=()->completeOnWorker(cb,g,feat,0);
   d.steamHaptic(click);
   check(get(d,"inFlight")==null&&!((Handler)get(d,"handler")).delayed.contains(get(d,"writeWatchdog")),
         "an already completed write cannot leave a stale report or watchdog behind");
   g.acceptWrite=false;d.steamHaptic(click);
   check(!g.closed&&get(d,"gatt")==g,"an early completion resets the previous synchronous rejection window");
   d.stop();}

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
  // Early connection results: the stack can deliver DISCONNECTED (client registration refused)
  // or CONNECTED on its binder thread before connectGatt returns. Neither may be dropped.
  {BluetoothDevice dev=new BluetoothDevice();SteamControllerBle drv=create(dev);final BluetoothGattCallback cb=(BluetoothGattCallback)get(drv,"gattCallback");
   dev.onConnect=()->cb.onConnectionStateChange(dev.next,133,BluetoothProfile.STATE_DISCONNECTED);
   boolean startedEarly=drv.start();
   check(!startedEarly&&get(drv,"gatt")==null&&dev.next.closed&&!drv.isLinkUp(),"a DISCONNECTED delivered before connectGatt returns retires the attempt instead of publishing a dead link");
   drv.stop();}
  {BluetoothDevice dev=new BluetoothDevice();SteamControllerBle drv=create(dev);final BluetoothGattCallback cb=(BluetoothGattCallback)get(drv,"gattCallback");
   dev.onConnect=()->cb.onConnectionStateChange(dev.next,BluetoothGatt.GATT_SUCCESS,BluetoothProfile.STATE_CONNECTED);
   boolean startedEarly=drv.start();Handler h=(Handler)get(drv,"handler");
   check(startedEarly&&get(drv,"gatt")==dev.next&&h.delayed.contains(tryGet(drv,"setupWatchdog"))&&dev.next.mtuRequests==1,"a CONNECTED delivered before connectGatt returns still negotiates the link");
   drv.stop();}
  // Connect watchdog: an attempt that never reports anything cannot keep isLinkUp() true.
  {d=make();old=(BluetoothGatt)get(d,"gatt");Handler h=(Handler)get(d,"handler");Runnable watchdog=(Runnable)tryGet(d,"connectWatchdog");
   check(watchdog!=null&&h.delayed.contains(watchdog),"start arms the connect watchdog");
   if(watchdog!=null)watchdog.run();
   check(get(d,"gatt")==null&&old.closed&&!d.isLinkUp(),"a connection attempt without any result is recycled");
   d.stop();}
  {d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");Handler h=(Handler)get(d,"handler");
   callback.onConnectionStateChange(old,BluetoothGatt.GATT_SUCCESS,BluetoothProfile.STATE_CONNECTED);
   check(!h.delayed.contains(tryGet(d,"connectWatchdog")),"CONNECTED disarms the connect watchdog");
   // Two retirements of one handle (binder DISCONNECTED and a watchdog) spend one reconnect attempt.
   h.delayed.clear();
   callback.onConnectionStateChange(old,8,BluetoothProfile.STATE_DISCONNECTED);
   Method lost=null;for(Class<?> c=d.getClass();c!=null;c=c.getSuperclass()){try{lost=c.getDeclaredMethod("onLinkLost",BluetoothGatt.class);break;}catch(NoSuchMethodException ignored){}}
   lost.setAccessible(true);lost.invoke(d,old);
   check((int)get(d,"reconnectAttempts")==1&&h.delayed.size()==1,"a second retirement of the same handle is a no-op");
   d.stop();}
  // Pause a CONNECTED callback after its first stopped check. stop() retains
  // the handle while restoring lizard mode, so handle equality is insufficient.
  {
   BluetoothDevice dev=new BluetoothDevice();SteamControllerBle drv=create(dev);drv.start();
   BluetoothGatt g=dev.next;
   BluetoothGattCallback cb=(BluetoothGattCallback)get(drv,"gattCallback");
   Handler h=(Handler)get(drv,"handler");
   set(drv,"writeChar",command());set(drv,"writeBusy",true);
   Thread worker=new Thread(()->cb.onConnectionStateChange(g,0,BluetoothProfile.STATE_CONNECTED));worker.setDaemon(true);
   synchronized(drv){
    worker.start();long deadline=System.nanoTime()+3_000_000_000L;
    while(worker.getState()!=Thread.State.BLOCKED&&worker.isAlive()&&System.nanoTime()<deadline)Thread.yield();
    if(worker.getState()!=Thread.State.BLOCKED)throw new AssertionError("CONNECTED did not reach the driver monitor");
    drv.stop();
    check(get(drv,"gatt")==g&&(boolean)get(drv,"closing"),"stop retains the handle until restore writes finish");
   }
   worker.join(3000);if(worker.isAlive())throw new AssertionError("CONNECTED did not finish after stop");
   check(g.mtuRequests==0&&!g.discovered,"a CONNECTED callback admitted before stop cannot restart setup during restoration");
   check(!(boolean)get(drv,"linkConnected")&&!(boolean)get(drv,"mtuRequested")
         &&!h.delayed.contains(tryGet(drv,"setupWatchdog")),
         "stop prevents a delayed CONNECTED callback from publishing setup state or timers");
   ((Runnable)get(drv,"closeLink")).run();
  }
  for(boolean retry:new boolean[]{false,true}){
   BluetoothDevice dev=new BluetoothDevice();SteamControllerBle drv=create(dev);drv.start();
   BluetoothGatt retired=dev.next,replacement=new BluetoothGatt();
   BluetoothGattCallback cb=(BluetoothGattCallback)get(drv,"gattCallback");
   if(retry)cb.onConnectionStateChange(retired,0,BluetoothProfile.STATE_CONNECTED);
   retired.acceptMtu=false;
   retired.beforeMtuReturn=()->{
    Thread worker=new Thread(()->{
     cb.onConnectionStateChange(retired,0,BluetoothProfile.STATE_DISCONNECTED);
     dev.next=replacement;drv.start();
     cb.onConnectionStateChange(replacement,0,BluetoothProfile.STATE_CONNECTED);
    });worker.setDaemon(true);worker.start();
    try{worker.join(3000);}catch(InterruptedException e){throw new AssertionError(e);}
    if(worker.isAlive())throw new AssertionError("replacement blocked behind the MTU call");
   };
   if(retry)cb.onMtuChanged(retired,23,0);
   else cb.onConnectionStateChange(retired,0,BluetoothProfile.STATE_CONNECTED);
   check(get(drv,"gatt")==replacement&&(boolean)get(drv,"mtuRequested")&&!retired.discovered,
         "a returning rejected MTU request cannot consume a replacement's negotiation; retry="+retry);
   cb.onMtuChanged(replacement,100,0);
   check(replacement.discovered,"the replacement's MTU completion still discovers services");
   drv.stop();}
  {d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");
   callback.onConnectionStateChange(old,0,BluetoothProfile.STATE_CONNECTED);
   callback.onMtuChanged(old,23,0);int requests=old.mtuRequests;
   callback.onConnectionStateChange(old,0,BluetoothProfile.STATE_CONNECTED);
   check(old.mtuRequests==requests&&(boolean)get(d,"mtuRetried"),
         "a duplicate CONNECTED callback cannot restart setup or reset the MTU retry budget");
   callback.onMtuChanged(old,23,0);
   check(old.closed,"the small-MTU retry limit survives a duplicate CONNECTED callback");
   d.stop();}
  // MTU: a link whose MTU stays too small for the 45-byte state reports is recycled, not announced.
  {d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");
   callback.onConnectionStateChange(old,BluetoothGatt.GATT_SUCCESS,BluetoothProfile.STATE_CONNECTED);
   int mtuBefore=old.mtuRequests;
   callback.onMtuChanged(old,23,0);
   check(old.mtuRequests==mtuBefore+1&&get(d,"gatt")==old&&!old.discovered,"a 23-byte MTU is asked for once more before anything else");
   callback.onMtuChanged(old,23,0);
   check(get(d,"gatt")==null&&old.closed,"an MTU that stays at 23 recycles the link");
   d.stop();}
  {d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");
   callback.onConnectionStateChange(old,BluetoothGatt.GATT_SUCCESS,BluetoothProfile.STATE_CONNECTED);
   callback.onMtuChanged(old,23,0);callback.onMtuChanged(old,100,0);
   check(get(d,"gatt")==old&&old.discovered,"an MTU that grows on the second request goes on to service discovery");
   d.stop();}
  // A refused retry cannot make a known-small link usable, and a retry callback can
  // arrive on another thread before requestMtu returns to this callback.
  {d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");
   callback.onConnectionStateChange(old,0,BluetoothProfile.STATE_CONNECTED);
   old.acceptMtu=false;callback.onMtuChanged(old,23,0);
   check(get(d,"gatt")==null&&old.closed&&!old.discovered,"a synchronously refused MTU retry retires the known-small link before discovery");
   d.stop();}
  for(int retryMtu:new int[]{100,23}){
   d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");
   callback.onConnectionStateChange(old,0,BluetoothProfile.STATE_CONNECTED);
   final BluetoothGatt g=old;final BluetoothGattCallback cb=callback;
   g.beforeMtuReturn=()->{Thread t=new Thread(()->cb.onMtuChanged(g,retryMtu,0));t.setDaemon(true);t.start();
    try{t.join(3000);}catch(InterruptedException e){throw new AssertionError(e);}
    if(t.isAlive())throw new AssertionError("MTU callback blocked behind requestMtu");};
   callback.onMtuChanged(old,23,0);
   check(retryMtu==100 ? get(d,"gatt")==old&&old.discovered : get(d,"gatt")==null&&old.closed&&!old.discovered,
         "an early MTU retry callback is processed before requestMtu returns; mtu="+retryMtu);
   check(!(boolean)get(d,"mtuRequested"),"a returning MTU request cannot resurrect a completed or retired negotiation");
   d.stop();}
  // A44-01: a late loss of a different handle during the connect window must not erase this
  // attempt's early CONNECTED.
  {BluetoothDevice dev=new BluetoothDevice();SteamControllerBle drv=create(dev);final BluetoothGattCallback cb=(BluetoothGattCallback)get(drv,"gattCallback");
   final BluetoothGatt stale=new BluetoothGatt();
   dev.onConnect=()->{cb.onConnectionStateChange(dev.next,BluetoothGatt.GATT_SUCCESS,BluetoothProfile.STATE_CONNECTED);
    cb.onConnectionStateChange(stale,133,BluetoothProfile.STATE_DISCONNECTED);};
   boolean startedEarly=drv.start();
   check(startedEarly&&get(drv,"gatt")==dev.next&&dev.next.mtuRequests==1,"a late loss of another handle does not erase this attempt's early CONNECTED");
   drv.stop();}
  // A44-02: a write watchdog already dequeued when its write completes must not expire the
  // next write; the same for a connect watchdog dequeued as its link is retired and replaced.
  {d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");set(d,"writeChar",command());Handler h=(Handler)get(d,"handler");
   d.steamHaptic(click);d.steamHaptic(new byte[]{(byte)0x82,0,2,(byte)0xf2});
   Runnable w1=(Runnable)get(d,"writeWatchdog");check(w1!=null&&h.delayed.contains(w1),"the first write arms its watchdog");
   h.delayed.remove(w1);callback.onCharacteristicWrite(old,command(),0);
   check(old.writes.size()==2&&(boolean)get(d,"writeBusy")&&get(d,"writeWatchdog")!=w1,"the completion dispatches the next write with its own watchdog");
   w1.run();
   check(get(d,"gatt")==old&&!old.closed,"a stale write watchdog does not recycle the link under the next write");
   ((Runnable)get(d,"writeWatchdog")).run();
   check(get(d,"gatt")==null&&old.closed,"the next write's own watchdog still expires it");
   d.stop();}
  {BluetoothDevice dev=new BluetoothDevice();SteamControllerBle drv=create(dev);final BluetoothGattCallback cb=(BluetoothGattCallback)get(drv,"gattCallback");
   drv.start();BluetoothGatt first=dev.next;Handler h=(Handler)get(drv,"handler");Runnable w=(Runnable)get(drv,"connectWatchdog");
   h.delayed.remove(w);cb.onConnectionStateChange(first,8,BluetoothProfile.STATE_DISCONNECTED);
   dev.next=new BluetoothGatt();drv.start();
   w.run();
   check(get(drv,"gatt")==dev.next&&!dev.next.closed,"a stale connect watchdog does not recycle the replacement link");
   drv.stop();}
  // A46-01: a setup completing as the stream stops must not hand the listener a context for
  // a driver that is already closing. Park onReady on the driver monitor, stop() while it
  // waits, then let it continue.
  {d=make();old=(BluetoothGatt)get(d,"gatt");set(d,"writeChar",command());set(d,"stateSubscribed",true);
   final SteamControllerBle drv=d;final BluetoothGatt g=old;int addedBefore=added;
   Method ready=null;for(Class<?> c=d.getClass();c!=null;c=c.getSuperclass()){try{ready=c.getDeclaredMethod("onReady",BluetoothGatt.class);break;}catch(NoSuchMethodException ignored){}}
   ready.setAccessible(true);final Method readyM=ready;
   Thread worker=new Thread(()->{try{readyM.invoke(drv,g);}catch(Exception e){throw new AssertionError(e);}});worker.setDaemon(true);
   synchronized(drv){
    worker.start();long deadline=System.nanoTime()+3_000_000_000L;
    while(worker.getState()!=Thread.State.BLOCKED&&worker.isAlive()&&System.nanoTime()<deadline)Thread.yield();
    if(worker.getState()!=Thread.State.BLOCKED)throw new AssertionError("onReady did not reach the driver monitor");
    drv.stop();
   }
   worker.join(3000);if(worker.isAlive())throw new AssertionError("onReady did not finish after stop");
   check(added==addedBefore&&!(boolean)get(drv,"announced"),"a setup completing during stop() does not announce a closing driver");
   ((Runnable)get(drv,"closeLink")).run();}
  // Host settings writes (Steam's 0x87/0x8E forwarded over the stream) go to the command
  // characteristic with their report id, minus the ids this link owns or that would change its
  // wire format; they are never deduplicated against the keep-alive; load-defaults is followed
  // by this link's own setup; a stopped driver ignores them.
  {SteamControllerBle hs=make();BluetoothGatt hg=(BluetoothGatt)get(hs,"gatt");BluetoothGattCharacteristic hfeat=command();set(hs,"writeChar",hfeat);
   int hBefore=hg.writes.size();
   hs.steamSettings((byte)0x87,new byte[]{48,0x18,0, 9,1,0, 7,7,0, 49,2,0, 52,(byte)0xff,(byte)0xff, 45,100,0, 71,1,0, 53,(byte)0xff,(byte)0xff});
   int hw=hg.writes.size()-1;
   check(hw==hBefore&&hg.targets.get(hw)==hfeat&&Arrays.equals(hg.writes.get(hw),new byte[]{(byte)0x87,6,7,7,0,45,100,0}),
         "a host settings write reaches characteristic 34 with its id, minus the link's own and wire-format ids");
   check(hfeat.writeTypes.get(hfeat.writeTypes.size()-1)==BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT,"host settings go as a write request, like the link's own setup");
   BluetoothGattCallback hcb=(BluetoothGattCallback)get(hs,"gattCallback");hcb.onCharacteristicWrite(hg,hfeat,0);
   hs.steamSettings((byte)0x87,new byte[]{9,0,0,71,0,0});
   check(hg.writes.size()==hw+1,"a host write with only reserved ids writes nothing");
   set(hs,"writeBusy",true);
   ((Runnable)get(hs,"keepAlive")).run();hs.steamSettings((byte)0x87,new byte[]{34,100,0});hs.steamSettings((byte)0x87,new byte[]{35,80,0});((Runnable)get(hs,"keepAlive")).run();
   int hostWrites=0,keepAlives=0;
   for(Object item:(Deque<?>)get(hs,"writeQueue")){byte[] b=(byte[])get(item,"bytes");if((b[0]&255)!=0x87)continue;if((boolean)get(item,"hostSettings"))hostWrites++;else keepAlives++;}
   check(hostWrites==2&&keepAlives==1,"host settings writes are never deduplicated and leave the single keep-alive in place");
   hs.stop();
   hs=make();hg=(BluetoothGatt)get(hs,"gatt");hfeat=command();set(hs,"writeChar",hfeat);set(hs,"writeBusy",true);
   hs.steamSettings((byte)0x8E,new byte[0]);
   List<byte[]> hq=new ArrayList<>();for(Object item:(Deque<?>)get(hs,"writeQueue"))hq.add((byte[])get(item,"bytes"));
   check(hq.size()==2&&(hq.get(0)[0]&255)==0x8E&&hq.get(0)[1]==0&&(hq.get(1)[0]&255)==0x87&&hq.get(1)[2]==9&&hq.get(1)[3]==0&&hq.get(1)[5]==71,
         "load-defaults from the host is followed by this link's lizard/watchdog/IMU setup");
   hs.stop();int hQueued=((Deque<?>)get(hs,"writeQueue")).size();hs.steamSettings((byte)0x87,new byte[]{45,100,0});
   check(((Deque<?>)get(hs,"writeQueue")).size()==hQueued&&hg.writes.isEmpty(),"a stopped driver ignores host settings");
   hs=make();hg=(BluetoothGatt)get(hs,"gatt");hfeat=command();set(hs,"writeChar",hfeat);
   hs.steamSettings((byte)0x89,new byte[]{45,100,0});
   check(hg.writes.isEmpty(),"an unknown host settings command is ignored");hs.stop();}
  // A keep-alive before LOAD_DEFAULT_SETTINGS cannot satisfy the setup required
  // after it. Drive the real queue through completion and inspect the wire order,
  // including repeated resets while a haptic write holds the GATT slot.
  for(boolean pendingKeepAlive:new boolean[]{false,true})for(int resets:new int[]{1,2}){
   SteamControllerBle hs=make();BluetoothGatt hg=(BluetoothGatt)get(hs,"gatt");
   BluetoothGattCharacteristic resetFeature=command();set(hs,"writeChar",resetFeature);
   BluetoothGattCallback cb=(BluetoothGattCallback)get(hs,"gattCallback");
   hs.steamHaptic(new byte[]{(byte)0x81,0,1,0,1,0,1,0});
   if(pendingKeepAlive)((Runnable)get(hs,"keepAlive")).run();
   for(int i=0;i<resets;i++)hs.steamSettings((byte)0x8E,new byte[0]);
   int completions=0;
   while(get(hs,"inFlight")!=null&&completions++<12)cb.onCharacteristicWrite(hg,resetFeature,0);
   byte[] setup={(byte)0x87,9,9,0,0,71,0,0,48,0,0};
   int restored=0,defaults=0;
   for(int i=0;i<hg.writes.size();i++)if((hg.writes.get(i)[0]&255)==0x8E){
    defaults++;
    if(i+1<hg.writes.size()&&Arrays.equals(hg.writes.get(i+1),setup))restored++;
   }
   String context=" (pending keep-alive="+pendingKeepAlive+", resets="+resets+")";
   check(defaults==resets&&restored==resets,"every host reset restores link settings immediately afterwards"+context);
   check(get(hs,"inFlight")==null&&Arrays.equals(hg.writes.get(hg.writes.size()-1),setup),
         "draining reset traffic leaves lizard/watchdog disabled and the configured IMU mode"+context);
   hs.stop();
  }
  // fork.112 diagnostics: the haptic boost, pulse scale and master gain preferences, and per-id host samples.
  {BluetoothGattCharacteristic outPulse2=new BluetoothGattCharacteristic("100f6cb6-1735-4313-b402-38567131e5f3",12);
   BluetoothGattCharacteristic outCmd2=new BluetoothGattCharacteristic("100f6cb7-1735-4313-b402-38567131e5f3",12);
   SteamControllerBle hd=createWith(new BluetoothDevice(),6,200,0);hd.start();
   BluetoothGatt hg=(BluetoothGatt)get(hd,"gatt");BluetoothGattCharacteristic feat=command();
   hg.service.chars.add(outPulse2);hg.service.chars.add(outCmd2);hg.service.chars.add(feat);hg.service.chars.add(notify);
   BluetoothGattCallback cb=(BluetoothGattCallback)get(hd,"gattCallback");cb.onServicesDiscovered(hg,0);
   cb.onCharacteristicWrite(hg,feat,0);cb.onCharacteristicWrite(hg,feat,0);
   check(Arrays.equals(hg.writes.get(1),new byte[]{(byte)0x87,9,9,0,0,71,0,0,48,0,0}),"master gain off leaves the link setup command unchanged");
   hd.steamHaptic(click);int w=hg.writes.size()-1;
   check(w==2&&hg.targets.get(w)==outPulse2&&Arrays.equals(hg.writes.get(w),new byte[]{1,(byte)0x20,3,0,0,1,0}),
         "pulse scale 2x doubles the on-time of a host click pulse (400 us to 800 us) and keeps the rest");
   cb.onCharacteristicWrite(hg,outPulse2,0);
   hd.steamHaptic(new byte[]{(byte)0x81,1,0,0,0,0,0,0});w=hg.writes.size()-1;
   check(w==3&&Arrays.equals(hg.writes.get(w),new byte[]{1,0,0,0,0,0,0}),"pulse scale leaves a zero stop pulse alone");
   cb.onCharacteristicWrite(hg,outPulse2,0);
   hd.steamHaptic(new byte[]{(byte)0x82,1,1,0});w=hg.writes.size()-1;
   check(w==4&&hg.targets.get(w)==outCmd2&&Arrays.equals(hg.writes.get(w),new byte[]{1,1,6}),"boost +6 lifts a 0 dB tick to the +6 dB ceiling");
   cb.onCharacteristicWrite(hg,outCmd2,0);
   hd.steamHaptic(new byte[]{(byte)0x82,0,2,(byte)0xf2});w=hg.writes.size()-1;
   check(w==5&&Arrays.equals(hg.writes.get(w),new byte[]{0,2,(byte)0xf8}),"boost +6 lifts a -14 dB click to -8 dB, keeping Steam's balance");
   cb.onCharacteristicWrite(hg,outCmd2,0);
   hd.steamHaptic(new byte[]{(byte)0x82,0,0,0});w=hg.writes.size()-1;
   check(w==6&&Arrays.equals(hg.writes.get(w),new byte[]{0,0,0}),"the boost leaves a 0x82 stop alone");
   check((int)get(hd,"hostHapticsAdjusted")==3,"adjusted reports are counted for the 10 s summary");
   hd.stop();
   SteamControllerBle pd=createWith(new BluetoothDevice(),20,100,6);pd.start();
   BluetoothGatt pg=(BluetoothGatt)get(pd,"gatt");BluetoothGattCharacteristic feat2=command();
   pg.service.chars.add(outCmd2);pg.service.chars.add(feat2);pg.service.chars.add(notify);
   cb=(BluetoothGattCallback)get(pd,"gattCallback");cb.onServicesDiscovered(pg,0);
   byte[] setupGain={(byte)0x87,12,9,0,0,71,0,0,48,0,0,76,6,0};
   cb.onCharacteristicWrite(pg,feat2,0);cb.onCharacteristicWrite(pg,feat2,0);
   check(Arrays.equals(pg.writes.get(1),setupGain),"master gain +6 dB is written as setting 76 in the link setup command");
   pd.steamHaptic(new byte[]{(byte)0x82,0,2,(byte)0xf2});w=pg.writes.size()-1;
   check(w==2&&pg.targets.get(w)==outCmd2&&Arrays.equals(pg.writes.get(w),new byte[]{0,2,6}),"boost +20 clamps a -14 dB click at the +6 dB ceiling");
   cb.onCharacteristicWrite(pg,outCmd2,0);
   pd.steamSettings((byte)0x8E,new byte[0]);
   int completions=0;
   while(get(pd,"inFlight")!=null&&completions++<12)cb.onCharacteristicWrite(pg,feat2,0);
   int n=pg.writes.size();
   check(n>=5&&(pg.writes.get(n-2)[0]&255)==0x8E&&Arrays.equals(pg.writes.get(n-1),setupGain),"a host reset is followed by the setup command including the master gain");
   pd.stop();
   List<String> infos=new ArrayList<>();com.limelight.LimeLog.infoHook=infos::add;
   SteamControllerBle sd=make();set(sd,"writeChar",command());
   for(int i=0;i<30;i++)sd.steamHaptic(click);
   sd.steamHaptic(new byte[]{(byte)0x82,1,1,0});
   com.limelight.LimeLog.infoHook=null;
   long pulses=infos.stream().filter(s->s.contains("host haptic report")&&s.contains(": 81 ")).count();
   boolean cmdLogged=infos.stream().anyMatch(s->s.contains("host haptic report")&&s.endsWith(": 82 01 01 00"));
   check(pulses==12&&cmdLogged,"host haptic samples are logged per report id, so a 0x82 after 30 pulses still appears");
   check(((int[])get(sd,"hostLeft"))[1]==30&&((int[])get(sd,"hostRight"))[1]==0&&((int[])get(sd,"hostRight"))[2]==1&&((int[])get(sd,"hostLeft"))[2]==0,
         "host 0x81/0x82 reports are counted per pad for the 10 s summary: pulse side 1 is the left pad, command side 1 the right");
   Method ilp=SteamControllerBle.class.getDeclaredMethod("isLeftPad",int.class,byte.class);ilp.setAccessible(true);
   check((Boolean)ilp.invoke(null,0x81,(byte)1)&&!(Boolean)ilp.invoke(null,0x81,(byte)0)&&(Boolean)ilp.invoke(null,0x82,(byte)0)&&!(Boolean)ilp.invoke(null,0x82,(byte)1),
         "the 0x82 command numbers its sides opposite to the 0x81 pulse");
   sd.stop();
  }
  // fork.113: the haptic self-test plays Steam's report shapes on each pad after link setup, without the host.
  {BluetoothGattCharacteristic outPulse3=new BluetoothGattCharacteristic("100f6cb6-1735-4313-b402-38567131e5f3",12);
   BluetoothGattCharacteristic outCmd3=new BluetoothGattCharacteristic("100f6cb7-1735-4313-b402-38567131e5f3",12);
   for(boolean enabled:new boolean[]{false,true}){
    SteamControllerBle td=createWith(new BluetoothDevice(),0,100,0,enabled);td.start();
    BluetoothGatt tg=(BluetoothGatt)get(td,"gatt");BluetoothGattCharacteristic feat=command();
    tg.service.chars.add(outPulse3);tg.service.chars.add(outCmd3);tg.service.chars.add(feat);tg.service.chars.add(notify);
    BluetoothGattCallback cb=(BluetoothGattCallback)get(td,"gattCallback");cb.onServicesDiscovered(tg,0);
    cb.onCharacteristicWrite(tg,feat,0);cb.onCharacteristicWrite(tg,feat,0);
    Handler h=(Handler)get(td,"handler");List<Runnable> due=new ArrayList<>(h.delayed);
    for(Runnable r:due){if(!r.getClass().getSimpleName().equals("SelfTestReport"))continue;h.delayed.remove(r);r.run();int c=0;while(get(td,"inFlight")!=null&&c++<4){Object p=get(td,"inFlight");cb.onCharacteristicWrite(tg,(BluetoothGattCharacteristic)get(p,"ch"),0);}}
    int clicksR=0,clicksL=0,ticksR=0,ticksL=0,pulsesR=0,pulsesL=0;
    for(byte[] w:tg.writes){
     if(Arrays.equals(w,new byte[]{1,2,6}))clicksR++;else if(Arrays.equals(w,new byte[]{0,2,6}))clicksL++;
     else if(Arrays.equals(w,new byte[]{1,1,6}))ticksR++;else if(Arrays.equals(w,new byte[]{0,1,6}))ticksL++;
     else if(Arrays.equals(w,new byte[]{0,(byte)0xd0,7,0,0,1,0}))pulsesR++;else if(Arrays.equals(w,new byte[]{1,(byte)0xd0,7,0,0,1,0}))pulsesL++;
    }
    int expected=enabled?3:0;
    check(clicksR==expected&&clicksL==expected&&ticksR==expected&&ticksL==expected&&pulsesR==expected&&pulsesL==expected,
          "the haptic self-test writes 3 clicks, 3 ticks and 3 pulses per pad at +6 dB only when enabled, command side 1 and pulse side 0 both being the right pad (enabled="+enabled+")");
    td.stop();
   }
  }
  // fork.115: the feature-report fallback for a 0x82 command derives its side mask from the measured numbering (side 0 = left).
  {Method th=SteamControllerBle.class.getDeclaredMethod("triggerHaptic",byte.class,byte.class,byte.class);th.setAccessible(true);
   byte[] left=(byte[])th.invoke(null,(byte)0,(byte)2,(byte)6),right=(byte[])th.invoke(null,(byte)1,(byte)2,(byte)6),both=(byte[])th.invoke(null,(byte)2,(byte)2,(byte)6);
   check(left[2]==0x01&&right[2]==0x02&&both[2]==0x03&&left[3]==2&&left[5]==6,
         "triggerHaptic maps 0x82 side 0 to the left-pad mask 0x01 and side 1 to the right-pad mask 0x02, anything else to both");
  }
  // fork.115: a self-test schedule belongs to the link it was made for; link loss removes it and a
  // reconnect gets a fresh schedule, so the two never interleave on the replacement link.
  {BluetoothGattCharacteristic outPulse5=new BluetoothGattCharacteristic("100f6cb6-1735-4313-b402-38567131e5f3",12);
   BluetoothGattCharacteristic outCmd5=new BluetoothGattCharacteristic("100f6cb7-1735-4313-b402-38567131e5f3",12);
   java.util.function.ToIntFunction<BluetoothGatt> selfTestWrites=g->{int n=0;for(byte[] w:g.writes){
    if(Arrays.equals(w,new byte[]{1,2,6})||Arrays.equals(w,new byte[]{0,2,6})||Arrays.equals(w,new byte[]{1,1,6})||Arrays.equals(w,new byte[]{0,1,6})
       ||Arrays.equals(w,new byte[]{0,(byte)0xd0,7,0,0,1,0})||Arrays.equals(w,new byte[]{1,(byte)0xd0,7,0,0,1,0}))n++;}return n;};
   BluetoothDevice dev=new BluetoothDevice();SteamControllerBle td=createWith(dev,0,100,0,true);td.start();
   BluetoothGatt first=dev.next;BluetoothGattCharacteristic feat=command();
   first.service.chars.add(outPulse5);first.service.chars.add(outCmd5);first.service.chars.add(feat);first.service.chars.add(notify);
   BluetoothGattCallback cb=(BluetoothGattCallback)get(td,"gattCallback");cb.onServicesDiscovered(first,0);
   cb.onCharacteristicWrite(first,feat,0);cb.onCharacteristicWrite(first,feat,0);
   Handler h=(Handler)get(td,"handler");
   List<Runnable> lostSched=new ArrayList<>();for(Runnable r:h.delayed)if(r.getClass().getSimpleName().equals("SelfTestReport"))lostSched.add(r);
   check(lostSched.size()==18,"the self-test schedule exists once the first link is set up");
   cb.onConnectionStateChange(first,8,BluetoothProfile.STATE_DISCONNECTED);
   boolean noneLeft=true;for(Runnable r:h.delayed)if(r.getClass().getSimpleName().equals("SelfTestReport"))noneLeft=false;
   check(get(td,"gatt")==null&&noneLeft,"link loss removes the pending self-test reports from the handler");
   BluetoothGatt replacement5=new BluetoothGatt();dev.next=replacement5;td.start();
   replacement5.service.chars.add(outPulse5);replacement5.service.chars.add(outCmd5);replacement5.service.chars.add(feat);replacement5.service.chars.add(notify);
   cb.onServicesDiscovered(replacement5,0);cb.onCharacteristicWrite(replacement5,feat,0);cb.onCharacteristicWrite(replacement5,feat,0);
   List<Runnable> freshSched=new ArrayList<>();for(Runnable r:h.delayed)if(r.getClass().getSimpleName().equals("SelfTestReport"))freshSched.add(r);
   boolean disjoint=true;for(Runnable r:freshSched)if(lostSched.contains(r))disjoint=false;
   check(get(td,"gatt")==replacement5&&freshSched.size()==18&&disjoint,"the replacement link gets its own self-test schedule");
   for(Runnable r:lostSched){r.run();int c=0;while(get(td,"inFlight")!=null&&c++<4){Object p=get(td,"inFlight");cb.onCharacteristicWrite(replacement5,(BluetoothGattCharacteristic)get(p,"ch"),0);}}
   check(selfTestWrites.applyAsInt(replacement5)==0&&get(td,"gatt")==replacement5,"the lost link's self-test reports write nothing to the replacement link");
   for(Runnable r:freshSched){h.delayed.remove(r);r.run();int c=0;while(get(td,"inFlight")!=null&&c++<4){Object p=get(td,"inFlight");cb.onCharacteristicWrite(replacement5,(BluetoothGattCharacteristic)get(p,"ch"),0);}}
   check(selfTestWrites.applyAsInt(replacement5)==18,"the replacement link's own schedule still writes all 18 reports");
   td.stop();
  }
  // fork.116: a pad touch decoded before the controller is announced (the state characteristic
  // is subscribed first, so reports arrive during setup) went to a listener without a context,
  // so the host never saw its DOWN. After the announcement the same report must send a DOWN,
  // not a MOVE for a pointer the host does not know, and no UP is sent for it.
  {d=make();old=(BluetoothGatt)get(d,"gatt");callback=(BluetoothGattCallback)get(d,"gattCallback");
   BluetoothGattCharacteristic state16=new BluetoothGattCharacteristic("100f6c7a-1735-4313-b402-38567131e5f3",16);
   state16.value=new byte[45];state16.value[4]=2;state16.value[17]=0x34;state16.value[18]=0x12;state16.value[21]=0x10; // left pad touched
   int downs=touchDowns,moves=touchMoves,ups=touchUps;
   callback.onCharacteristicChanged(old,state16);
   check(touchDowns==downs+1&&(boolean)get(d,"leftPadTouched"),"a touch during setup is decoded (the handler drops it for want of a context)");
   old.service.chars.add(command());old.service.chars.add(state16);before=added;
   callback.onServicesDiscovered(old,0);
   check(added==before+1&&touchUps==ups&&!(boolean)get(d,"leftPadTouched"),"the announcement forgets the setup-time touch without an UP the host never saw go down");
   callback.onCharacteristicChanged(old,state16);
   check(touchDowns==downs+2&&touchMoves==moves,"the first report after the announcement sends a DOWN for the held finger, not a MOVE");
   state16.value[18]=0x13;callback.onCharacteristicChanged(old,state16);
   check(touchMoves==moves+1&&touchDowns==downs+2,"a later movement of that finger is a MOVE");
   d.stop();}
  // fork.116: a late or duplicate descriptor-write completion used to run onReady again: the
  // keep-alive re-armed, the reconnect budget reset, the setup re-queued and a second self-test
  // schedule interleaved with the first.
  {BluetoothDevice dev=new BluetoothDevice();SteamControllerBle td=createWith(dev,0,100,0,true);td.start();
   BluetoothGatt tg=dev.next;BluetoothGattCharacteristic feat=command();
   BluetoothGattCharacteristic state6=new BluetoothGattCharacteristic("100f6c7a-1735-4313-b402-38567131e5f3",16);state6.descriptor=new BluetoothGattDescriptor(state6);
   tg.service.chars.add(feat);tg.service.chars.add(state6);
   BluetoothGattCallback cb=(BluetoothGattCallback)get(td,"gattCallback");Handler h=(Handler)get(td,"handler");
   int addedBefore=added;cb.onServicesDiscovered(tg,0);cb.onDescriptorWrite(tg,state6.descriptor,0);
   java.util.function.IntSupplier selfTests=()->{int n=0;for(Runnable r:h.delayed)if(r.getClass().getSimpleName().equals("SelfTestReport"))n++;return n;};
   Object keepAliveRunnable=get(td,"keepAlive");
   java.util.function.IntSupplier keepAlives=()->{int n=0;for(Runnable r:h.delayed)if(r==keepAliveRunnable)n++;return n;};
   check(added==addedBefore+1&&selfTests.getAsInt()==18&&keepAlives.getAsInt()==1,"the first descriptor-write completion sets the link up once");
   int queued=((Deque<?>)get(td,"writeQueue")).size();set(td,"reconnectAttempts",3);
   cb.onDescriptorWrite(tg,state6.descriptor,0);
   check(added==addedBefore+1&&selfTests.getAsInt()==18&&keepAlives.getAsInt()==1&&(int)get(td,"reconnectAttempts")==3&&((Deque<?>)get(td,"writeQueue")).size()==queued,
         "a duplicate descriptor-write completion neither re-arms the keep-alive, resets the reconnect budget, re-queues the setup nor schedules a second self-test");
   td.stop();}
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
 int gripsMode,rumbleHoldMs,rumbleMethod,hapticBoostDb,pulseScalePercent,masterGainDb;boolean hapticSelfTest;BroadcastReceiver aclReceiver;
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
