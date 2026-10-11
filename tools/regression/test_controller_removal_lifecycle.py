#!/usr/bin/env python3
"""Execute production controller stop/removal and native-send methods with fake devices.

Exercises stale removals after stop and a removal paused at the native boundary.
--baseline reads HEAD. No Android device or controller hardware is used.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[2]
def read(path):
    return (subprocess.check_output(['git','show','HEAD:'+path],cwd=ROOT,text=True)
            if '--baseline' in sys.argv else (ROOT/path).read_text())
source=read('app/src/main/java/com/limelight/binding/input/ControllerHandler.java')
connection=read('app/src/main/java/com/limelight/nvstream/NvConnection.java')
def method(src,marker):
    start=src.index(marker);end=src.index('{',start);depth=1
    while depth:
        end+=1;depth+=(src[end]=='{')-(src[end]=='}')
    return src[start:end+1]
prefix=r'''
import java.util.*;import java.util.concurrent.*;
public class RemovalLifecycle {
 static class LimeLog {static void info(String s){}}
 static class AbstractController {final int id;AbstractController(int id){this.id=id;}int getControllerId(){return id;}}
 static class GenericControllerContext {short controllerNumber;boolean reservedControllerNumber=true,assignedControllerNumber=true;
  int destroys;void destroy(){++destroys;}}
 static class InputDeviceContext extends GenericControllerContext {String name="test";}
 static class UsbDeviceContext extends InputDeviceContext {}
 static class SparseArray<T> {final Map<Integer,T> m=new LinkedHashMap<>();T get(int n){return m.get(n);}void put(int n,T t){m.put(n,t);}
  void remove(int n){m.remove(n);}int size(){return m.size();}T valueAt(int n){return new ArrayList<T>(m.values()).get(n);}}
 static class InputManager {void unregisterInputDeviceListener(Object o){}}
 static class Vibrator {void cancel(){}}
 static class Preferences {boolean multiController=true,onscreenController;}
 static class MoonBridge {
  static final List<String> events=Collections.synchronizedList(new ArrayList<>());
  static volatile String session="old";static volatile boolean block;
  static CountDownLatch entered=new CountDownLatch(1),resume=new CountDownLatch(1);
  static void sendMultiControllerInput(short n,short mask,int buttons,byte lt,byte rt,short lx,short ly,short rx,short ry){
   if(block){entered.countDown();try{resume.await();}catch(InterruptedException e){throw new RuntimeException(e);}}
   events.add(session+":"+n+":"+mask);
  }
  static void reset(){events.clear();session="old";block=false;entered=new CountDownLatch(1);resume=new CountDownLatch(1);}
 }
 static class NvConnection {boolean isMonkey;
 SEND
 }
 STOPPED
 final Object controllerMaskLock=new Object();short currentControllers=1,initialControllers;
 final Preferences prefConfig=new Preferences();final NvConnection conn=new NvConnection();
 final InputManager inputManager=new InputManager();final Vibrator deviceVibrator=new Vibrator();
 final SparseArray<InputDeviceContext> inputDeviceContexts=new SparseArray<>();
 final ConcurrentHashMap<Integer,UsbDeviceContext> usbDeviceContexts=new ConcurrentHashMap<>();
 void refreshInputDeviceContextSnapshot(){}
 static int checks,failures;
 static void check(boolean ok,String name){++checks;if(!ok)++failures;System.out.println((ok?"PASS ":"FAIL ")+name);}
'''
suffix=r'''
 public static void main(String[] args)throws Exception{
  for(boolean usb:new boolean[]{true,false}){
   MoonBridge.reset();RemovalLifecycle h=new RemovalLifecycle();InputDeviceContext context=new InputDeviceContext();
   if(usb){UsbDeviceContext u=new UsbDeviceContext();context=u;h.usbDeviceContexts.put(7,u);}else h.inputDeviceContexts.put(7,context);
   if(usb)h.deviceRemoved(new AbstractController(7));else h.onInputDeviceRemoved(7);
   check(MoonBridge.events.equals(Arrays.asList("old:0:0"))&&context.destroys==1,"live "+(usb?"USB/BLE":"Android")+" removal releases its controller normally");
   if(usb)h.deviceRemoved(new AbstractController(7));else h.onInputDeviceRemoved(7);
   check(MoonBridge.events.size()==1,"duplicate removal emits no second packet");
  }
  for(boolean usb:new boolean[]{true,false}){
   MoonBridge.reset();RemovalLifecycle h=new RemovalLifecycle();InputDeviceContext context=new InputDeviceContext();
   if(usb){UsbDeviceContext u=new UsbDeviceContext();context=u;h.usbDeviceContexts.put(7,u);}else h.inputDeviceContexts.put(7,context);
   h.stop();MoonBridge.session="replacement";
   if(usb)h.deviceRemoved(new AbstractController(7));else h.onInputDeviceRemoved(7);
   check(MoonBridge.events.isEmpty(),"late "+(usb?"USB/BLE":"Android")+" removal cannot remove a replacement session's controller");
   check(context.destroys==1,"late removal does not destroy an already stopped context twice");
  }
  MoonBridge.reset();RemovalLifecycle h=new RemovalLifecycle();h.usbDeviceContexts.put(7,new UsbDeviceContext());MoonBridge.block=true;
  Thread removal=new Thread(()->h.deviceRemoved(new AbstractController(7)));removal.start();
  check(MoonBridge.entered.await(2,TimeUnit.SECONDS),"removal reaches the native send boundary");
  CountDownLatch stopping=new CountDownLatch(1),stopped=new CountDownLatch(1);
  Thread stop=new Thread(()->{stopping.countDown();h.stop();stopped.countDown();});stop.start();stopping.await();
  boolean returned=stopped.await(200,TimeUnit.MILLISECONDS);
  check(!returned,"stop waits for an already admitted removal send to finish");
  if(returned)MoonBridge.session="replacement";
  MoonBridge.resume.countDown();removal.join(2000);stop.join(2000);
  check(!removal.isAlive()&&!stop.isAlive()&&MoonBridge.events.equals(Arrays.asList("old:0:0")),
        "a removal racing stop completes against the original native session");
  System.out.println(checks+" checks, "+failures+" failures");System.exit(failures==0?0:1);
 }
}
'''
methods=['    public void stop()', '    public void deviceRemoved(', '    public void onInputDeviceRemoved(',
         '    private void releaseControllerNumber(', '    private short getActiveControllerMask(']
code=prefix.replace('SEND',method(connection,'    public void sendControllerInput('))
code=code.replace('STOPPED',re.search(r'    private (?:volatile )?boolean stopped = false;',source).group())
code+='\n'.join(method(source,m) for m in methods)+suffix
with tempfile.TemporaryDirectory(prefix='controller-removal-') as temp:
    work=Path(temp);java=work/'RemovalLifecycle.java';java.write_text(code)
    subprocess.run(['java','com.sun.tools.javac.Main','-d',str(work),str(java)],check=True)
    raise SystemExit(subprocess.run(['java','-cp',str(work),'RemovalLifecycle'],timeout=15).returncode)
