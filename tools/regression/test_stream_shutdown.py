#!/usr/bin/env python3
"""Exercise production StreamContainer teardown and delayed failure callbacks.

UI/render boundaries are fakes. --baseline reads the current HEAD.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/ui/StreamContainer.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())


def block(marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


PREFIX = r'''
public class StreamShutdown {
 final Object surfaceReadyLock = new Object();
 volatile boolean destroyed;
 boolean xrStereo=true,isSurfaceReady;
 int views,notifications;
 enum StreamMode {MODE_2D,MODE_AI_3D}
 StreamMode renderMode=StreamMode.MODE_AI_3D;
 Stereo3DRenderer mStereoRenderer;
 SurfaceGlThread xrGlThread;
 CanvasTestPattern xrTestPattern;
 XrStereoPresenter xrPresenter;
 SurfaceView mSurfaceView=new SurfaceView();Surface mCurrentSurface;Runnable onSurfaceAvailable;
 static class Surface {}
 static class SurfaceView {Holder getHolder(){return new Holder();}}
 static class GLSurfaceView extends SurfaceView {}
 static class Holder {void addCallback(Object o){} void removeCallback(Object o){}}
 static class Stereo3DRenderer {int stops;void onSurfaceDestroyed(){stops++;}}
 static class SurfaceGlThread {void shutdown(){}}
 static class CanvasTestPattern {void shutdown(){}}
 static class XrStereoPresenter {void stop(){}}
 static class LimeLog {static void warning(String s){}}
 static class R {static class string {static int xr_stereo_fallback_toast;}}
 static class Context {String getString(int id,String reason){return reason;}}
 static class Toast {static int LENGTH_LONG;static Toast makeText(Context c,String s,int d){return new Toast();}void show(){}}
 Context getContext(){return new Context();}
 void createFlatStereoView(){views++;mSurfaceView=new GLSurfaceView();mStereoRenderer=new Stereo3DRenderer();}
 void notifySurfaceReady(){notifications++;}
'''
SUFFIX = r'''
 static int failures;
 static void check(boolean ok,String name){System.out.println((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
 public static void main(String[] args){
  StreamShutdown s=new StreamShutdown();s.onDestroy();s.fallBackToFlatStereo("queued failure");
  check(s.views==0,"delayed flat fallback does not recreate a destroyed stream");
  s=new StreamShutdown();s.onDestroy();s.onStereo3DSurfaceReady(new Surface());
  check(s.notifications==0&&s.mCurrentSurface==null,"late renderer surface does not restart a destroyed stream");
  s=new StreamShutdown();Stereo3DRenderer partial=new Stereo3DRenderer();s.mStereoRenderer=partial;s.xrGlThread=new SurfaceGlThread();s.onXrGlFailed("partial init");
  check(partial.stops==1&&s.views==1,"partial renderer initialization is cleaned up before fallback");
  s=new StreamShutdown();partial=new Stereo3DRenderer();s.mStereoRenderer=partial;s.onDestroy();s.onDestroy();
  check(partial.stops==1&&!s.xrStereo,"stream shutdown releases renderer exactly once");
  System.exit(failures==0?0:1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='stream-shutdown-') as directory:
    work = pathlib.Path(directory)
    java = work / 'StreamShutdown.java'
    markers = ['    private void onXrGlFailed(', '    private void fallBackToFlatStereo(',
               '    public void onStereo3DSurfaceReady(', '    public void onDestroy()']
    java.write_text(PREFIX + '\n'.join(map(block, markers)) + SUFFIX)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(java)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'StreamShutdown']).returncode)
