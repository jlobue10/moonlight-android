#!/usr/bin/env python3
"""Exercise the production puck transport and shared input mapper with fake Android USB I/O.

JDK 17+, no SDK or USB hardware. Firmware, USB scheduling and RF timing need a real puck.
"""
import ast
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
JAVA = ROOT / 'app/src/main/java'
# Reuse the Android boundary fakes, not another implementation of the driver.
module = ast.parse((Path(__file__).with_name('test_steam_ble.py')).read_text())
STUBS = ast.literal_eval(next(n.value for n in module.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'STUBS' for t in n.targets)))
STUBS.update({
'android/os/Handler.java': '''package android.os; import java.util.*; public class Handler {
 static class Task {Handler h;Runnable r;long due;Task(Handler h,Runnable r,long d){this.h=h;this.r=r;due=d;}}
 static final List<Task> tasks=new ArrayList<>(); public Handler(Looper l){}
 public boolean post(Runnable r){return postDelayed(r,0);} public boolean postDelayed(Runnable r,long d){synchronized(tasks){tasks.add(new Task(this,r,SystemClock.now+d));}return true;}
 public void removeCallbacks(Runnable r){synchronized(tasks){tasks.removeIf(t->t.h==this&&t.r==r);}}
 public void removeCallbacksAndMessages(Object o){synchronized(tasks){tasks.removeIf(t->t.h==this);}}
 public static void runReady(){for(int i=0;i<10000;i++){Task next=null;synchronized(tasks){for(Task t:tasks)if(t.due<=SystemClock.now){next=t;break;}if(next!=null)tasks.remove(next);}if(next==null)return;next.r.run();}throw new AssertionError("task spin");}
}''',
'android/os/SystemClock.java': 'package android.os; public class SystemClock {public static volatile long now; public static long uptimeMillis(){return now;}}',
'android/hardware/usb/UsbConstants.java': 'package android.hardware.usb; public class UsbConstants {public static final int USB_CLASS_HID=3,USB_ENDPOINT_XFER_INT=3,USB_DIR_IN=128,USB_DIR_OUT=0;}',
'android/hardware/usb/UsbEndpoint.java': '''package android.hardware.usb; public class UsbEndpoint {
 public int address; public UsbEndpoint(int a){address=a;} public int getDirection(){return address&128;} public int getType(){return 3;} public int getMaxPacketSize(){return 64;}}''',
'android/hardware/usb/UsbInterface.java': '''package android.hardware.usb; public class UsbInterface {public int id;public boolean out;
 public UsbInterface(int i,boolean o){id=i;out=o;}public int getId(){return id;}public int getInterfaceClass(){return 3;}public int getAlternateSetting(){return 0;}
 public int getEndpointCount(){return out?2:1;}public UsbEndpoint getEndpoint(int n){return new UsbEndpoint(id|(n==0?128:0));}}''',
'android/hardware/usb/UsbDevice.java': '''package android.hardware.usb; public class UsbDevice {
 public int vendor=0x28de,product=0x1304; public UsbInterface[] interfaces={new UsbInterface(0,true),new UsbInterface(5,true),new UsbInterface(1,true),new UsbInterface(3,true),new UsbInterface(2,true),new UsbInterface(4,true)};
 public int getVendorId(){return vendor;}public int getProductId(){return product;}public int getDeviceId(){return 7;}public String getDeviceName(){return "puck";}
 public int getInterfaceCount(){return interfaces.length;}public UsbInterface getInterface(int n){return interfaces[n];}}''',
'android/hardware/usb/UsbDeviceConnection.java': '''package android.hardware.usb; import java.util.*; public class UsbDeviceConnection {
 public static class Tx {public int index,value,endpoint;public byte[] data;Tx(int i,int v,int e,byte[] d){index=i;value=v;endpoint=e;data=d.clone();}}
 public java.util.concurrent.ConcurrentHashMap<Integer,java.util.concurrent.ConcurrentLinkedQueue<byte[]>> input=new java.util.concurrent.ConcurrentHashMap<>();
 public void receive(int id,byte[] r){input.computeIfAbsent(id,k->new java.util.concurrent.ConcurrentLinkedQueue<>()).add(r);}
 public List<Integer> claims=new ArrayList<>(),releases=new ArrayList<>();public List<Tx> tx=new ArrayList<>();public int closes,failClaim=-1;public boolean failOut,failControl;
 public boolean claimInterface(UsbInterface i,boolean force){if(!force)throw new AssertionError();if(i.id==failClaim)return false;claims.add(i.id);return true;}
 public boolean releaseInterface(UsbInterface i){releases.add(i.id);return true;}public void close(){closes++;}
 public int bulkTransfer(UsbEndpoint e,byte[] b,int n,int timeout){if(e.getDirection()==128){var q=input.get(e.address&127);byte[] r=q==null?null:q.poll();if(r!=null){System.arraycopy(r,0,b,0,r.length);return r.length;}try{Thread.sleep(timeout);}catch(InterruptedException x){}return -1;}tx.add(new Tx(-1,0,e.address,Arrays.copyOf(b,n)));return failOut?n-1:n;}
 public int controlTransfer(int type,int req,int value,int index,byte[] b,int n,int timeout){if(type!=0x21||req!=9)throw new AssertionError();tx.add(new Tx(index,value,-1,Arrays.copyOf(b,n)));return failControl?n-1:n;}
}'''
})
TEST = r'''
import java.util.*;import java.lang.reflect.*;import android.content.*;import android.os.*;import android.hardware.usb.*;
import com.limelight.binding.input.driver.*;import com.limelight.nvstream.input.ControllerPacket;import com.limelight.nvstream.jni.MoonBridge;
public class UsbRegression {
 static int checks,failed;
 static void check(boolean b,String n){checks++;System.out.println((b?"PASS ":"FAIL ")+n);if(!b)failed++;}
 static Object get(Object o,String key)throws Exception{for(Class<?> c=o.getClass();c!=null;c=c.getSuperclass()){try{Field f=c.getDeclaredField(key);f.setAccessible(true);return f.get(o);}catch(NoSuchFieldException e){}}throw new NoSuchFieldException(key);}
 static Object slot(SteamControllerUsb d,int iface)throws Exception{for(Object s:(List<?>)get(d,"slots"))if(((UsbInterface)get(s,"iface")).id==iface)return s;throw new AssertionError();}
 static void input(Object s,byte[] r,int n)throws Exception{Method m=s.getClass().getDeclaredMethod("handleReport",byte[].class,int.class);m.setAccessible(true);synchronized(s){m.invoke(s,r,n);}}
 static void put16(byte[] b,int off,int v){b[off]=(byte)v;b[off+1]=(byte)(v>>8);}
 static boolean near(float a,float b){return Math.abs(a-b)<0.0001f;}
 static class Events implements UsbDriverListener {
  List<Integer> added=new java.util.concurrent.CopyOnWriteArrayList<>(),removed=new java.util.concurrent.CopyOnWriteArrayList<>();List<float[]> touch=new ArrayList<>(),motion=new ArrayList<>();int states,flags,battery=-1,percent=-1;float lx,ly,lt,rt;
  public void reportControllerState(int i,int f,float x,float y,float rx,float ry,float l,float r){states++;flags=f;lx=x;ly=y;lt=l;rt=r;}
  public void reportControllerMotion(int i,byte t,float x,float y,float z){motion.add(new float[]{i,t,x,y,z});}
  public void reportControllerTouch(int i,byte pad,byte event,int ptr,float x,float y,float p){touch.add(new float[]{i,pad,event,ptr,x,y,p});}
  public void reportControllerBattery(int i,byte s,byte p){battery=s;percent=p&255;}
  public void deviceAdded(AbstractController c){added.add(c.getControllerId());}public void deviceRemoved(AbstractController c){removed.add(c.getControllerId());}
 }
 static SteamControllerUsb make(UsbDevice d,UsbDeviceConnection c,Events e){return new SteamControllerUsb(d,c,100,e,new Context(),true,false,SteamControllerInput.GRIPS_PADDLES,500,false);}
 static byte[] state(int id){byte[] r=new byte[id==0x42?54:46];r[0]=(byte)id;r[1]=3;int buttons=1|(1<<6)|(1<<7)|(1<<14)|(1<<20)|(1<<21)|(1<<25)|(1<<28)|(1<<29);for(int i=0;i<4;i++)r[i+2]=(byte)(buttons>>(8*i));
  put16(r,6,32767);put16(r,8,16384);put16(r,10,16384);put16(r,12,-16384);put16(r,14,-32767);put16(r,16,32767);
  int p=id==0x47?20:18;if(id==0x47)put16(r,18,0xabcd);put16(r,p,-16384);put16(r,p+2,16384);put16(r,p+4,16384);put16(r,p+6,16384);put16(r,p+8,-16384);put16(r,p+10,8192);
  put16(r,34,16384);put16(r,36,-8192);put16(r,38,4096);put16(r,40,16384);put16(r,42,-8192);put16(r,44,4096);return r;}
 static byte[] pulse(int side,int count){return new byte[]{(byte)0x81,(byte)side,4,0,0,0,(byte)count,0};}
 static void close(SteamControllerUsb d)throws Exception{d.stop();Handler.runReady();for(Object s:(List<?>)get(d,"slots")){Thread t=(Thread)get(s,"reader");if(t!=null){t.join(1000);check(!t.isAlive(),"slot read thread exits after stop");}}}
 public static void main(String[] args)throws Exception{
  UsbDevice dev=new UsbDevice();check(SteamControllerUsb.canClaimDevice(dev),"2026 puck VID/PID is accepted");dev.product=0x1142;check(!SteamControllerUsb.canClaimDevice(dev),"2015 receiver is not mistaken for the 2026 puck");dev.product=0x1303;check(!SteamControllerUsb.canClaimDevice(dev),"BLE PID is not claimed over USB");dev.product=0x1304;dev.vendor=1;check(!SteamControllerUsb.canClaimDevice(dev),"non-Valve VID is rejected");dev.vendor=0x28de;
  UsbDeviceConnection io=new UsbDeviceConnection();Events e=new Events();SteamControllerUsb d=make(dev,io,e);check(d.start(),"puck starts");Handler.runReady();check(new HashSet<>(io.claims).equals(Set.of(2,3,4,5)),"only controller interfaces 2..5 are claimed even in shuffled descriptor order");check(e.added.isEmpty()&&io.tx.isEmpty(),"empty receiver creates no phantom gamepads and receives no controller writes");
  Object s2=slot(d,2),s3=slot(d,3),s4=slot(d,4),s5=slot(d,5);
  for(int id:new int[]{0x42,0x45,0x47}){byte[] r=state(id);for(int n=1;n<r.length;n++)input(s3,r,n);}input(s3,new byte[]{0x40,2},2);input(s3,new byte[]{0x79,2},1);check(e.added.isEmpty(),"truncated state and wireless reports and lizard reports cannot announce a controller");
  input(s5,state(0x42),54);check(e.added.equals(List.of(103)),"first valid report discovers a preconnected controller in the correct slot");Handler.runReady();UsbDeviceConnection.Tx config=io.tx.get(0);check(config.index==5&&config.value==0x301&&config.data.length==64&&config.data[0]==1&&config.data[1]==(byte)0x87&&config.data[2]==9&&config.data[3]==9&&config.data[4]==0&&config.data[6]==48&&config.data[7]==0x18&&config.data[9]==71&&config.data[10]==1,"settings use report 1 and the actual slot interface, with lizard off, raw IMU and firmware watchdog enabled");
  check((e.flags&(ControllerPacket.A_FLAG|ControllerPacket.PLAY_FLAG|ControllerPacket.BACK_FLAG|ControllerPacket.PADDLE1_FLAG|ControllerPacket.LEFT_GRIP_TOUCH_FLAG|ControllerPacket.RIGHT_GRIP_TOUCH_FLAG|ControllerPacket.RIGHT_STICK_TOUCH_FLAG))==(ControllerPacket.A_FLAG|ControllerPacket.PLAY_FLAG|ControllerPacket.BACK_FLAG|ControllerPacket.PADDLE1_FLAG|ControllerPacket.LEFT_GRIP_TOUCH_FLAG|ControllerPacket.RIGHT_GRIP_TOUCH_FLAG|ControllerPacket.RIGHT_STICK_TOUCH_FLAG),"buttons, paddles, start/back and capacitive sensors reach the shared mapping");
  check(near(e.lx,16384f/32767)&&near(e.ly,16384f/32767)&&e.lt==1&&near(e.rt,16384f/32767),"stick signs and analog trigger scaling match the BLE/host convention");
  check(e.touch.size()==2&&e.touch.get(0)[1]==0&&e.touch.get(1)[1]==1&&near(e.touch.get(0)[4],.25f)&&near(e.touch.get(0)[5],.25f)&&near(e.touch.get(1)[4],.75f)&&near(e.touch.get(1)[6],.25f),"both touchpads retain independent positions and pressure");
  check(e.motion.size()==2&&near(e.motion.get(0)[2],1000)&&near(e.motion.get(0)[3],250)&&near(e.motion.get(0)[4],500)&&near(e.motion.get(1)[2],9.80665f),"gyro and accelerometer units and axes match the host protocol");
  int old=e.states;input(s5,state(0x42),54);check(e.states==old&&e.touch.size()==2,"unchanged buttons and touch moves are deduplicated");
  input(s2,state(0x45),46);input(s4,state(0x47),46);check(e.added.containsAll(List.of(100,102))&&e.touch.size()==6&&near(e.touch.get(4)[4],.25f)&&near(e.touch.get(5)[4],.75f),"0x45 and timestamped 0x47 reports decode without shifting pad or IMU fields");Handler.runReady();
  check((((AbstractController)s5).getCapabilities()&MoonBridge.LI_CCAP_SC26_BUTTONS)!=0,"arrival advertises authoritative SC2026 clicks");
  byte[] clicks=state(0x42);clicks[5]|=4;clicks[4]|=(byte)128;input(s5,clicks,54);
  check((e.flags&(ControllerPacket.LEFT_PAD_CLICK_FLAG|ControllerPacket.RIGHT_PAD_CLICK_FLAG|ControllerPacket.LEFT_TRIGGER_CLICK_FLAG|ControllerPacket.RIGHT_TRIGGER_CLICK_FLAG|ControllerPacket.TOUCHPAD_FLAG))==(ControllerPacket.LEFT_PAD_CLICK_FLAG|ControllerPacket.RIGHT_TRIGGER_CLICK_FLAG|ControllerPacket.TOUCHPAD_FLAG),"physical left pad and right trigger clicks stay independent of other touched pad and analog triggers");
  input(s5,state(0x42),54);check((e.flags&(ControllerPacket.LEFT_PAD_CLICK_FLAG|ControllerPacket.RIGHT_TRIGGER_CLICK_FLAG))==0,"physical click releases survive state deduplication");
  input(s3,state(0x42),54);Handler.runReady();check(new HashSet<>(e.added).equals(Set.of(100,101,102,103)),"four concurrent controllers have independent identities");
  Events rebound=new Events();d.reportAttachedControllers(rebound);check(new HashSet<>(rebound.added).equals(Set.of(100,101,102,103)),"service rebind replays every active controller");
  byte[] battery=new byte[15];battery[0]=0x43;battery[1]=4;battery[2]=100;input(s5,battery,14);check(e.battery==-1,"truncated battery report is ignored");input(s5,battery,15);check(e.battery==MoonBridge.LI_BATTERY_STATE_FULL&&e.percent==100,"battery state and level are forwarded");
  AbstractController c5=(AbstractController)s5;io.tx.clear();for(int id=0x80;id<=0x85;id++){int n=new int[]{10,8,4,10,9,4}[id-0x80];byte[] r=new byte[n];r[0]=(byte)id;r[1]=1;if(id==0x81)r[6]=1;c5.steamHaptic(r);Handler.runReady();check(io.tx.get(io.tx.size()-1).endpoint==5&&Arrays.equals(io.tx.get(io.tx.size()-1).data,r),"native haptic report "+Integer.toHexString(id)+" reaches only its slot unchanged");}
  int sent=io.tx.size();c5.steamHaptic(new byte[]{(byte)0x81,1});c5.steamHaptic(new byte[64]);for(int side=3;side<256;side++)c5.steamHaptic(pulse(side,0));Handler.runReady();check(io.tx.size()==sent,"invalid haptic IDs, lengths and sides cause no USB writes");
  c5.steamHaptic(pulse(0,1));input(s5,new byte[]{0x79,1},2);input(s5,state(0x42),54);io.tx.clear();Handler.runReady();check(io.tx.stream().noneMatch(t->t.endpoint==5),"queued feedback from a disconnected generation is not replayed after reconnect");check(e.removed.equals(List.of(103))&&e.added.stream().filter(i->i==103).count()==2&&e.states>old,"wireless disconnect removes only its slot and reconnect resends held input");
  // Fill the bounded queue while the output worker is held. A stop must survive effects.
  byte[] zeroDuration=pulse(0,5);zeroDuration[2]=0;c5.steamHaptic(zeroDuration);for(int i=0;i<100;i++)c5.steamHaptic(pulse(1,1));check(((ArrayDeque<?>)get(s5,"outputs")).size()<=32,"effect overload keeps the output queue bounded");io.tx.clear();Handler.runReady();check(io.tx.stream().anyMatch(t->Arrays.equals(t.data,zeroDuration)),"zero-duration motor stop survives effect overload");
  io.tx.clear();c5.rumble((short)1234,(short)5678);Handler.runReady();SystemClock.now+=40;Handler.runReady();check(io.tx.stream().filter(t->t.endpoint==5&&t.data[0]==(byte)0x80).count()==2,"rumble refreshes at 40 ms without host reissuance");SystemClock.now+=500;Handler.runReady();check(io.tx.stream().anyMatch(t->t.endpoint==5&&t.data.length==10&&t.data[4]==0&&t.data[5]==0&&t.data[7]==0&&t.data[8]==0),"rumble hold deadline sends motors off");
  // A tolerated transfer failure must not discard a motor-stop command.
  byte[] motorStop=pulse(0,0);io.tx.clear();io.failOut=true;c5.steamHaptic(motorStop);Handler.runReady();
  check((boolean)get(s5,"connected")&&((ArrayDeque<?>)get(s5,"outputs")).size()==1,"failed stop remains queued while the slot tolerates a transient error");
  int attempts=io.tx.size();Handler.runReady();check(io.tx.size()==attempts,"failed stop retry waits instead of spinning");
  for(int i=0;i<100;i++)c5.steamHaptic(pulse(1,1));
  check(((ArrayDeque<?>)get(s5,"outputs")).size()<=32,"retained stop shares the bounded output queue under overload");
  io.failOut=false;io.tx.clear();SystemClock.now+=40;Handler.runReady();
  check(!io.tx.isEmpty()&&Arrays.equals(io.tx.get(0).data,motorStop),"recovered USB link sends the retained stop before later effects");
  io.failOut=true;c5.steamHaptic(motorStop);Handler.runReady();input(s5,new byte[]{0x79,1},2);input(s5,state(0x42),54);
  io.failOut=false;io.tx.clear();SystemClock.now+=40;Handler.runReady();
  check(io.tx.stream().noneMatch(t->Arrays.equals(t.data,motorStop)),"disconnect cancels a pending stop retry before a new controller generation");
  // Stops retry autonomously; a retained stop is one strike however often it is retried, and
  // the retry backs off (40, 80, 160 ms ...) so a stalled pipe does not spend the failure budget.
  io.failOut=true;io.tx.clear();c5.steamHaptic(motorStop);Handler.runReady();
  SystemClock.now+=40;Handler.runReady();check((boolean)get(s5,"connected"),"two failed stop attempts retain the slot");
  SystemClock.now+=80;Handler.runReady();int stopTries=io.tx.size();check(stopTries==3&&(boolean)get(s5,"connected"),"a third failed stop retry is not a third strike");
  SystemClock.now+=100;Handler.runReady();check(io.tx.size()==stopTries,"stop retries back off after each failure");
  SystemClock.now+=60;Handler.runReady();check(io.tx.size()==stopTries+1&&(boolean)get(s5,"connected"),"the backed-off retry fires at 160 ms and the slot is retained");
  io.failOut=false;SystemClock.now+=1000;input(s5,state(0x42),54);Handler.runReady();
  check(io.tx.stream().anyMatch(t->t.endpoint==5&&Arrays.equals(t.data,motorStop))&&((ArrayDeque<?>)get(s5,"outputs")).isEmpty(),"the retained stop is delivered once the pipe recovers");
  // Latest 0x80 state supersedes an obsolete stop, even while it awaits retry.
  io.failOut=true;c5.rumble((short)0,(short)0);Handler.runReady();
  c5.rumble((short)1,(short)2);io.failOut=false;io.tx.clear();SystemClock.now+=40;Handler.runReady();
  check(io.tx.stream().noneMatch(t->t.endpoint==5&&t.data[0]==(byte)0x80&&t.data[4]==0&&t.data[7]==0),"new rumble supersedes an obsolete pending rumble stop");
  c5.rumble((short)0,(short)0);Handler.runReady();
  io.failOut=true;c5.steamHaptic(pulse(0,1));Handler.runReady();check((boolean)get(s5,"connected"),"one short USB output transfer does not retire the slot (Android reports a timeout, a STALL and a detach alike)");for(int i=0;i<2;i++){c5.steamHaptic(pulse(0,1));Handler.runReady();}check(!(boolean)get(s5,"connected"),"three consecutive short USB output transfers retire the affected slot");input(s5,state(0x42),54);check(!(boolean)get(s5,"connected"),"failed output has a reconnect backoff");io.failOut=false;SystemClock.now+=1000;input(s5,state(0x42),54);Handler.runReady();check((boolean)get(s5,"connected"),"later state recovers after transient output failure");
  io.failControl=true;SystemClock.now+=1000;Handler.runReady();check((boolean)get(s5,"connected"),"one feature keepalive failure does not retire the slot");for(int i=0;i<2;i++){SystemClock.now+=1000;Handler.runReady();}check(!(boolean)get(s5,"connected"),"three consecutive keepalive failures retire the slot");io.failControl=false;SystemClock.now+=1000;input(s5,state(0x42),54);Handler.runReady();check((boolean)get(s5,"connected"),"state reconnects after feature failure");
  close(d);d.stop();Handler.runReady();check(io.closes==1&&new HashSet<>(io.releases).equals(Set.of(2,3,4,5)),"stop closes once and releases only owned controller interfaces");check(io.tx.stream().anyMatch(t->t.value==0x301&&t.data[4]==1&&t.data[7]==0),"stop restores lizard mode and disables raw IMU");int adds=e.added.size();check(!d.start()&&e.added.size()==adds,"stopped receiver cannot restart on its retired output thread");
  UsbDeviceConnection fail=new UsbDeviceConnection();fail.failClaim=3;SteamControllerUsb bad=make(new UsbDevice(),fail,new Events());check(!bad.start()&&fail.closes==1&&fail.releases.equals(List.of(5)),"partial claim failure releases prior claims and closes the connection");
  UsbDevice wired=new UsbDevice();wired.product=0x1302;wired.interfaces=new UsbInterface[]{new UsbInterface(0,false)};UsbDeviceConnection wi=new UsbDeviceConnection();Events we=new Events();SteamControllerUsb wd=make(wired,wi,we);check(wd.start(),"direct wired SC2026 uses the same transport");Object ws=slot(wd,0);input(ws,state(0x42),54);Handler.runReady();((AbstractController)ws).steamHaptic(pulse(1,1));Handler.runReady();check(wi.tx.stream().anyMatch(t->t.index==0&&t.value==0x281&&t.data.length==8),"without interrupt OUT, haptics use HID SET_REPORT(output) on the proper interface");close(wd);
  UsbDevice liveDevice=new UsbDevice();UsbDeviceConnection liveIo=new UsbDeviceConnection();Events liveEvents=new Events();SteamControllerUsb live=make(liveDevice,liveIo,liveEvents);live.start();
  liveIo.receive(2,state(0x42));for(int i=0;i<200&&liveEvents.added.isEmpty();i++)Thread.sleep(5);
  check(liveEvents.added.equals(List.of(100)),"actual read loop discovers a controller from interrupt IN");
  synchronized(slot(live,2)){SystemClock.now+=3001;}
  for(int i=0;i<200&&liveEvents.removed.isEmpty();i++)Thread.sleep(5);
  check(liveEvents.removed.equals(List.of(100)),"silent input expires through the actual read loop without a detach broadcast");close(live);
  System.out.println(checks+" checks, "+failed+" failures");if(failed!=0)System.exit(1);
 }
}
'''
with tempfile.TemporaryDirectory() as temp:
    work=Path(temp)
    constants = re.findall(r'public static final (?:byte|short|int) LI_(?:CCAP|CTYPE|TOUCH_EVENT|BATTERY_STATE|MOTION_TYPE)[^;]+;', (JAVA/'com/limelight/nvstream/jni/MoonBridge.java').read_text())
    STUBS['com/limelight/nvstream/jni/MoonBridge.java']='package com.limelight.nvstream.jni; public class MoonBridge {'+'\n'.join(constants)+'}'
    STUBS['UsbRegression.java']=TEST
    for name,source in STUBS.items():
        p=work/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(source)
    sources=list(work.rglob('*.java'))+[JAVA/p for p in (
        'com/limelight/binding/input/driver/AbstractController.java',
        'com/limelight/binding/input/driver/UsbDriverListener.java',
        'com/limelight/binding/input/driver/SteamControllerInput.java',
        'com/limelight/binding/input/driver/SteamControllerUsb.java',
        'com/limelight/nvstream/input/ControllerPacket.java')]
    subprocess.run(['java','com.sun.tools.javac.Main','-d',str(work/'classes'),*map(str,sources)],check=True)
    raise SystemExit(subprocess.run(['java','-cp',str(work/'classes'),'UsbRegression'],timeout=30).returncode)
