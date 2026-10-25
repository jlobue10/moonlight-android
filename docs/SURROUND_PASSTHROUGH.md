# Surround passthrough (Dolby Digital, DTS, Dolby TrueHD)

Moonlight receives multichannel audio from the host as 5.1 or 7.1 Opus and normally plays
it as multichannel PCM. Many TVs, soundbars and AV receivers only accept multichannel audio
over HDMI or S/PDIF as a compressed bitstream, and play PCM as stereo. With this feature the
app encodes the decoded 5.1 PCM on the device into one of those bitstreams and writes it to
a passthrough `AudioTrack`, so the receiver decodes the surround mix itself.

Based on [ClassicOldSong/moonlight-android#605](https://github.com/ClassicOldSong/moonlight-android/pull/605)
by kefrens, adapted to this fork's audio renderer (which recovers from a dead output) and
built by CI for every release.

## Settings

Both settings live under **Audio Settings** and are hidden when the build does not include
the encoders.

- **Surround passthrough format**: Off (PCM, the default), Dolby Digital, DTS or Dolby TrueHD
  (experimental). The format only applies to 5.1 streams; stereo stays PCM. Selecting a format
  with 7.1 configured asks the host for 5.1 instead, because the encoders stop at 5.1.
  If the device refuses the chosen format, the next cheaper one is tried (TrueHD, DTS, then
  Dolby Digital), and if none works the stream falls back to 5.1 PCM.
- **Passthrough buffer**: how many encoded frames the device buffers before the receiver.
  Auto uses the platform minimum (at least two frames). Fewer frames mean less latency (a
  Dolby Digital frame is 32 ms, a DTS frame 10.7 ms) but choppy audio if the receiver needs
  more.

## How the output is chosen

For each codec in the fallback chain that the build can encode:

1. **IEC 61937 bursts** on an `ENCODING_IEC61937` stereo track (Android 7+). Each frame is
   wrapped in a burst that lasts exactly one frame, so the framework paces the stream like
   PCM. Some HALs drain a plain compressed track faster than real time and underrun.
2. The codec's own encoding (`ENCODING_AC3`, `ENCODING_DTS`, `ENCODING_DOLBY_TRUEHD`),
   declared as 5.1 and then as stereo, since some HALs only accept compressed formats
   declared as stereo.

The format is checked with `AudioTrack.isDirectPlaybackSupported` (Android 10+) before a track
is created. If the output dies mid-stream (HDMI or eARC renegotiation, soundbar reconnect) an
identical track is rebuilt with the same rate limit as the PCM path, and the encoder is kept.
Audio effects are not offered a session while a bitstream is playing.

## Build

The encoders are FFmpeg's `ac3`, `dca` and `truehd` encoders in a minimal static
`libavcodec`/`libavutil` (LGPL, no GPL or nonfree components), linked into
`libmoonlight-ptenc.so` from `app/src/main/jni/ptenc`. The FFmpeg libraries are not checked
in; `app/src/main/jni/ptenc/build-ffmpeg.sh` builds them for every ABI from the pinned FFmpeg
commit (`n7.1.5`) with the project's NDK. CI runs the script (cached on the script and
`app/build.gradle`) before Gradle and fails the build if a library is missing for any ABI, so
release APKs always include passthrough.

Locally, without the libraries the module is skipped, the app builds and runs, and the two
settings are hidden. To enable passthrough in a local build run the script once (Linux or
macOS, or WSL on Windows, with `ANDROID_NDK_HOME` set or the NDK installed under
`$ANDROID_HOME/ndk/<version from app/build.gradle>`):

```
bash app/src/main/jni/ptenc/build-ffmpeg.sh
```

## Tests

- `tools/regression/test_audio_passthrough.py`: the production renderer and output selection
  with a fake encoder and scripted AudioTracks (format preference, fallback chain, buffer
  sizing, frame-sized writes, partial-write retry, dead-output recreation, PCM fallback,
  effect sessions, cleanup).
- `tools/regression/test_passthrough_iec61937.py`: the IEC 61937 burst packer compiled on the
  host.
- `PassthroughCodecTest` (unit tests): preference mapping, fallback chains, framing constants.
- `tools/regression/test_audio_track_recovery.py` still covers the PCM path, which is unchanged.

Not verified here: real receivers. Latency logging from the original PR was left out; the
regression output shows the frame-sized writes instead.
