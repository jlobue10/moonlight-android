#!/usr/bin/env python3
"""Run the actual BLE start method as a late permission/connection callback."""
import pathlib, subprocess, sys, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
PATH='app/src/main/java/com/limelight/Game.java'
source=(subprocess.check_output(['git','show','HEAD:'+PATH],cwd=ROOT,text=True)
        if '--baseline' in sys.argv else (ROOT/PATH).read_text())
def block(marker):
 start=source.index(marker);end=source.index('{',start);depth=1
 while depth:
  end+=1;depth+=(source[end]=='{')-(source[end]=='}')
 return source[start:end+1]
method=block('    private void startSteamControllerDriver()')
# The USB bind lives in connectionStarted(); extract just that guarded block as a method.
cs=block('    public void connectionStarted()')
ub=cs.index('        if (prefConfig.usbDriver) {');ue=cs.index('        if (prefConfig.steamControllerBle) {')
usb_bind='    void bindUsbDriver() {\n'+cs[ub:ue]+'    }\n'
od=block('    protected void onDestroy()')
us=od.index('        if (usbDriverBindRequested) {');ue2=od.index('        }\n',us)+len('        }\n')
usb_unbind='    void unbindUsbDriver() {\n'+od[us:ue2]+'    }\n'
method=(method+'\n'+usb_bind+usb_unbind).replace('Game.this','BleStartLifecycle.this')
prefix=r'''
public class BleStartLifecycle {
 boolean finishing,destroyed;Object controllerHandler=new Object();int requests;
 SteamControllerBleManager steamControllerBle;Prefs prefConfig=new Prefs();
 static final int REQUEST_STEAM_CONTROLLER_BLUETOOTH=1,MODE_PRIVATE=0;
 static class SharedPreferences {static boolean denied; boolean getBoolean(String k,boolean d){return denied;}}
 SharedPreferences getSharedPreferences(String n,int m){return new SharedPreferences();}
 boolean isFinishing(){return finishing;}boolean isDestroyed(){return destroyed;}
 void requestPermissions(String[] names,int request){++requests;}
 int binds,unbinds;boolean usbDriverBindRequested,connectedToUsbDriverService;Object usbDriverServiceConnection=new Object();
 static class Intent {Intent(Object c,Class<?> k){}} static class Service {static final int BIND_AUTO_CREATE=1;} static class UsbDriverService {}
 void runOnUiThread(Runnable r){r.run();}
 void bindService(Intent i,Object c,int f){++binds;} void unbindService(Object c){++unbinds;}
 void bindUsbDriverIfEnabled(){prefConfig.usbDriver=true;bindUsbDriver();}
 static class Build {static class VERSION {static int SDK_INT=35;}static class VERSION_CODES {static final int M=23;}}
 static class Prefs {Object steamControllerMotion,steamControllerSplitPads,steamControllerGrips,steamControllerRumbleHold,steamControllerRumbleMethod,steamControllerStickRim;boolean usbDriver;}
 static class SteamControllerBleManager {
  static boolean permission=true;static int starts;
  SteamControllerBleManager(Object... args){}void start(){++starts;}
  static boolean hasPermission(Object c){return permission;}
  static String requiredPermission(){return "CONNECT";}
  static Object gripsModeFromPref(Object x){return x;}
  static Object rumbleHoldFromPref(Object x){return x;}
  static Object rumbleMethodFromPref(Object x){return x;}
 }
 static int failures;
 static void check(boolean ok,String name){System.out.println((ok?"PASS ":"FAIL ")+name);if(!ok)++failures;}
'''
suffix=r'''
 public static void main(String[] args){
  {BleStartLifecycle g=new BleStartLifecycle();SteamControllerBleManager.permission=false;SteamControllerBleManager.starts=0;SharedPreferences.denied=true;g.startSteamControllerDriver();check(g.requests==0 && SteamControllerBleManager.starts==0,"a remembered permission denial neither asks again nor starts the driver");SharedPreferences.denied=false;}
  for(int mode=0;mode<3;mode++){
   BleStartLifecycle g=new BleStartLifecycle();g.finishing=mode!=1;g.destroyed=mode==1;
   SteamControllerBleManager.starts=0;SteamControllerBleManager.permission=mode!=2;
   g.startSteamControllerDriver();
   check(SteamControllerBleManager.starts==0 && g.requests==0,"late BLE start neither connects nor asks permission (state "+mode+")");
  }
  BleStartLifecycle g=new BleStartLifecycle();SteamControllerBleManager.permission=true;
  SteamControllerBleManager.starts=0;g.startSteamControllerDriver();g.startSteamControllerDriver();
  check(SteamControllerBleManager.starts==1,"live activity starts the BLE manager only once");
  g=new BleStartLifecycle();SteamControllerBleManager.permission=false;g.startSteamControllerDriver();
  check(g.requests==1 && g.steamControllerBle==null,"live activity still requests missing permission");
  // USB driver bind: never after the activity is going away, once per activity, and always unbound.
  for(int mode=0;mode<2;mode++){
   BleStartLifecycle u=new BleStartLifecycle();u.finishing=mode==0;u.destroyed=mode==1;u.bindUsbDriverIfEnabled();
   check(u.binds==0 && !u.usbDriverBindRequested,"late USB bind is skipped on a finishing/destroyed activity (state "+mode+")");
   u.unbindUsbDriver();check(u.unbinds==0,"nothing to unbind when no bind was issued (state "+mode+")");
  }
  BleStartLifecycle u=new BleStartLifecycle();u.bindUsbDriverIfEnabled();u.bindUsbDriverIfEnabled();
  check(u.binds==1 && u.usbDriverBindRequested,"live activity binds the USB driver once");
  u.unbindUsbDriver();
  check(u.unbinds==1 && !u.usbDriverBindRequested,"a requested bind is unbound even before onServiceConnected");
  if(failures!=0)System.exit(1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='ble-start-lifecycle-') as directory:
 p=pathlib.Path(directory);(p/'BleStartLifecycle.java').write_text(prefix+method+suffix)
 subprocess.run(['java','com.sun.tools.javac.Main','-d',str(p),str(p/'BleStartLifecycle.java')],check=True)
 raise SystemExit(subprocess.run(['java','-cp',str(p),'BleStartLifecycle']).returncode)
