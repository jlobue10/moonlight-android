#!/usr/bin/env python3
"""Run the full AndroidAudioRenderer against scripted AudioTrack/lifecycle boundaries.

No sound is played. --baseline reads HEAD to reproduce dropped partial writes,
a dead track that never recovers, and stop waiting on a blocked decoder write.
The android.media stand-ins live in audio_stubs.py (shared with test_audio_passthrough.py).
"""
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_stubs  # noqa: E402

files = audio_stubs.stubs()
files.update(audio_stubs.production_files('AndroidAudioRenderer', 'PassthroughCodec', 'PassthroughOutput'))
files['AudioRecovery.java'] = r'''
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
'''
with tempfile.TemporaryDirectory(prefix='audio-track-recovery-') as temp:
    work = Path(temp)
    for name, content in files.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-encoding', 'UTF-8', '-d', str(work)]
                   + [str(work / name) for name in files], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'AudioRecovery'], timeout=15).returncode)
