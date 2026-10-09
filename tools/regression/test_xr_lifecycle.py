#!/usr/bin/env python3
"""Run actual XR start/stop/failure methods against queued SceneCore callbacks.

No Android runtime or headset is simulated. --baseline reads the current HEAD.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/xr/XrStereoPresenter.java'
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
import java.util.*;
import java.util.function.Consumer;
public class XrLifecycle {
 static final long FULL_SPACE_TIMEOUT_MS=6000;
 static boolean synchronousGrant;
 static class Activity {}
 static class Surface {}
 interface Listener {void onStereoSurfaceReady(Surface s,int w,int h);void onStereoUnavailable(String reason);}
 static class Recorder implements Listener {
  int ready,failed;
  public void onStereoSurfaceReady(Surface s,int w,int h){ready++;}
  public void onStereoUnavailable(String reason){failed++;}
 }
 static class Looper {static Looper getMainLooper(){return new Looper();}}
 static class Handler {
  List<Runnable> queued=new ArrayList<>();Handler(Looper l){}
  void postDelayed(Runnable r,long ms){queued.add(r);}
  void removeCallbacks(Runnable r){queued.removeIf(t->t==r);}
 }
 static class LimeLog {static void info(String s){}static void warning(String s){}}
 enum SpatialCapability {SPATIAL_3D_CONTENT}
 static class SpatialModeChangeEvent {}
 static class MovableComponent {}
 static class Entity {void setEnabled(boolean b){}void setAlpha(float a){}}
 static class SurfaceEntity extends Entity {
  boolean disposed;void removeComponent(MovableComponent m){}void dispose(){disposed=true;}
 }
 static class ActivitySpace {void removeOriginChangedListener(Runnable r){}}
 static class Scene {
  Consumer<Set<SpatialCapability>> callback;boolean throwRemove;int homes;
  Set<SpatialCapability> getSpatialCapabilities(){return Set.of();}
  void addSpatialCapabilitiesChangedListener(Consumer<Set<SpatialCapability>> c){callback=c;}
  void removeSpatialCapabilitiesChangedListener(Consumer<Set<SpatialCapability>> c){if(throwRemove)throw new IllegalStateException("remove");callback=null;}
  void clearSpatialModeChangedListener(){}
  ActivitySpace getActivitySpace(){return new ActivitySpace();}
  Entity getMainPanelEntity(){return new Entity();}
  void requestFullSpace(){if(synchronousGrant)callback.accept(Set.of(SpatialCapability.SPATIAL_3D_CONTENT));}
  void requestHomeSpace(){homes++;}
 }
 static class Session {final Scene scene=new Scene();static SessionCreateResult create(Activity a){return new SessionCreateSuccess();}}
 static class SessionCreateResult {}
 static class SessionCreateSuccess extends SessionCreateResult {Session getSession(){return new Session();}}
 static class SessionExt {static Scene getScene(Session s){return s.scene;}}
 void enableDeviceTracking(){}
 void createEntity(){
  if(scene==null)throw new IllegalStateException("late creation after stop");
  waitingForFullSpace=false;mainHandler.removeCallbacks(fullSpaceTimeout);
  entity=new SurfaceEntity();listener.onStereoSurfaceReady(new Surface(),frameWidthPx,frameHeightPx);
 }
 XrLifecycle(){activity=new Activity();}
'''
SUFFIX = r'''
 static int failures;
 static void check(boolean ok,String name){System.out.println((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
 void begin(Recorder r){start(1920,1080,2f,true,false,r);}
 static Set<SpatialCapability> granted(){return Set.of(SpatialCapability.SPATIAL_3D_CONTENT);}
 public static void main(String[] args){
  XrLifecycle p=new XrLifecycle();Recorder r=new Recorder();p.begin(r);
  Consumer<Set<SpatialCapability>> stale=p.capabilitiesListener;p.stop();stale.accept(granted());
  check(r.ready==0&&r.failed==0&&p.entity==null,"capability callback after stop cannot restart or trigger fallback");
  p=new XrLifecycle();r=new Recorder();p.begin(r);stale=p.capabilitiesListener;p.stop();
  Recorder current=new Recorder();p.begin(current);stale.accept(granted());
  check(current.ready==0&&current.failed==0&&p.isWaitingForFullSpace(),"old capability callback cannot act on a restarted presenter");p.stop();
  p=new XrLifecycle();r=new Recorder();p.begin(r);Runnable timeout=p.mainHandler.queued.get(0);p.stop();timeout.run();
  check(r.failed==0,"queued timeout after stop cannot trigger fallback");
  p=new XrLifecycle();r=new Recorder();synchronousGrant=true;p.begin(r);synchronousGrant=false;
  check(r.ready==1&&!p.isWaitingForFullSpace()&&p.mainHandler.queued.isEmpty(),"synchronous Full Space grant clears waiting state and timeout");p.stop();
  p=new XrLifecycle();r=new Recorder();p.begin(r);p.createEntity();SurfaceEntity created=p.entity;Scene old=p.scene;old.throwRemove=true;p.stop();
  check(created.disposed&&old.homes==1&&p.entity==null&&p.listener==null,"listener removal failure does not skip entity disposal or retain listener");
  System.exit(failures==0?0:1);
 }
}
'''

fields = source[source.index('    private final Activity activity;'):
                source.index('    public XrStereoPresenter(Activity activity)')]
markers = ['    public void start(', '    public boolean isWaitingForFullSpace()',
           '    private void fail(', '    public void stop()']
for marker in ['    private boolean isCurrentGeneration(', '    private void cleanup(']:
    if marker in source:
        markers.append(marker)
with tempfile.TemporaryDirectory(prefix='xr-lifecycle-') as directory:
    work = pathlib.Path(directory)
    java = work / 'XrLifecycle.java'
    java.write_text(PREFIX + fields + '\n'.join(map(block, markers)) + SUFFIX)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(java)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'XrLifecycle']).returncode)
