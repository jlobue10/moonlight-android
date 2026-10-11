#!/usr/bin/env python3
"""Run the full AndroidAudioRenderer against scripted AudioTrack/lifecycle boundaries.

No sound is played. --baseline reads HEAD to reproduce dropped partial writes,
a dead track that never recovers, and stop waiting on a blocked decoder write.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/binding/audio/AndroidAudioRenderer.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT/PATH).read_text())
files = {
'com/limelight/binding/audio/AndroidAudioRenderer.java': source,
'android/content/Intent.java': '''package android.content;
import java.util.*; public class Intent {public String action;public Map<String,Object> extras=new HashMap<>();
public Intent(String a){action=a;} public Intent putExtra(String k,int v){extras.put(k,v);return this;}
public Intent putExtra(String k,String v){extras.put(k,v);return this;}}''',
'android/content/Context.java': '''package android.content;
import java.util.*; public class Context {public List<Intent> broadcasts=new ArrayList<>();
public String getPackageName(){return "test";}public void sendBroadcast(Intent i){broadcasts.add(i);}}''',
'android/media/AudioAttributes.java': '''package android.media; public class AudioAttributes {
public static final int USAGE_GAME=1,FLAG_LOW_LATENCY=2;
public static class Builder {public Builder setUsage(int i){return this;}public Builder setFlags(int i){return this;}
public AudioAttributes build(){return new AudioAttributes();}}}''',
'android/media/AudioFormat.java': '''package android.media;public class AudioFormat {
public static final int ENCODING_PCM_16BIT=2,CHANNEL_OUT_STEREO=12,CHANNEL_OUT_QUAD=204,CHANNEL_OUT_5POINT1=252;
public int rate,mask;public static class Builder {AudioFormat f=new AudioFormat();
public Builder setEncoding(int i){return this;}public Builder setSampleRate(int i){f.rate=i;return this;}
public Builder setChannelMask(int i){f.mask=i;return this;}public AudioFormat build(){return f;}}}''',
'android/media/AudioManager.java': '''package android.media;public class AudioManager {
public static final int STREAM_MUSIC=3,AUDIO_SESSION_ID_GENERATE=0;}''',
'android/media/audiofx/AudioEffect.java': '''package android.media.audiofx;public class AudioEffect {
public static final String ACTION_OPEN_AUDIO_EFFECT_CONTROL_SESSION="open",ACTION_CLOSE_AUDIO_EFFECT_CONTROL_SESSION="close",
EXTRA_AUDIO_SESSION="session",EXTRA_PACKAGE_NAME="package",EXTRA_CONTENT_TYPE="content";
public static final int CONTENT_TYPE_GAME=1;}''',
'android/os/Build.java': '''package android.os;public class Build {
public static class VERSION {public static int SDK_INT=35;}public static class VERSION_CODES {public static final int O=26;}}''',
'android/os/SystemClock.java': '''package android.os;public class SystemClock {
public static long now;public static long uptimeMillis(){return now;}public static long elapsedRealtime(){return now;}}''',
'com/limelight/LimeLog.java': '''package com.limelight;public class LimeLog {
public static void info(String s){}public static void warning(String s){}public static void severe(String s){}}''',
'com/limelight/nvstream/av/audio/AudioRenderer.java': '''package com.limelight.nvstream.av.audio;
import com.limelight.nvstream.jni.MoonBridge;public interface AudioRenderer {
int setup(MoonBridge.AudioConfiguration c,int r,int n);void playDecodedAudio(short[] d);void start();void stop();void cleanup();}''',
'com/limelight/nvstream/jni/MoonBridge.java': '''package com.limelight.nvstream.jni;public class MoonBridge {
public static int pending;public static int getPendingAudioDuration(){return pending;}
public static class AudioConfiguration {public int channelCount;public AudioConfiguration(int n){channelCount=n;}}}''',
'android/media/AudioTrack.java': r'''package android.media;
import java.util.*;import java.util.concurrent.*;
public class AudioTrack {
 public static final int MODE_STREAM=1,PERFORMANCE_MODE_LOW_LATENCY=1,ERROR_DEAD_OBJECT=-6,ERROR_INVALID_OPERATION=-3;
 public static final Deque<AudioTrack> plans=new ArrayDeque<>();public static final List<AudioTrack> built=new ArrayList<>();
 public static final List<Short> accepted=new ArrayList<>();public static int builds;
 public final Deque<Integer> results=new ArrayDeque<>();public final List<String> writes=new ArrayList<>();
 public int id,releases,pauses,plays,flushes,bufferSize;public boolean lowLatency,failBuild,failPlay,block;
 public AudioFormat format;public final CountDownLatch entered=new CountDownLatch(1),unblock=new CountDownLatch(1);
 public AudioTrack(int... returns){for(int n:returns)results.add(n);}
 public AudioTrack(AudioAttributes a,AudioFormat f,int b,int m,int s){id=++builds;format=f;bufferSize=b;built.add(this);}
 public static class Builder {AudioFormat format;int buffer;boolean low;
  public Builder setAudioFormat(AudioFormat f){format=f;return this;}public Builder setAudioAttributes(AudioAttributes a){return this;}
  public Builder setTransferMode(int i){return this;}public Builder setBufferSizeInBytes(int n){buffer=n;return this;}
  public Builder setPerformanceMode(int i){low=true;return this;}
  public AudioTrack build(){AudioTrack t=plans.isEmpty()?new AudioTrack():plans.remove();++builds;
   if(t.failBuild)throw new IllegalStateException("scripted unavailable output");
   t.id=builds;t.format=format;t.bufferSize=buffer;t.lowLatency=low;built.add(t);return t;}}
 public static int getMinBufferSize(int r,int c,int e){return 8192;}
 public static int getNativeOutputSampleRate(int s){return 48000;}
 public int getAudioSessionId(){return id;}
 public void play(){++plays;if(failPlay)throw new IllegalStateException("scripted play failure");}
 public void pause(){++pauses;unblock.countDown();}public void flush(){++flushes;}public void release(){++releases;}
 public int write(short[] data,int offset,int count){writes.add(offset+":"+count);
  if(block){entered.countDown();try{unblock.await();}catch(InterruptedException e){throw new RuntimeException(e);}return 0;}
  int n=results.isEmpty()?count:results.remove();if(n>0)for(int i=0;i<n;i++)accepted.add(data[offset+i]);return n;}
 public static void reset(){plans.clear();built.clear();accepted.clear();builds=0;android.os.SystemClock.now=0;}
}
''',
'AudioRecovery.java': r'''
import android.content.*;import android.media.*;import android.os.*;
import com.limelight.binding.audio.AndroidAudioRenderer;import com.limelight.nvstream.jni.MoonBridge;
import java.util.*;import java.util.concurrent.*;
public class AudioRecovery {
 static int checks,failures;static final short[] DATA={1,2,3,4,5,6};
 static void check(boolean ok,String name){++checks;if(!ok)++failures;System.out.println((ok?"PASS ":"FAIL ")+name);}
 static AndroidAudioRenderer setup(Context c,boolean fx,AudioTrack... tracks){
  AudioTrack.reset();MoonBridge.pending=0;Collections.addAll(AudioTrack.plans,tracks);
  AndroidAudioRenderer r=new AndroidAudioRenderer(c,fx);check(r.setup(new MoonBridge.AudioConfiguration(2),48000,240)==0,"setup succeeds");r.start();return r;}
 static boolean accepted(){return AudioTrack.accepted.equals(Arrays.asList((short)1,(short)2,(short)3,(short)4,(short)5,(short)6));}
 public static void main(String[] args)throws Exception{
  AudioTrack first=new AudioTrack(2,4);AndroidAudioRenderer r=setup(new Context(),false,first);r.playDecodedAudio(DATA);
  check(accepted()&&first.writes.equals(Arrays.asList("0:6","2:4")),"partial write retries only the remaining PCM samples");r.stop();r.cleanup();
  first=new AudioTrack(2,AudioTrack.ERROR_DEAD_OBJECT);AudioTrack replacement=new AudioTrack();Context context=new Context();
  r=setup(context,true,first,replacement);r.playDecodedAudio(DATA);
  check(accepted()&&replacement.writes.equals(Arrays.asList("2:4")),"dead track after a partial write resumes at the unaccepted suffix");
  check(first.releases==1&&AudioTrack.builds==2&&replacement.plays==1,"dead track is retired and replacement starts once");
  check(replacement.format!=null&&replacement.format.rate==48000&&replacement.format.mask==12&&replacement.bufferSize==1920&&!replacement.lowLatency,
   "replacement preserves the successful playback configuration");
  r.stop();r.cleanup();try{r.cleanup();}catch(Exception e){check(false,"cleanup does not throw twice");}
  List<String> effects=new ArrayList<>();for(Intent i:context.broadcasts)effects.add(i.action+":"+i.extras.get("session"));
  check(effects.equals(Arrays.asList("open:1","close:1","open:2","close:2")),"effect sessions follow replacement and close on stop");
  check(first.releases==1&&replacement.releases==1,"cleanup releases each track only once");
  first=new AudioTrack(AudioTrack.ERROR_DEAD_OBJECT);AudioTrack unavailable=new AudioTrack();unavailable.failBuild=true;replacement=new AudioTrack();
  r=setup(new Context(),false,first,unavailable,replacement);for(int i=0;i<100;i++)r.playDecodedAudio(DATA);
  check(AudioTrack.builds==2&&AudioTrack.accepted.isEmpty(),"failed recreation is rate limited while audio continues arriving");
  SystemClock.now=999;r.playDecodedAudio(DATA);check(AudioTrack.builds==2,"recreation waits through the cooldown");
  SystemClock.now=1000;r.playDecodedAudio(DATA);check(AudioTrack.builds==3&&accepted(),"a later callback recovers once the output is available");r.stop();r.cleanup();
  first=new AudioTrack(AudioTrack.ERROR_DEAD_OBJECT);replacement=new AudioTrack(AudioTrack.ERROR_DEAD_OBJECT);
  r=setup(new Context(),false,first,replacement);r.playDecodedAudio(DATA);
  check(AudioTrack.builds==2&&first.releases==1&&replacement.releases==1,"immediately dead replacements cannot loop within a callback");
  for(int i=0;i<100;i++)r.playDecodedAudio(DATA);
  check(AudioTrack.builds==2,"persistent dead output remains rate limited");r.stop();r.cleanup();
  first=new AudioTrack(AudioTrack.ERROR_DEAD_OBJECT);replacement=new AudioTrack();replacement.failPlay=true;
  r=setup(new Context(),false,first,replacement);r.playDecodedAudio(DATA);
  check(replacement.releases==1&&AudioTrack.builds==2,"replacement playback failure releases the partly initialized track");
  r.stop();SystemClock.now=2000;r.playDecodedAudio(DATA);
  check(AudioTrack.builds==2,"stop suppresses a pending recovery retry");r.cleanup();
  first=new AudioTrack(0);r=setup(new Context(),false,first);r.playDecodedAudio(DATA);
  check(first.writes.size()==1&&AudioTrack.builds==1,"zero progress does not spin or recreate the track");r.stop();r.cleanup();
  first=new AudioTrack(AudioTrack.ERROR_INVALID_OPERATION);r=setup(new Context(),false,first);r.playDecodedAudio(DATA);
  check(first.writes.size()==1&&AudioTrack.builds==1,"other write errors are bounded");r.stop();r.cleanup();
  first=new AudioTrack();r=setup(new Context(),false,first);MoonBridge.pending=40;r.playDecodedAudio(DATA);
  check(first.writes.isEmpty(),"backlog guard is preserved");MoonBridge.pending=0;first.block=true;
  final AndroidAudioRenderer active=r;Thread decoder=new Thread(()->active.playDecodedAudio(DATA));decoder.start();
  check(first.entered.await(2,TimeUnit.SECONDS),"decoder enters blocking audio write");r.stop();decoder.join(300);
  check(!decoder.isAlive(),"stop interrupts a blocked write before native decoder join");
  first.pause();decoder.join(2000);int writes=first.writes.size();r.playDecodedAudio(DATA);
  check(first.writes.size()==writes&&AudioTrack.builds==1,"stopped renderer neither writes nor recreates tracks");r.cleanup();
  System.out.println(checks+" checks, "+failures+" failures");System.exit(failures==0?0:1);
 }
}
'''}
with tempfile.TemporaryDirectory(prefix='audio-track-recovery-') as temp:
    work=Path(temp)
    for name,content in files.items():
        path=work/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
    subprocess.run(['java','com.sun.tools.javac.Main','-d',str(work)]+[str(work/name) for name in files],check=True)
    raise SystemExit(subprocess.run(['java','-cp',str(work),'AudioRecovery'],timeout=15).returncode)
