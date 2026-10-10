#!/usr/bin/env python3
"""Count Mat construction/release calls in the production depth comparison.

OpenCV operations are fakes: this checks allocation/lifetime, not image output,
resident memory, or native GC behavior. --baseline reads the current HEAD.
"""
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
if '--baseline' in sys.argv:
    # The method moved from Stereo3DRenderer to DepthFrameDifference; read it where it lives now.
    source = subprocess.check_output(['git', 'show', 'HEAD:app/src/main/java/com/limelight/utils/DepthFrameDifference.java'], cwd=ROOT, text=True)
    marker = '    private double hasFrameChangedSignificantlyOCV('
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    production = '''class DepthFrameDifference implements AutoCloseable {
      int modelInputWidth,modelInputHeight;
      DepthFrameDifference(int w,int h){modelInputWidth=w;modelInputHeight=h;}
      double compare(ByteBuffer a,ByteBuffer b){return hasFrameChangedSignificantlyOCV(a,b);}
      public void close(){}
    ''' + source[start:end + 1] + '\n}'
else:
    production = (ROOT / 'app/src/main/java/com/limelight/utils/DepthFrameDifference.java').read_text()
    production = re.sub(r'^(package|import) .*?;\n', '', production, flags=re.M)

FAKES = r'''
import java.nio.*;import java.util.*;
class Mat {
 static List<Mat> all=new ArrayList<>();static int made;boolean released;
 Mat(){made++;all.add(this);}Mat(int h,int w,int type,ByteBuffer b){this();}
 void release(){released=true;}
 static long unreleased(){return all.stream().filter(m->!m.released).count();}
 static void reset(){all.clear();made=0;}
}
class MatOfInt extends Mat {MatOfInt(int... v){}}
class MatOfFloat extends Mat {MatOfFloat(float... v){}}
class CvType {static int CV_8UC4=1,CV_16S=2;}
class Core {static void convertScaleAbs(Mat a,Mat b){}static void addWeighted(Mat a,double b,Mat c,double d,double e,Mat f){}}
class Imgproc {
 static int COLOR_RGBA2GRAY=1,HISTCMP_CORREL=1;static boolean failSobel;
 static void cvtColor(Mat a,Mat b,int c){}
 static void Sobel(Mat a,Mat b,int c,int d,int e){if(failSobel)throw new IllegalStateException("Sobel failed");}
 static void calcHist(List<Mat> images,Mat channels,Mat mask,Mat out,Mat bins,Mat ranges){}
 static double compareHist(Mat a,Mat b,int c){return 0.5;}
}
'''
RUNNER = r'''
public class DepthScratch {
 static int failures;
 static void check(boolean ok,String text){System.out.println((ok?"PASS ":"FAIL ")+text);if(!ok)failures++;}
 public static void main(String[] args){
  ByteBuffer a=ByteBuffer.allocateDirect(16),b=ByteBuffer.allocateDirect(16);
  DepthFrameDifference workspace=new DepthFrameDifference(2,2);
  workspace.compare(a,b);int before=Mat.made;
  for(int i=0;i<1000;i++)workspace.compare(a,b);
  int perFrame=(Mat.made-before)/1000;
  check(perFrame<=2,"steady-state Mat constructions per comparison: "+perFrame+" (limit 2)");
  workspace.close();check(Mat.unreleased()==0,"all comparison Mats explicitly released at shutdown (remaining "+Mat.unreleased()+")");
  Mat.reset();workspace=new DepthFrameDifference(2,2);Imgproc.failSobel=true;
  try{workspace.compare(a,b);}catch(IllegalStateException expected){}finally{workspace.close();}
  check(Mat.unreleased()==0,"Sobel failure preserves explicit cleanup (remaining "+Mat.unreleased()+")");
  System.exit(failures==0?0:1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='depth-scratch-') as directory:
    work = pathlib.Path(directory)
    java = work / 'DepthScratch.java'
    java.write_text(FAKES + production + RUNNER)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(java)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'DepthScratch']).returncode)
