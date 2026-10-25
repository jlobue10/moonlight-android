#!/usr/bin/env python3
"""Run the full AndroidAudioRenderer and PassthroughOutput with a fake bitstream encoder.

No sound is played and no JNI library is loaded: the encoder is a Java fake that
turns every 1536 (AC-3) or 512 (DTS) samples per channel into one write unit.
AudioTracks come from the scripted stand-ins in audio_stubs.py, which also decide
which encodings the "device" accepts for direct playback.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_stubs  # noqa: E402

files = audio_stubs.stubs()
files.update(audio_stubs.production_files('AndroidAudioRenderer', 'PassthroughCodec', 'PassthroughOutput'))
files['AudioPassthrough.java'] = r'''
import android.content.*;import android.media.*;import android.os.*;
import com.limelight.LimeLog;
import com.limelight.binding.audio.*;import com.limelight.nvstream.jni.MoonBridge;
import java.util.*;import java.util.concurrent.*;
public class AudioPassthrough {
 static int checks,failures;
 static void check(boolean ok,String name){++checks;if(!ok)++failures;System.out.println((ok?"PASS ":"FAIL ")+name);}

 // Emits one write unit of unitBytes marker bytes per writeUnitSamples samples fed in
 static class FakeEncoder implements PassthroughOutput.Encoder {
  final PassthroughCodec codec;final boolean iec;final int channels;long samplesIn,samplesOut;int releases;boolean fail;
  byte[] out=new byte[0];
  FakeEncoder(PassthroughCodec c,int channels,boolean iec){codec=c;this.channels=channels;this.iec=iec;}
  public PassthroughCodec getCodec(){return codec;}
  public int getWriteSize(){return codec.writeUnitSamples;}
  public int getCodecDelay(){return 0;}
  public long getBufferedSamples(){return samplesIn-samplesOut;}
  public long getSamplesOutput(){return samplesOut;}
  public int encode(short[] pcm){
   if(fail)return -1;
   samplesIn+=pcm.length/channels;int unit=iec?codec.iec61937BurstBytes():codec.writeUnitBytes;int units=0;
   while(samplesOut+codec.writeUnitSamples<=samplesIn){samplesOut+=codec.writeUnitSamples;units++;}
   int len=units*unit;if(out.length<len)out=new byte[len];Arrays.fill(out,0,len,(byte)0x5A);return len;}
  public byte[] getOutput(){return out;}
  public void release(){releases++;}
 }
 static class FakeFactory implements PassthroughOutput.EncoderFactory {
  final Set<PassthroughCodec> supported=EnumSet.allOf(PassthroughCodec.class);boolean available=true;
  final Set<String> failCreate=new HashSet<>();final List<FakeEncoder> created=new ArrayList<>();
  public boolean isAvailable(){return available;}
  public boolean isSupported(PassthroughCodec c){return supported.contains(c);}
  public PassthroughOutput.Encoder create(PassthroughCodec c,int rate,int channels,boolean iec){
   if(failCreate.contains(c.name()+(iec?"/iec":"/raw")))throw new IllegalStateException("scripted encoder failure");
   FakeEncoder e=new FakeEncoder(c,channels,iec);created.add(e);return e;}
 }
 static final int IEC=AudioFormat.ENCODING_IEC61937,AC3=AudioFormat.ENCODING_AC3,DTS=AudioFormat.ENCODING_DTS,PCM=AudioFormat.ENCODING_PCM_16BIT;
 static AudioTrack track(){return AudioTrack.built.get(AudioTrack.built.size()-1);}
 static AndroidAudioRenderer open(Context c,boolean fx,PassthroughCodec codec,int frames,FakeFactory f,int channels,AudioTrack... tracks){
  // reset() clears the device's direct encodings too; the test configured them just before calling open()
  Set<Integer> direct=new HashSet<>(AudioTrack.directEncodings);AudioTrack.reset();AudioTrack.directEncodings.addAll(direct);
  MoonBridge.pending=0;LimeLog.lines.clear();Collections.addAll(AudioTrack.plans,tracks);
  AndroidAudioRenderer r=new AndroidAudioRenderer(c,fx,codec,frames,f);
  check(r.setup(new MoonBridge.AudioConfiguration(channels),48000,240)==0,"setup succeeds");r.start();return r;}
 static short[] pcm(int samplesPerChannel){return new short[samplesPerChannel*6];}

 public static void main(String[] args)throws Exception{
  // 1. IEC 61937 is preferred when the device accepts it: stereo mask, whole bursts, auto buffer >= platform minimum
  FakeFactory f=new FakeFactory();AudioTrack.reset();AudioTrack.directEncodings.addAll(Arrays.asList(IEC,AC3));
  AndroidAudioRenderer r=open(new Context(),false,PassthroughCodec.AC3,0,f,6);
  AudioTrack t=track();
  check(t.format.encoding==IEC&&t.format.mask==12,"AC-3 opens as IEC 61937 bursts on a stereo track when the device accepts it");
  check(t.bufferSize==12288,"auto buffer is the larger of the platform minimum and two bursts, rounded to whole bursts (12288)");
  check(f.created.size()==1&&f.created.get(0).iec,"exactly one encoder is created, in IEC 61937 mode");
  check(t.plays==1&&t.attributes.usage==AudioAttributes.USAGE_MEDIA&&t.attributes.contentType==AudioAttributes.CONTENT_TYPE_MOVIE,
   "the bitstream track plays once with media/movie attributes");
  r.stop();r.cleanup();

  // 2. An explicit buffer setting is honoured in whole bursts
  AudioTrack.reset();AudioTrack.directEncodings.add(IEC);f=new FakeFactory();
  r=open(new Context(),false,PassthroughCodec.AC3,1,f,6);
  check(track().bufferSize==6144,"a 1-frame buffer setting gives exactly one burst (6144 bytes)");r.stop();r.cleanup();

  // 3. Fallback chain: TrueHD requested, build only has AC-3
  AudioTrack.reset();AudioTrack.directEncodings.add(IEC);f=new FakeFactory();f.supported.clear();f.supported.add(PassthroughCodec.AC3);
  r=open(new Context(),false,PassthroughCodec.TRUEHD,0,f,6);
  check(f.created.size()==1&&f.created.get(0).codec==PassthroughCodec.AC3&&track().format.encoding==IEC,"TrueHD falls back to Dolby Digital when the build lacks the other encoders");
  check(LimeLog.lines.stream().anyMatch(l->l.contains("fell back from Dolby TrueHD to Dolby Digital")),"the fallback is logged");r.stop();r.cleanup();

  // 4. Raw encoding when IEC 61937 is refused: 5.1 mask first
  AudioTrack.reset();AudioTrack.directEncodings.add(DTS);f=new FakeFactory();
  r=open(new Context(),false,PassthroughCodec.DTS,0,f,6);
  t=track();
  check(t.format.encoding==DTS&&t.format.mask==252&&!f.created.get(0).iec,"DTS opens as a raw bitstream declared as 5.1 when IEC 61937 is not accepted");
  check(t.bufferSize==8192,"raw buffer is the platform minimum when that exceeds two frames");r.stop();r.cleanup();

  // 5. Nothing accepted by the device: PCM 5.1 fallback, no encoder
  AudioTrack.reset();f=new FakeFactory();
  r=open(new Context(),false,PassthroughCodec.AC3,0,f,6);
  check(track().format.encoding==PCM&&track().format.mask==252&&f.created.isEmpty(),"a device that accepts no bitstream falls back to 5.1 PCM");
  check(LimeLog.lines.stream().anyMatch(l->l.contains("Passthrough unavailable, falling back to PCM")),"the PCM fallback is logged");r.stop();r.cleanup();

  // 6. Stereo never uses passthrough
  AudioTrack.reset();AudioTrack.directEncodings.add(IEC);f=new FakeFactory();
  r=open(new Context(),false,PassthroughCodec.AC3,0,f,2);
  check(track().format.encoding==PCM&&f.created.isEmpty(),"stereo streams stay PCM even with a passthrough codec selected");r.stop();r.cleanup();

  // 7. Frames are written only when complete, as whole bursts
  AudioTrack.reset();AudioTrack.directEncodings.add(IEC);f=new FakeFactory();
  r=open(new Context(),false,PassthroughCodec.AC3,0,f,6);t=track();
  for(int i=0;i<3;i++)r.playDecodedAudio(pcm(384));
  check(t.writes.isEmpty(),"no bitstream is written before a whole frame has been encoded");
  r.playDecodedAudio(pcm(384));
  check(t.writes.equals(Arrays.asList("0:6144"))&&t.acceptedBytes==6144,"the fourth 8 ms chunk completes the 32 ms frame and one 6144-byte burst is written");
  r.playDecodedAudio(pcm(1536*2));
  check(t.writes.size()==2&&t.writes.get(1).equals("0:12288"),"a large chunk yields every completed burst in one write");r.stop();r.cleanup();

  // 8. Partial writes retry the remaining bytes
  AudioTrack.reset();AudioTrack.directEncodings.add(IEC);f=new FakeFactory();AudioTrack scripted=new AudioTrack(1000,5144);
  r=open(new Context(),false,PassthroughCodec.AC3,0,f,6,scripted);
  r.playDecodedAudio(pcm(1536));
  check(scripted.writes.equals(Arrays.asList("0:6144","1000:5144"))&&scripted.acceptedBytes==6144,"a partial bitstream write retries only the remaining bytes");r.stop();r.cleanup();

  // 9. Dead output: identical bitstream track is rebuilt, encoder survives, retries are rate limited
  AudioTrack.reset();AudioTrack.directEncodings.add(IEC);f=new FakeFactory();AudioTrack dead=new AudioTrack(AudioTrack.ERROR_DEAD_OBJECT);AudioTrack again=new AudioTrack();
  Context ctx=new Context();r=open(ctx,true,PassthroughCodec.AC3,0,f,6,dead,again);
  r.playDecodedAudio(pcm(1536));
  check(dead.releases==1&&AudioTrack.builds==2&&again.plays==1,"a dead bitstream output is retired and a replacement started once");
  check(again.format.encoding==IEC&&again.format.mask==12&&again.bufferSize==dead.bufferSize&&again.attributes.usage==AudioAttributes.USAGE_MEDIA,"the replacement repeats the accepted bitstream configuration");
  check(again.writes.equals(Arrays.asList("0:6144")),"the frame that hit the dead output is written to the replacement");
  check(f.created.size()==1&&f.created.get(0).releases==0,"the encoder is kept across the output replacement");
  check(ctx.broadcasts.isEmpty(),"no audio effect session is opened for a bitstream even with effects enabled");
  r.stop();r.cleanup();
  check(f.created.get(0).releases==1&&dead.releases==1&&again.releases==1,"cleanup releases the encoder and each track once");
  r.cleanup();check(f.created.get(0).releases==1,"a second cleanup releases nothing twice");

  AudioTrack.reset();AudioTrack.directEncodings.add(IEC);f=new FakeFactory();dead=new AudioTrack(AudioTrack.ERROR_DEAD_OBJECT);AudioTrack unavailable=new AudioTrack();unavailable.failBuild=true;again=new AudioTrack();
  r=open(new Context(),false,PassthroughCodec.AC3,0,f,6,dead,unavailable,again);
  for(int i=0;i<100;i++)r.playDecodedAudio(pcm(1536));
  check(AudioTrack.builds==2,"failed bitstream recreation is rate limited while audio keeps arriving");
  SystemClock.now=1000;r.playDecodedAudio(pcm(1536));
  check(AudioTrack.builds==3&&again.writes.size()==1,"a later frame recovers once the output is back");r.stop();r.cleanup();

  // 10. Encoder failure is bounded and logged once
  AudioTrack.reset();AudioTrack.directEncodings.add(IEC);f=new FakeFactory();
  r=open(new Context(),false,PassthroughCodec.AC3,0,f,6);t=track();f.created.get(0).fail=true;
  for(int i=0;i<5;i++)r.playDecodedAudio(pcm(1536));
  check(t.writes.isEmpty()&&AudioTrack.builds==1,"an encoder error writes nothing and does not recreate the track");
  check(LimeLog.lines.stream().filter(l->l.contains("Passthrough encode failed")).count()==1,"the encoder error is logged once");r.stop();r.cleanup();

  // 11. Encoder refuses IEC mode: the IEC track is released and the raw encoding is used
  AudioTrack.reset();AudioTrack.directEncodings.addAll(Arrays.asList(IEC,AC3));f=new FakeFactory();f.failCreate.add("AC3/iec");
  r=open(new Context(),false,PassthroughCodec.AC3,0,f,6);
  check(AudioTrack.built.get(0).releases==1&&AudioTrack.built.get(0).plays==0,"the IEC track is released without playing when its encoder cannot be created");
  check(track().format.encoding==AC3&&track().format.mask==252&&f.created.size()==1&&!f.created.get(0).iec,"the raw AC-3 encoding is used instead");r.stop();r.cleanup();

  // 12. stop() interrupts a blocked bitstream write
  AudioTrack.reset();AudioTrack.directEncodings.add(IEC);f=new FakeFactory();AudioTrack blocking=new AudioTrack();
  r=open(new Context(),false,PassthroughCodec.AC3,0,f,6,blocking);blocking.block=true;
  final AndroidAudioRenderer active=r;Thread decoder=new Thread(()->active.playDecodedAudio(pcm(1536)));decoder.start();
  check(blocking.entered.await(2,TimeUnit.SECONDS),"decoder enters the blocking bitstream write");r.stop();decoder.join(300);
  check(!decoder.isAlive(),"stop interrupts a blocked bitstream write");blocking.pause();decoder.join(2000);
  int writes=blocking.writes.size();r.playDecodedAudio(pcm(1536));
  check(blocking.writes.size()==writes&&AudioTrack.builds==1,"a stopped passthrough renderer neither writes nor recreates tracks");r.cleanup();

  // 13. No factory or no library: plain PCM
  AudioTrack.reset();AudioTrack.directEncodings.add(IEC);
  r=open(new Context(),false,PassthroughCodec.AC3,0,null,6);
  check(track().format.encoding==PCM,"without an encoder factory the renderer is plain PCM");r.stop();r.cleanup();
  AudioTrack.reset();AudioTrack.directEncodings.add(IEC);f=new FakeFactory();f.available=false;
  r=open(new Context(),false,PassthroughCodec.AC3,0,f,6);
  check(track().format.encoding==PCM&&f.created.isEmpty(),"a build without the encoder library is plain PCM");r.stop();r.cleanup();

  System.out.println(checks+" checks, "+failures+" failures");System.exit(failures==0?0:1);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='audio-passthrough-') as temp:
    work = Path(temp)
    for name, content in files.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-encoding', 'UTF-8', '-d', str(work)]
                   + [str(work / name) for name in files], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'AudioPassthrough'], timeout=20).returncode)
