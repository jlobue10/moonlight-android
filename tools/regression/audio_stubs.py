"""Deterministic android.media stand-ins shared by the audio regression harnesses.

No sound is played. AudioTrack records writes and plays back scripted results;
AudioTrack.plans supplies the tracks the Builder hands out, in order.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
AUDIO = 'app/src/main/java/com/limelight/binding/audio/'


def production(name):
    path = AUDIO + name + '.java'
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
    return (ROOT / path).read_text(encoding='utf-8')


def stubs():
    return {
'android/content/Intent.java': '''package android.content;
import java.util.*; public class Intent {public String action;public Map<String,Object> extras=new HashMap<>();
public Intent(String a){action=a;} public Intent putExtra(String k,int v){extras.put(k,v);return this;}
public Intent putExtra(String k,String v){extras.put(k,v);return this;}}''',
'android/content/Context.java': '''package android.content;
import java.util.*; public class Context {public List<Intent> broadcasts=new ArrayList<>();
public String getPackageName(){return "test";}public void sendBroadcast(Intent i){broadcasts.add(i);}}''',
'android/media/AudioAttributes.java': '''package android.media; public class AudioAttributes {
public static final int USAGE_GAME=14,USAGE_MEDIA=1,CONTENT_TYPE_MOVIE=3,FLAG_LOW_LATENCY=256;
public int usage,contentType;
public static class Builder {AudioAttributes a=new AudioAttributes();
public Builder setUsage(int i){a.usage=i;return this;}public Builder setContentType(int i){a.contentType=i;return this;}
public Builder setFlags(int i){return this;}public AudioAttributes build(){return a;}}}''',
'android/media/AudioFormat.java': '''package android.media;public class AudioFormat {
public static final int ENCODING_PCM_16BIT=2,ENCODING_AC3=5,ENCODING_DTS=7,ENCODING_IEC61937=13,ENCODING_DOLBY_TRUEHD=14,
CHANNEL_OUT_STEREO=12,CHANNEL_OUT_QUAD=204,CHANNEL_OUT_5POINT1=252;
public int encoding,rate,mask;public static class Builder {AudioFormat f=new AudioFormat();
public Builder setEncoding(int i){f.encoding=i;return this;}public Builder setSampleRate(int i){f.rate=i;return this;}
public Builder setChannelMask(int i){f.mask=i;return this;}public AudioFormat build(){return f;}}}''',
'android/media/AudioManager.java': '''package android.media;public class AudioManager {
public static final int STREAM_MUSIC=3,AUDIO_SESSION_ID_GENERATE=0;}''',
'android/media/audiofx/AudioEffect.java': '''package android.media.audiofx;public class AudioEffect {
public static final String ACTION_OPEN_AUDIO_EFFECT_CONTROL_SESSION="open",ACTION_CLOSE_AUDIO_EFFECT_CONTROL_SESSION="close",
EXTRA_AUDIO_SESSION="session",EXTRA_PACKAGE_NAME="package",EXTRA_CONTENT_TYPE="content";
public static final int CONTENT_TYPE_GAME=1;}''',
'android/os/Build.java': '''package android.os;public class Build {
public static class VERSION {public static int SDK_INT=35;}
public static class VERSION_CODES {public static final int N=24,N_MR1=25,O=26,Q=29;}}''',
'android/os/SystemClock.java': '''package android.os;public class SystemClock {
public static long now;public static long uptimeMillis(){return now;}public static long elapsedRealtime(){return now;}}''',
'com/limelight/LimeLog.java': '''package com.limelight;public class LimeLog {
public static java.util.List<String> lines=new java.util.ArrayList<>();
public static void info(String s){lines.add("I "+s);}public static void warning(String s){lines.add("W "+s);}public static void severe(String s){lines.add("S "+s);}}''',
'com/limelight/nvstream/av/audio/AudioRenderer.java': '''package com.limelight.nvstream.av.audio;
import com.limelight.nvstream.jni.MoonBridge;public interface AudioRenderer {
int setup(MoonBridge.AudioConfiguration c,int r,int n);void playDecodedAudio(short[] d);void start();void stop();void cleanup();}''',
'com/limelight/nvstream/jni/MoonBridge.java': '''package com.limelight.nvstream.jni;public class MoonBridge {
public static int pending;public static int getPendingAudioDuration(){return pending;}
public static class AudioConfiguration {public int channelCount;public AudioConfiguration(int n){channelCount=n;}}}''',
'android/media/AudioTrack.java': r'''package android.media;
import java.util.*;import java.util.concurrent.*;
public class AudioTrack {
 public static final int MODE_STREAM=1,PERFORMANCE_MODE_LOW_LATENCY=1,ERROR_DEAD_OBJECT=-6,ERROR_INVALID_OPERATION=-3,STATE_INITIALIZED=1,STATE_UNINITIALIZED=0;
 public static final Deque<AudioTrack> plans=new ArrayDeque<>();public static final List<AudioTrack> built=new ArrayList<>();
 public static final List<Short> accepted=new ArrayList<>();public static int builds;
 // Encodings the device accepts for direct (bitstream) playback, and the platform minimum buffer
 public static final Set<Integer> directEncodings=new HashSet<>();public static int minBufferSize=8192;
 public final Deque<Integer> results=new ArrayDeque<>();public final List<String> writes=new ArrayList<>();
 public int id,releases,pauses,plays,flushes,bufferSize,acceptedBytes,state=STATE_INITIALIZED;public boolean lowLatency,failBuild,failPlay,block;
 public AudioFormat format;public AudioAttributes attributes;public final CountDownLatch entered=new CountDownLatch(1),unblock=new CountDownLatch(1);
 public AudioTrack(int... returns){for(int n:returns)results.add(n);}
 public AudioTrack(AudioAttributes a,AudioFormat f,int b,int m,int s){id=++builds;format=f;attributes=a;bufferSize=b;built.add(this);}
 public static class Builder {AudioFormat format;AudioAttributes attributes;int buffer;boolean low;
  public Builder setAudioFormat(AudioFormat f){format=f;return this;}public Builder setAudioAttributes(AudioAttributes a){attributes=a;return this;}
  public Builder setTransferMode(int i){return this;}public Builder setBufferSizeInBytes(int n){buffer=n;return this;}
  public Builder setPerformanceMode(int i){low=true;return this;}
  public AudioTrack build(){AudioTrack t=plans.isEmpty()?new AudioTrack():plans.remove();++builds;
   if(t.failBuild)throw new IllegalStateException("scripted unavailable output");
   t.id=builds;t.format=format;t.attributes=attributes;t.bufferSize=buffer;t.lowLatency=low;built.add(t);return t;}}
 public static int getMinBufferSize(int r,int c,int e){return minBufferSize;}
 public static int getNativeOutputSampleRate(int s){return 48000;}
 public static boolean isDirectPlaybackSupported(AudioFormat f,AudioAttributes a){return directEncodings.contains(f.encoding);}
 public int getAudioSessionId(){return id;}public int getState(){return state;}
 public void play(){++plays;if(failPlay)throw new IllegalStateException("scripted play failure");}
 public void pause(){++pauses;unblock.countDown();}public void flush(){++flushes;}public void release(){++releases;}
 public int write(short[] data,int offset,int count){writes.add(offset+":"+count);
  if(block){entered.countDown();try{unblock.await();}catch(InterruptedException e){throw new RuntimeException(e);}return 0;}
  int n=results.isEmpty()?count:results.remove();if(n>0)for(int i=0;i<n;i++)accepted.add(data[offset+i]);return n;}
 public int write(byte[] data,int offset,int count){writes.add(offset+":"+count);
  if(block){entered.countDown();try{unblock.await();}catch(InterruptedException e){throw new RuntimeException(e);}return 0;}
  int n=results.isEmpty()?count:results.remove();if(n>0)acceptedBytes+=n;return n;}
 public static void reset(){plans.clear();built.clear();accepted.clear();directEncodings.clear();minBufferSize=8192;builds=0;android.os.SystemClock.now=0;}
}
''',
    }


def production_files(*names):
    return {AUDIO + name + '.java': production(name) for name in names}
