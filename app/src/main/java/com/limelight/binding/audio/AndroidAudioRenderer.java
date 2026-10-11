package com.limelight.binding.audio;

import android.content.Context;
import android.content.Intent;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.media.audiofx.AudioEffect;
import android.os.Build;
import android.os.SystemClock;

import com.limelight.LimeLog;
import com.limelight.nvstream.av.audio.AudioRenderer;
import com.limelight.nvstream.jni.MoonBridge;

public class AndroidAudioRenderer implements AudioRenderer {

    private final Context context;
    private final boolean enableAudioFx;

    private static final long TRACK_RETRY_DELAY_MS = 1000;
    private final Object trackLock = new Object();
    private AudioTrack track;
    private volatile boolean stopped;
    private boolean started;
    private boolean effectSessionOpen;
    private int playbackChannelConfig;
    private int playbackSampleRate;
    private int playbackBufferSize;
    private boolean playbackLowLatency;
    private long nextTrackRetryTime;

    public AndroidAudioRenderer(Context context, boolean enableAudioFx) {
        this.context = context;
        this.enableAudioFx = enableAudioFx;
    }

    private AudioTrack createAudioTrack(int channelConfig, int sampleRate, int bufferSize, boolean lowLatency) {
        AudioAttributes.Builder attributesBuilder = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_GAME);
        AudioFormat format = new AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(sampleRate)
                .setChannelMask(channelConfig)
                .build();

        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            // Use FLAG_LOW_LATENCY on L through N
            if (lowLatency) {
                attributesBuilder.setFlags(AudioAttributes.FLAG_LOW_LATENCY);
            }
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            AudioTrack.Builder trackBuilder = new AudioTrack.Builder()
                    .setAudioFormat(format)
                    .setAudioAttributes(attributesBuilder.build())
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .setBufferSizeInBytes(bufferSize);

            // Use PERFORMANCE_MODE_LOW_LATENCY on O and later
            if (lowLatency) {
                trackBuilder.setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY);
            }

            return trackBuilder.build();
        }
        else {
            return new AudioTrack(attributesBuilder.build(),
                    format,
                    bufferSize,
                    AudioTrack.MODE_STREAM,
                    AudioManager.AUDIO_SESSION_ID_GENERATE);
        }
    }

    @Override
    public int setup(MoonBridge.AudioConfiguration audioConfiguration, int sampleRate, int samplesPerFrame) {
        stopped = false;
        nextTrackRetryTime = 0;
        int channelConfig;
        int bytesPerFrame;

        switch (audioConfiguration.channelCount)
        {
            case 2:
                channelConfig = AudioFormat.CHANNEL_OUT_STEREO;
                break;
            case 4:
                channelConfig = AudioFormat.CHANNEL_OUT_QUAD;
                break;
            case 6:
                channelConfig = AudioFormat.CHANNEL_OUT_5POINT1;
                break;
            case 8:
                // AudioFormat.CHANNEL_OUT_7POINT1_SURROUND isn't available until Android 6.0,
                // yet the CHANNEL_OUT_SIDE_LEFT and CHANNEL_OUT_SIDE_RIGHT constants were added
                // in 5.0, so just hardcode the constant so we can work on Lollipop.
                channelConfig = 0x000018fc; // AudioFormat.CHANNEL_OUT_7POINT1_SURROUND
                break;
            default:
                LimeLog.severe("Decoder returned unhandled channel count");
                return -1;
        }

        LimeLog.info("Audio channel config: "+String.format("0x%X", channelConfig));

        bytesPerFrame = audioConfiguration.channelCount * samplesPerFrame * 2;

        // We're not supposed to request less than the minimum
        // buffer size for our buffer, but it appears that we can
        // do this on many devices and it lowers audio latency.
        // We'll try the small buffer size first and if it fails,
        // use the recommended larger buffer size.

        for (int i = 0; i < 4; i++) {
            boolean lowLatency;
            int bufferSize;

            // We will try:
            // 1) Small buffer, low latency mode
            // 2) Large buffer, low latency mode
            // 3) Small buffer, standard mode
            // 4) Large buffer, standard mode

            switch (i) {
                case 0:
                case 1:
                    lowLatency = true;
                    break;
                case 2:
                case 3:
                    lowLatency = false;
                    break;
                default:
                    // Unreachable
                    throw new IllegalStateException();
            }

            switch (i) {
                case 0:
                case 2:
                    bufferSize = bytesPerFrame * 2;
                    break;

                case 1:
                case 3:
                    // Try the larger buffer size
                    bufferSize = Math.max(AudioTrack.getMinBufferSize(sampleRate,
                            channelConfig,
                            AudioFormat.ENCODING_PCM_16BIT),
                            bytesPerFrame * 2);

                    // Round to next frame
                    bufferSize = (((bufferSize + (bytesPerFrame - 1)) / bytesPerFrame) * bytesPerFrame);
                    break;
                default:
                    // Unreachable
                    throw new IllegalStateException();
            }

            // Skip low latency options if hardware sample rate doesn't match the content
            if (AudioTrack.getNativeOutputSampleRate(AudioManager.STREAM_MUSIC) != sampleRate && lowLatency) {
                continue;
            }

            // Skip low latency options when using audio effects, since low latency mode
            // precludes the use of the audio effect pipeline (as of Android 13).
            if (enableAudioFx && lowLatency) {
                continue;
            }

            try {
                track = createAudioTrack(channelConfig, sampleRate, bufferSize, lowLatency);
                track.play();

                playbackChannelConfig = channelConfig;
                playbackSampleRate = sampleRate;
                playbackBufferSize = bufferSize;
                playbackLowLatency = lowLatency;

                // Successfully created working AudioTrack. We're done here.
                LimeLog.info("Audio track configuration: "+bufferSize+" "+lowLatency);
                break;
            } catch (Exception e) {
                // Try to release the AudioTrack if we got far enough
                e.printStackTrace();
                try {
                    if (track != null) {
                        AudioTrack failedTrack = track;
                        track = null;
                        failedTrack.release();
                    }
                } catch (Exception ignored) {}
            }
        }

        if (track == null) {
            // Couldn't create any audio track for playback
            return -2;
        }

        return 0;
    }

    // Call with trackLock held. Session broadcasts must follow track replacement.
    private void setEffectSessionOpen(boolean open) {
        if (!enableAudioFx || effectSessionOpen == open || track == null) {
            return;
        }
        Intent i = new Intent(open ? AudioEffect.ACTION_OPEN_AUDIO_EFFECT_CONTROL_SESSION :
                AudioEffect.ACTION_CLOSE_AUDIO_EFFECT_CONTROL_SESSION);
        i.putExtra(AudioEffect.EXTRA_AUDIO_SESSION, track.getAudioSessionId());
        i.putExtra(AudioEffect.EXTRA_PACKAGE_NAME, context.getPackageName());
        if (open) {
            i.putExtra(AudioEffect.EXTRA_CONTENT_TYPE, AudioEffect.CONTENT_TYPE_GAME);
        }
        context.sendBroadcast(i);
        effectSessionOpen = open;
    }

    private void releaseTrack() {
        setEffectSessionOpen(false);
        AudioTrack oldTrack = track;
        track = null;
        if (oldTrack != null) {
            try {
                oldTrack.release();
            } catch (Exception e) {
                LimeLog.warning("Unable to release audio track: " + e);
            }
        }
    }

    private AudioTrack getPlaybackTrack() {
        synchronized (trackLock) {
            if (stopped) {
                return null;
            }
            if (track == null && SystemClock.uptimeMillis() >= nextTrackRetryTime) {
                // Rate limit both failed creation and replacements that immediately die.
                nextTrackRetryTime = SystemClock.uptimeMillis() + TRACK_RETRY_DELAY_MS;
                try {
                    track = createAudioTrack(playbackChannelConfig, playbackSampleRate,
                            playbackBufferSize, playbackLowLatency);
                    track.play();
                    setEffectSessionOpen(started);
                } catch (Exception e) {
                    LimeLog.warning("Unable to recreate audio track: " + e);
                    releaseTrack();
                }
            }
            return track;
        }
    }

    @Override
    public void playDecodedAudio(short[] audioData) {
        // Only queue up to 40 ms of pending audio data in addition to AudioTrack's buffer.
        if (MoonBridge.getPendingAudioDuration() >= 40) {
            LimeLog.info("Too much pending audio data: " + MoonBridge.getPendingAudioDuration() + " ms");
            return;
        }

        int offset = 0;
        while (offset < audioData.length && !stopped) {
            AudioTrack currentTrack = getPlaybackTrack();
            if (currentTrack == null || stopped) {
                return;
            }
            // Do not hold trackLock across this blocking call: stop() must be able
            // to pause/flush it before common-C joins the audio decoder thread.
            int written = currentTrack.write(audioData, offset, audioData.length - offset);
            if (written > 0) {
                offset += written;
            } else if (written == AudioTrack.ERROR_DEAD_OBJECT) {
                synchronized (trackLock) {
                    if (track == currentTrack) {
                        releaseTrack();
                    }
                }
                // Preserve the unaccepted suffix. A second dead track in the same
                // interval is retired but cannot cause an unbounded recreation loop.
            } else {
                // Zero progress, shutdown and other errors must not spin the decoder.
                return;
            }
        }
    }

    @Override
    public void start() {
        synchronized (trackLock) {
            if (!stopped) {
                started = true;
                setEffectSessionOpen(true);
            }
        }
    }

    @Override
    public void stop() {
        synchronized (trackLock) {
            stopped = true;
            started = false;
            setEffectSessionOpen(false);
            if (track != null) {
                // Interrupt an outstanding blocking write before native decoder join.
                // Flush also leaves room for a write already racing with this stop.
                try {
                    track.pause();
                } catch (Exception e) {
                    LimeLog.warning("Unable to pause audio track: " + e);
                }
                try {
                    track.flush();
                } catch (Exception e) {
                    LimeLog.warning("Unable to flush audio track: " + e);
                }
            }
        }
    }

    @Override
    public void cleanup() {
        stop();
        synchronized (trackLock) {
            releaseTrack();
        }
    }
}
