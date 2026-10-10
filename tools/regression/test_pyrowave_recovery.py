#!/usr/bin/env python3
"""Production renderer recovery with fake JNI and a controlled cleanup interleaving."""
import pathlib,subprocess,tempfile,sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
path='app/src/main/java/com/limelight/binding/video/PyroWaveDecoderRenderer.java'
s=(subprocess.check_output(['git','show','HEAD:'+path],cwd=ROOT,text=True) if '--baseline' in sys.argv else (ROOT/path).read_text())
def block(marker):
 start=s.index(marker);end=s.index('{',start);depth=1
 while depth:
  end+=1;depth+=(s[end]=='{')-(s[end]=='}')
 return s[start:end+1]
fields=s[s.index('    private final ReentrantReadWriteLock'):s.index('    private static boolean loadLibrary')]
fields=fields.replace('new ReentrantReadWriteLock()', 'new HookLock()')
methods=[block(x) for x in ['    public boolean setup(', '    private boolean recreate(', '    public void setHdrMode(', '    public int submitFrame(', '    public boolean isDead()', '    public void cleanup()']]
pre=r'''
import java.util.concurrent.locks.ReentrantReadWriteLock;
public class PyroRecovery {
 static final boolean LIBRARY_LOADED=true;static final int SUBMIT_ERROR=-1,RECREATE_AFTER_ERRORS=30;
 static class Surface {boolean isValid(){return true;}}
 static class MoonBridge{static final int DR_OK=0,DR_NEED_IDR=-1;}
 static class LimeLog{static void warning(String s){}static void severe(String s){}}
 static Runnable afterReadUnlock;
 static class HookLock extends ReentrantReadWriteLock {
  final ReadLock read=new ReadLock(this){public void unlock(){super.unlock();Runnable r=afterReadUnlock;afterReadUnlock=null;if(r!=null)r.run();}};
  public ReadLock readLock(){return read;}
 }
 static int creates,destroys,submitResult=-1;static boolean failCreate,hdr;static float peak;
 static long nativeCreate(Surface s,int w,int h,int fps,boolean c,boolean t){creates++;return failCreate?0:creates;}
 static void nativeDestroy(long h){destroys++;}
 static void nativeSetHdrMode(long h,boolean enabled,float p){hdr=enabled;peak=p;}
 static int nativeSubmitFrame(long h,byte[] data,int n){return submitResult;}
 static int failures;
 static void check(boolean ok,String text){System.out.println((ok?"PASS ":"FAIL ")+text);if(!ok)failures++;}
 void setup(){setup(new Surface(),1920,1080,60,false,true);}
 void errors(int count){for(int i=0;i<count;i++)submitFrame(new byte[1],1);}
'''
post=r'''
 public static void main(String[] args){
  PyroRecovery p=new PyroRecovery();p.setup();p.setHdrMode(true,1200);p.errors(30);
  check(creates==2&&!p.isDead()&&hdr&&peak==1200,"one rebuild restores HDR after thirty errors");
  p.errors(30);check(p.isDead()&&creates==2,"a second error streak stops after one rebuild");p.cleanup();
  p=new PyroRecovery();p.setup();p.errors(29);int before=creates;afterReadUnlock=p::cleanup;p.errors(1);
  check(creates==before&&p.handle==0&&!p.isDead(),"cleanup between submit and recovery cannot recreate or report a dead retired renderer");
  p=new PyroRecovery();p.setup();p.errors(29);PyroRecovery current=p;before=creates;afterReadUnlock=current::setup;p.errors(1);
  check(creates==before+1&&p.consecutiveSubmitErrors==0&&!p.isDead(),"old submission cannot alter the replacement session recovery state");p.cleanup();
  p=new PyroRecovery();p.setup();p.errors(20);submitResult=0;p.errors(1);submitResult=-1;p.errors(29);
  check(!p.recreated,"successful submission resets the consecutive error streak");p.cleanup();
  p=new PyroRecovery();p.setup();failCreate=true;p.errors(30);check(p.isDead()&&p.handle==0,"failed rebuild is terminal");
  if(failures>0)System.exit(1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='pyro-recovery-') as d:
 p=pathlib.Path(d);(p/'PyroRecovery.java').write_text(pre+fields+'\n'.join(methods)+post)
 subprocess.run(['java','com.sun.tools.javac.Main','-d',d,str(p/'PyroRecovery.java')],check=True)
 raise SystemExit(subprocess.run(['java','-cp',d,'PyroRecovery']).returncode)
