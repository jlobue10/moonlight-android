#!/usr/bin/env python3
"""Execute production motion configuration/decimation with fake Android sensors/time.

Android 12's permission limit is modeled at registerListener; no physical IMU is used.
--baseline reads HEAD rather than the edited ControllerHandler.
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
    return source[start:end + 1].replace('System.nanoTime()', 'Clock.now')

prefix = r'''
import java.util.*;
import java.util.concurrent.*;
public class MotionRegression {
 static class Clock {static long now;}
 static final int NATIVE_MOTION_RATE_HZ=250; static final long MOTION_BURST_WINDOW_NS=16_000_000L;
 static class MoonBridge {static final byte LI_MOTION_TYPE_ACCEL=1, LI_MOTION_TYPE_GYRO=2;}
 static class Sensor {static final int TYPE_ACCELEROMETER=1, TYPE_GYROSCOPE=4;}
 interface SensorEventListener {}
 static class SensorManager {
  int period, registrations;
  Sensor getDefaultSensor(int type){return new Sensor();}
  void unregisterListener(SensorEventListener listener){}
  void registerListener(SensorEventListener listener, Sensor sensor, int periodUs){
   if(periodUs < 5000) throw new SecurityException("HIGH_SAMPLING_RATE_SENSORS required");
   period=periodUs; ++registrations;
  }
 }
 static class Handler {void removeCallbacks(Runnable r){}}
 static class Contexts extends ArrayList<InputDeviceContext> {
  InputDeviceContext valueAt(int index){return get(index);}
 }
 static class InputDeviceContext {
  short controllerNumber,gyroReportRateHz,accelReportRateHz;
  long nextGyroReportNs,nextAccelReportNs;
  SensorManager sensorManager;
  SensorEventListener gyroListener,accelListener;
  Runnable enableSensorRunnable;
 }
 static class UsbDeviceContext extends InputDeviceContext {}
 static class Connection {
  int gyro,accel;
  void sendControllerMotionEvent(byte id,byte type,float x,float y,float z){
   if(type==2) ++gyro;else ++accel;
  }
 }
 boolean stopped;
 final Contexts inputDeviceContexts=new Contexts();
 final ConcurrentHashMap<Integer,UsbDeviceContext> usbDeviceContexts=new ConcurrentHashMap<>();
 final Handler backgroundThreadHandler=new Handler();
 final SensorManager deviceSensorManager=new SensorManager();
 final Connection conn=new Connection();
 SensorEventListener createSensorListener(short id,byte type,boolean local){return new SensorEventListener(){};}
 static int failures;
 static void check(boolean ok,String message){System.out.println((ok?"PASS ":"FAIL ")+message);if(!ok)++failures;}
'''
suffix = r'''
 public static void main(String[] args){
  for(int requested:new int[]{250,65535}){
   MotionRegression r=new MotionRegression();InputDeviceContext ctx=new InputDeviceContext();
   ctx.sensorManager=r.deviceSensorManager;r.inputDeviceContexts.add(ctx);boolean accepted=true;
   try {r.handleSetMotionEventState((short)0,(byte)2,(short)requested);}
   catch(SecurityException e){accepted=false;}
   check(accepted && ctx.sensorManager.period==5000,"Android sensors stay at 200 Hz for wire rate "+requested);
  }
  MotionRegression r=new MotionRegression();UsbDeviceContext ctx=new UsbDeviceContext();
  r.usbDeviceContexts.put(7,ctx);
  for(int requested:new int[]{250,65535}){
   r.handleSetMotionEventState((short)0,(byte)2,(short)requested);
   check(ctx.gyroReportRateHz==250,"driver-fed rate preserves 250 Hz for wire rate "+requested);
  }
  ctx.nextAccelReportNs=12345;
  r.handleSetMotionEventState((short)0,(byte)2,(short)100);
  check(ctx.nextAccelReportNs==12345,"gyro rate update preserves the accelerometer deadline");
  for(int i=0;i<250;i++){Clock.now=1_000_000_000L+i*4_000_000L;r.reportControllerMotion(7,(byte)2,1,2,3);}
  // Credits let the first samples through at once; the average converges to the request.
  check(r.conn.gyro>=100 && r.conn.gyro<=104,"250 source samples produce ~100 requested motion packets ("+r.conn.gyro+")");
  r.conn.gyro=0;r.handleSetMotionEventState((short)0,(byte)2,(short)100);
  // BLE delivers notifications in bursts: four 4 ms samples arrive together every 16 ms.
  for(int burst=0;burst<16;burst++){for(int k=0;k<4;k++){Clock.now=3_000_000_000L+burst*16_000_000L+k*100_000L;r.reportControllerMotion(7,(byte)2,1,2,3);}}
  check(r.conn.gyro>=24 && r.conn.gyro<=30,"bursty delivery still yields ~100 Hz at a 100 Hz request ("+r.conn.gyro+" of 64 in 256 ms)");
  r.conn.gyro=0;r.handleSetMotionEventState((short)0,(byte)2,(short)250);
  for(int i=0;i<250;i++){Clock.now=2_000_000_000L+i*4_000_000L;r.reportControllerMotion(7,(byte)2,1,2,3);}
  check(r.conn.gyro==250,"native-rate request retains all 250 IMU samples");
  r.handleSetMotionEventState((short)0,(byte)2,(short)0);
  Clock.now+=100_000_000L;r.reportControllerMotion(7,(byte)2,1,2,3);
  check(r.conn.gyro==250,"rate zero disables driver-fed motion");
  if(failures!=0)System.exit(1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='controller-motion-') as directory:
    work = pathlib.Path(directory)
    code = prefix + method('    public void handleSetMotionEventState(')
    code += method('    public void reportControllerMotion(') + suffix
    (work / 'MotionRegression.java').write_text(code)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work),
                    str(work / 'MotionRegression.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'MotionRegression']).returncode)
