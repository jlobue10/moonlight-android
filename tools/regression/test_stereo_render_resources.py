#!/usr/bin/env python3
"""Check production shader lifetime and overlay timing with deterministic GL/time.

Shader compilation/linking is a fake boundary; this does not measure GPU memory,
driver timing or real FPS. --baseline reads the committed renderer.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/utils/Stereo3DRenderer.java'
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


if 'private void updatePerformanceStats(' in source:
    timing = block('    private void updatePerformanceStats(')
else:
    begin = source.index('            if (lastFpsTime == 0)')
    end = source.index('\n        }\n    }', begin)
    timing = 'void updatePerformanceStats(long startTime,long endTime,DepthSession depth){\n' + source[begin:end] + '\n}'

PREFIX = r'''
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
public class StereoRenderResources {
 long lastFpsTime,totalDrawTime;
 float drawDelay,fps=60,calcFps,threeDFps,calcThreeDFps;
 int depthMapResultCount;
 Boolean isDebugMode=false;
 static class DepthSession {
  AtomicInteger completedDepthFrames=new AtomicInteger();
  ArrayBlockingQueue<Object> freeInputBuffers=new ArrayBlockingQueue<>(10),
   inferenceInputQueue=new ArrayBlockingQueue<>(1),filledOutputBuffers=new ArrayBlockingQueue<>(6),
   freeSmoothedBuffers=new ArrayBlockingQueue<>(3);
 }
 static class Log {static void d(String a,String b){}}
 static class LimeLog {static void severe(String s){}}
 static class GLES20 {
  static final int GL_VERTEX_SHADER=1,GL_FRAGMENT_SHADER=2,GL_COMPILE_STATUS=3,GL_LINK_STATUS=4,GL_TRUE=1;
  static int next,failCompileType;static boolean failLink,failCreate,throwLink;
  static Set<Integer> shaders=new HashSet<>(),programs=new HashSet<>();
  static Map<Integer,Integer> types=new HashMap<>();
  static int glCreateShader(int type){int id=++next;shaders.add(id);types.put(id,type);return id;}
  static void glShaderSource(int shader,String s){}static void glCompileShader(int shader){}
  static void glGetShaderiv(int id,int what,int[] out,int offset){out[offset]=types.get(id)==failCompileType?0:1;}
  static String glGetShaderInfoLog(int shader){return "compile failed";}
  // Track deletion requests, including requests for attached shaders: GL keeps
  // the linked executable alive until its program is deleted.
  static void glDeleteShader(int shader){shaders.remove(shader);}
  static int glCreateProgram(){if(failCreate)return 0;int id=++next;programs.add(id);return id;}
  static void glAttachShader(int p,int s){}
  static void glLinkProgram(int p){if(throwLink)throw new IllegalStateException("link boundary");}
  static void glGetProgramiv(int p,int what,int[] out,int offset){out[offset]=failLink?0:1;}
  static String glGetProgramInfoLog(int p){return "link failed";}
  static void glDeleteProgram(int p){programs.remove(p);}
  static void reset(){next=0;failCompileType=0;failLink=failCreate=throwLink=false;shaders.clear();programs.clear();types.clear();}
 }
 static int failures;
 static void check(boolean ok,String label){System.out.println((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 public static void main(String[] args){
  StereoRenderResources r=new StereoRenderResources();
  for(int i=0;i<100;i++)GLES20.glDeleteProgram(r.createProgram("vertex","fragment"));
  check(GLES20.shaders.isEmpty() && GLES20.programs.isEmpty(),
        "100 successful shader programs leave no undeleted shader objects (remaining="+GLES20.shaders.size()+")");
  GLES20.reset();GLES20.failCompileType=GLES20.GL_FRAGMENT_SHADER;
  check(r.createProgram("v","f")==0 && GLES20.shaders.isEmpty(),"fragment compile failure releases the vertex shader");
  GLES20.reset();GLES20.failLink=true;
  check(r.createProgram("v","f")==0 && GLES20.shaders.isEmpty() && GLES20.programs.isEmpty(),"link failure releases both shaders and the program");
  GLES20.reset();GLES20.failCreate=true;
  check(r.createProgram("v","f")==0 && GLES20.shaders.isEmpty(),"program allocation failure releases both shaders");
  GLES20.reset();GLES20.throwLink=true;
  try {r.createProgram("v","f");}catch(IllegalStateException expected){}
  check(GLES20.shaders.isEmpty() && GLES20.programs.isEmpty(),"exceptional link cleanup releases all acquired objects");
  DepthSession depth=new DepthSession();
  for(int i=0;i<=50;i++){
   long start=1_000_000_000L+i*20_000_000L;
   depth.completedDepthFrames.incrementAndGet();r.calcThreeDFps++;
   r.updatePerformanceStats(start,start+2_000_000L,depth);
  }
  check(Math.abs(r.drawDelay-2.0f)<0.001f,"2 ms draw duration is reported as 2 ms (actual="+r.drawDelay+")");
  float expected=51f/1.002f;
  check(Math.abs(r.fps-expected)<0.001f && Math.abs(r.threeDFps-expected)<0.001f,
        "frame rates include the boundary frame and use elapsed seconds (render="+r.fps+", depth="+r.threeDFps+")");
  System.exit(failures==0?0:1);
 }
'''
java = PREFIX + block('    private int loadShader(') + block('    private int createProgram(') + timing + '\n}'
with tempfile.TemporaryDirectory(prefix='stereo-render-resources-') as directory:
    work = pathlib.Path(directory)
    (work / 'StereoRenderResources.java').write_text(java)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(work / 'StereoRenderResources.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'StereoRenderResources']).returncode)
