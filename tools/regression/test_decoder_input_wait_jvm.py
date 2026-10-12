#!/usr/bin/env python3
"""Execute the production fetchNextInputBuffer() on the JVM with scripted MediaCodec and clock
fakes. One call waits at most INPUT_BUFFER_WAIT_MS; the hung exception fires only once the
stall measured from the first failed wait reaches DECODER_HUNG_MS, after exactly one
notifyCrash; a success in between restarts the window; stopping never throws. --baseline reads
HEAD. Needs a JDK (java with com.sun.tools.javac.Main), as the other JVM harnesses.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/binding/video/MediaCodecDecoderRenderer.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True, encoding='utf-8')
          if '--baseline' in sys.argv else (ROOT / PATH).read_text(encoding='utf-8'))


def block(marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


def line(prefix):
    at = source.index(prefix)
    return source[at:source.index('\n', at)]


code = r"""
import java.nio.ByteBuffer;
public class DecoderInputWait {
    static final class SystemClock { static long now = 1000; static long uptimeMillis() { return now; } }
    static final class Build {
        static final class VERSION { static final int SDK_INT = 30; }
        static final class VERSION_CODES { static final int LOLLIPOP = 21; }
    }
    static final class LimeLog { static void warning(String s) {} }
    interface CrashListener { void notifyCrash(Exception e); }
    static final class DecoderHungException extends RuntimeException { DecoderHungException(int ms) { super("hung " + ms); } }
    static final class RendererException extends RuntimeException { RendererException(Object r, Exception e) { super(e); } }
    static final class MediaCodec {
        int available = -1, dequeues;
        int dequeueInputBuffer(long timeoutUs) { ++dequeues; SystemClock.now += 10; return available; }
        ByteBuffer getInputBuffer(int index) { return ByteBuffer.allocate(16); }
    }
    static final int CR_FLAG_INPUT_THREAD = 0x1;
    @@CONSTANTS@@
    private MediaCodec videoDecoder = new MediaCodec();
    private ByteBuffer[] legacyInputBuffers;
    private int nextInputBufferIndex = -1;
    private ByteBuffer nextInputBuffer;
    private volatile boolean stopping;
    private boolean reportedCrash;
    private RendererException initialException;
    int crashes;
    private CrashListener crashListener = e -> ++crashes;
    private void handleDecoderException(IllegalStateException e) {}
    private boolean doCodecRecoveryIfRequired(int flag) { return false; }
    @@FETCH@@
    static int checks, failures;
    static void check(boolean ok, String name) { ++checks; if (!ok) ++failures; System.out.println((ok ? "PASS " : "FAIL ") + name); }
    public static void main(String[] args) {
        DecoderInputWait r = new DecoderInputWait();
        long start = SystemClock.now;
        boolean got = r.fetchNextInputBuffer();
        check(!got && r.videoDecoder.dequeues >= 1 && SystemClock.now - start <= INPUT_BUFFER_WAIT_MS + 10,
              "a failed wait returns false within INPUT_BUFFER_WAIT_MS");
        check(r.inputStallStartMs == start, "the stall is measured from the first failed wait");
        boolean threw = false;
        while (SystemClock.now - start < DECODER_HUNG_MS - INPUT_BUFFER_WAIT_MS - 10) {
            try { r.fetchNextInputBuffer(); } catch (RendererException e) { threw = true; break; }
        }
        check(!threw && r.crashes == 0, "calls inside the hang threshold neither throw nor report a crash");
        try { while (true) { r.fetchNextInputBuffer(); } } catch (RendererException e) { threw = true; }
        check(threw && r.crashes == 1 && SystemClock.now - start >= DECODER_HUNG_MS,
              "a call at the hang threshold throws after exactly one notifyCrash");
        // A success between failures restarts the window.
        DecoderInputWait s = new DecoderInputWait();
        long t0 = SystemClock.now;
        s.fetchNextInputBuffer();
        s.videoDecoder.available = 3;
        check(s.fetchNextInputBuffer() && s.inputStallStartMs == 0, "an obtained buffer resets the stall origin");
        s.nextInputBuffer = null; s.nextInputBufferIndex = -1; s.videoDecoder.available = -1;
        long t1 = SystemClock.now;
        s.fetchNextInputBuffer();
        check(s.inputStallStartMs == t1 && t1 > t0, "a new stall starts its own window");
        // stopping never throws.
        DecoderInputWait q = new DecoderInputWait();
        q.stopping = true;
        boolean quiet = true;
        for (int i = 0; i < 80; i++) { try { q.fetchNextInputBuffer(); } catch (RendererException e) { quiet = false; } }
        check(quiet && q.crashes == 0, "a stopping decoder never reports a hang");
        System.out.println(checks + " checks; " + failures + " failures");
        System.exit(failures == 0 ? 0 : 1);
    }
}
"""
constants = '\n'.join('    ' + line(p).strip() for p in ('    private static final int INPUT_BUFFER_WAIT_MS', '    private static final int DECODER_HUNG_MS', '    private long inputStallStartMs'))
code = code.replace('@@CONSTANTS@@', constants).replace('@@FETCH@@', block('    private boolean fetchNextInputBuffer() {'))
with tempfile.TemporaryDirectory(prefix='decoder-input-wait-') as temp:
    work = Path(temp)
    java = work / 'DecoderInputWait.java'
    java.write_text(code, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(java)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'DecoderInputWait'], timeout=60).returncode)
