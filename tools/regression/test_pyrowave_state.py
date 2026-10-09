#!/usr/bin/env python3
"""Run actual MediaCodec setup/HDR/cleanup methods with a fake PyroWave JNI edge."""
import pathlib,subprocess,tempfile,sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
path='app/src/main/java/com/limelight/binding/video/MediaCodecDecoderRenderer.java'
source=subprocess.check_output(['git','show','HEAD:'+path],cwd=ROOT,text=True) if '--baseline' in sys.argv else (ROOT/path).read_text()
def block(marker):
 start=source.index(marker);end=source.index('{',start);depth=1
 while depth:
  end+=1;depth+=(source[end]=='{')-(source[end]=='}')
 return source[start:end+1]
PREFIX=r'''import java.util.*;import java.util.concurrent.atomic.*;
public class PyroState {
 int targetFps,initialWidth,initialHeight,videoFormat,refreshRate,codecRecoveryAttempts;
 Object renderTarget;PyroWaveDecoderRenderer pyroWaveRenderer,videoDecoder=new PyroWaveDecoderRenderer();
 final Object pyroWaveStateLock=new Object();boolean requestedHdrEnabled;byte[] requestedHdrMetadata,currentHdrMetadata;
 AtomicInteger codecRecoveryType=new AtomicInteger();
 static final int CR_RECOVERY_TYPE_NONE=0,CR_RECOVERY_TYPE_RESTART=1,CR_RECOVERY_TYPE_FLUSH=2;
 static class Build{static class VERSION{static final int SDK_INT=35;}static class VERSION_CODES{static final int N=24;}}
 static class MoonBridge{static final int VIDEO_FORMAT_MASK_PYROWAVE=0xf0000,VIDEO_FORMAT_MASK_YUV444=0xa0000,VIDEO_FORMAT_MASK_10BIT=0xc0000;}
 static class LimeLog{static void info(String s){}static void severe(String s){}}
 int initializeDecoder(boolean b){return 0;}
 static class PyroWaveDecoderRenderer{boolean enabled;float peak;boolean dead;
 boolean setup(Object s,int w,int h,int fps,boolean chroma,boolean ten){return true;}
 void setHdrMode(boolean e,float p){if(dead)throw new AssertionError("callback after destroy");enabled=e;peak=p;}
 void cleanup(){dead=true;}void release(){}}
'''
SUFFIX=r'''
 public static void main(String[] args){PyroState state=new PyroState();byte[] metadata=new byte[22];metadata[20]=(byte)0xe8;metadata[21]=3;
 state.setHdrMode(true,metadata);state.setup(0x40000,1920,1080,60);
 boolean early=state.pyroWaveRenderer.enabled && state.pyroWaveRenderer.peak==1000;
 System.out.println((early?"PASS ":"FAIL ")+"HDR received before video setup is applied to the created renderer");
 state.setHdrMode(false,null);boolean disabled=!state.pyroWaveRenderer.enabled;
 state.cleanup();state.setHdrMode(true,metadata);
 System.out.println((disabled?"PASS ":"FAIL ")+"HDR can be disabled and callbacks after cleanup avoid the destroyed renderer");
 if(!early || !disabled)System.exit(1);}
}
'''
markers=['    public int setup(', '    public void setHdrMode(', '    public void cleanup()']
if '    private static float hdrPeakNits(' in source:markers.insert(0,'    private static float hdrPeakNits(')
with tempfile.TemporaryDirectory(prefix='pyro-state-') as directory:
 w=pathlib.Path(directory);(w/'PyroState.java').write_text(PREFIX+'\n'.join(map(block,markers))+SUFFIX)
 subprocess.run(['java','com.sun.tools.javac.Main','-d',str(w),str(w/'PyroState.java')],check=True)
 raise SystemExit(subprocess.run(['java','-cp',str(w),'PyroState']).returncode)
