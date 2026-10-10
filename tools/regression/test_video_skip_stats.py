#!/usr/bin/env python3
"""Exercise production frame-gap accounting with native skip counts, without JNI/GPU."""
import pathlib, subprocess, sys, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
PATH='app/src/main/java/com/limelight/binding/video/MediaCodecDecoderRenderer.java'
source=(subprocess.check_output(['git','show','HEAD:'+PATH],cwd=ROOT,text=True)
        if '--baseline' in sys.argv else (ROOT/PATH).read_text())
if '    private void recordFrameStats(' in source:
 start=source.index('    private void recordFrameStats(');end=source.index('{',start);depth=1
 while depth:
  end+=1;depth+=(source[end]=='{')-(source[end]=='}')
 method=source[start:end+1]
else:
 start=source.index('        if (lastFrameNumber == 0) {',source.index('    public int submitDecodeUnit('))
 end=source.index('        // Reset CSD data',start)
 method='private void recordFrameStats(int frameNumber){'+source[start:end]+'}'
stats=(ROOT/'app/src/main/java/com/limelight/binding/video/VideoStats.java').read_text()
stats=stats.replace('package com.limelight.binding.video;','').replace('import android.os.SystemClock;','')
prefix=r'''
class SystemClock {static long uptimeMillis(){return 1000;}}
public class VideoSkipStats {
 static class MoonBridge {static final int VIDEO_FORMAT_MASK_PYROWAVE=0xf0000;static int skips;static int getSkippedVideoFrames(){return skips;}}
 int videoFormat=0x10000,lastFrameNumber,lastSkippedVideoFrames;
 VideoStats activeWindowVideoStats=new VideoStats();
 static int failures;
 static void check(boolean ok,String message){System.out.println((ok?"PASS ":"FAIL ")+message);if(!ok)++failures;}
 void frame(int number,int skipped){MoonBridge.skips=skipped;recordFrameStats(number);lastFrameNumber=number;activeWindowVideoStats.totalFramesReceived++;activeWindowVideoStats.totalFrames++;}
'''
suffix=r'''
 public static void main(String[] args){
  VideoSkipStats s=new VideoSkipStats();s.frame(1,0);s.frame(4,2);
  check(s.activeWindowVideoStats.framesLost==0 && s.activeWindowVideoStats.frameLossEvents==0 && s.activeWindowVideoStats.totalFramesReceived==4 && s.activeWindowVideoStats.totalFrames==4,"two locally skipped complete frames are received, not network losses");
  s=new VideoSkipStats();s.frame(1,0);s.frame(6,2);
  check(s.activeWindowVideoStats.framesLost==2 && s.activeWindowVideoStats.frameLossEvents==1 && s.activeWindowVideoStats.totalFramesReceived==4 && s.activeWindowVideoStats.totalFrames==6,"mixed queue skips and true loss retain the two real network losses");
  s=new VideoSkipStats();s.frame(3,2);
  check(s.activeWindowVideoStats.totalFramesReceived==3 && s.activeWindowVideoStats.totalFrames==3,"skips before the first submitted frame are counted");
  s=new VideoSkipStats();s.videoFormat=1;s.frame(1,0);s.frame(4,2);
  check(s.activeWindowVideoStats.framesLost==2 && s.activeWindowVideoStats.totalFramesReceived==2,"inter-frame codec loss accounting is unchanged");
  s=new VideoSkipStats();s.lastSkippedVideoFrames=-2;s.lastFrameNumber=10;s.frame(14,1);
  check(s.activeWindowVideoStats.framesLost==0 && s.activeWindowVideoStats.totalFramesReceived==4,"native uint32 skip counter rollover preserves its delta");
  VideoStats copied=new VideoStats();copied.copy(s.activeWindowVideoStats);
  check(copied.totalFramesSkipped==3 && copied.getSubmittedFrames()==1,"window copy retains skipped and submitted frame counts");
  VideoStats combined=new VideoStats();combined.add(copied);combined.add(copied);
  check(combined.totalFramesSkipped==6 && combined.getSubmittedFrames()==2,"window aggregation excludes skipped frames from latency denominators");
  combined.clear();
  check(combined.totalFramesSkipped==0 && combined.getSubmittedFrames()==0,"window clear resets skip accounting");
  if(failures!=0)System.exit(1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='video-skip-stats-') as directory:
 p=pathlib.Path(directory);(p/'VideoSkipStats.java').write_text(prefix+method+suffix+'\n'+stats)
 subprocess.run(['java','com.sun.tools.javac.Main','-d',str(p),str(p/'VideoSkipStats.java')],check=True)
 raise SystemExit(subprocess.run(['java','-cp',str(p),'VideoSkipStats']).returncode)
