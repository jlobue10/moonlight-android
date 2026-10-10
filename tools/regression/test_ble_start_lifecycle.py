#!/usr/bin/env python3
"""Run the actual BLE start method as a late permission/connection callback."""
import pathlib, subprocess, sys, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
PATH='app/src/main/java/com/limelight/Game.java'
source=(subprocess.check_output(['git','show','HEAD:'+PATH],cwd=ROOT,text=True)
        if '--baseline' in sys.argv else (ROOT/PATH).read_text())
start=source.index('    private void startSteamControllerDriver()');end=source.index('{',start);depth=1
while depth:
 end+=1;depth+=(source[end]=='{')-(source[end]=='}')
method=source[start:end+1]
prefix=r'''
public class BleStartLifecycle {
 boolean finishing,destroyed;Object controllerHandler=new Object();int requests;
 SteamControllerBleManager steamControllerBle;Prefs prefConfig=new Prefs();
 static final int REQUEST_STEAM_CONTROLLER_BLUETOOTH=1,MODE_PRIVATE=0;
 static class SharedPreferences {static boolean denied; boolean getBoolean(String k,boolean d){return denied;}}
 SharedPreferences getSharedPreferences(String n,int m){return new SharedPreferences();}
 boolean isFinishing(){return finishing;}boolean isDestroyed(){return destroyed;}
 void requestPermissions(String[] names,int request){++requests;}
 static class Build {static class VERSION {static int SDK_INT=35;}static class VERSION_CODES {static final int M=23;}}
 static class Prefs {Object steamControllerMotion,steamControllerSplitPads,steamControllerGrips,steamControllerRumbleHold,steamControllerRumbleMethod,steamControllerStickRim;}
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
  if(failures!=0)System.exit(1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='ble-start-lifecycle-') as directory:
 p=pathlib.Path(directory);(p/'BleStartLifecycle.java').write_text(prefix+method+suffix)
 subprocess.run(['java','com.sun.tools.javac.Main','-d',str(p),str(p/'BleStartLifecycle.java')],check=True)
 raise SystemExit(subprocess.run(['java','-cp',str(p),'BleStartLifecycle']).returncode)
