#!/usr/bin/env python3
"""Compile the production PyroWaveDecoderRenderer against a fake native layer and check that
setHdrMode() (control-stream thread) returns while cleanup() is queued behind a submitFrame()
that is stalled inside the native present, and that the pending mode reaches the renderer.

The non-fair ReentrantReadWriteLock blocks a new reader whenever a writer is queued, so the old
readLock().lock() in setHdrMode waited for the whole present (up to the 2 s fence timeout) and
held the rumble/haptic callbacks behind it.
"""
import pathlib, re, subprocess, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
PW = 'app/src/main/java/com/limelight/binding/video/PyroWaveDecoderRenderer.java'

source = (subprocess.check_output(['git', 'show', 'HEAD:' + PW], cwd=ROOT, text=True, encoding='utf-8')
          if '--baseline' in sys.argv else (ROOT / PW).read_text(encoding='utf-8'))
source = re.sub(r'^package [^\n]*\n', '', source, flags=re.M)
source = re.sub(r'^import (android|com\.limelight)\.[^\n]*\n', '', source, flags=re.M)
source = source.replace('System.loadLibrary("pyrowave-renderer");', 'Fake.loaded();')


def delegate(match):
    rtype, name, params = match.group(1), match.group(2), match.group(3).strip()
    names = ', '.join(p.strip().split()[-1] for p in params.split(',')) if params else ''
    body = ('Fake.%s(%s);' if rtype == 'void' else 'return Fake.%s(%s);') % (name, names)
    return 'private static %s %s(%s) { %s }' % (rtype, name, params, body)


source, replaced = re.subn(r'private static native (\S+) (\w+)\(([^)]*)\);', delegate, source)
assert replaced >= 7, replaced

support = r'''
import java.util.concurrent.CountDownLatch;
class Surface { boolean valid = true; public boolean isValid() { return valid; } }
class Build { static class VERSION { static final int SDK_INT = 35; } static class VERSION_CODES { static final int Q = 29; } }
class LimeLog { static void info(String s) {} static void warning(String s) {} static void severe(String s) {} }
class MoonBridge { static final int DR_OK = 0, DR_NEED_IDR = 1; }
class Fake {
    static final CountDownLatch inSubmit = new CountDownLatch(1), release = new CountDownLatch(1);
    static volatile boolean delayOldHdr;
    static final CountDownLatch oldHdrEntered = new CountDownLatch(1), releaseOldHdr = new CountDownLatch(1);
    static volatile int hdrCalls; static volatile boolean lastEnabled; static volatile float lastPeak;
    static void loaded() {}
    static boolean nativeIsAvailable() { return true; }
    static long nativeCreate(Surface s, int w, int h, int f, boolean c, boolean t) { return 1; }
    static void nativeSetHdrMode(long h, boolean e, float p) {
        if (delayOldHdr && e && p == 1000f) {
            oldHdrEntered.countDown();
            try { releaseOldHdr.await(); } catch (InterruptedException x) { throw new RuntimeException(x); }
        }
        hdrCalls++; lastEnabled = e; lastPeak = p;
    }
    static int nativeSubmitFrame(long h, byte[] d, int l) { inSubmit.countDown(); try { release.await(); } catch (InterruptedException x) {} return 0; }
    static int nativeSubmitPacketFrame(long h, byte[] d, int l, int[] p, int n, int c) { return nativeSubmitFrame(h, d, l); }
    static int nativeGetLastGpuDecodeUs(long h) { return 0; }
    static void nativeDestroy(long h) {}
}
public class PyroHdrLockRegression {
    static int checks, failures;
    static void check(boolean ok, String name) { checks++; System.out.println((ok ? "PASS " : "FAIL ") + name); if (!ok) failures++; }
    public static void main(String[] args) throws Exception {
        PyroWaveDecoderRenderer r = new PyroWaveDecoderRenderer();
        check(r.setup(new Surface(), 1920, 1080, 60, false, true), "the fake renderer sets up");
        int before = Fake.hdrCalls; r.setHdrMode(false, 0f);
        check(Fake.hdrCalls == before + 1, "an uncontended setHdrMode reaches native immediately");
        java.lang.reflect.Field f = PyroWaveDecoderRenderer.class.getDeclaredField("handleLock"); f.setAccessible(true);
        java.util.concurrent.locks.ReentrantReadWriteLock lock = (java.util.concurrent.locks.ReentrantReadWriteLock) f.get(r);
        Thread submitter = new Thread(() -> r.submitFrame(new byte[16], 16)); submitter.start();
        check(Fake.inSubmit.await(5, java.util.concurrent.TimeUnit.SECONDS), "the submitter is inside the stalled present");
        Thread cleaner = new Thread(r::cleanup); cleaner.start();
        long deadline = System.nanoTime() + 5_000_000_000L;
        while (!lock.hasQueuedThreads() && System.nanoTime() < deadline) Thread.sleep(5);
        check(lock.hasQueuedThreads(), "cleanup is queued behind the stalled submit");
        Thread control = new Thread(() -> r.setHdrMode(true, 1000f)); control.start();
        control.join(500);
        check(!control.isAlive(), "setHdrMode returns while cleanup waits on a stalled present");
        Fake.release.countDown(); submitter.join(5000); cleaner.join(5000); control.join(5000);
        check(!submitter.isAlive() && !cleaner.isAlive() && !control.isAlive(), "all threads finish once the present completes");
        // tryLock may barge past the queued writer (mode applied at once) or fail (mode pending);
        // either way a new native renderer, which starts in SDR, must receive the current mode.
        Fake.lastEnabled = false; Fake.lastPeak = 0f; before = Fake.hdrCalls;
        check(r.setup(new Surface(), 1920, 1080, 60, false, true), "the renderer sets up again");
        check(Fake.hdrCalls > before && Fake.lastEnabled && Fake.lastPeak == 1000f, "the current HDR mode reaches the next native renderer");
        // A submitting/control reader may be descheduled during the native HDR write.
        // A newer control message must remain pending if that old write finishes last.
        Fake.delayOldHdr = true;
        Thread oldHdr = new Thread(() -> r.setHdrMode(true, 1000f)); oldHdr.start();
        check(Fake.oldHdrEntered.await(5, java.util.concurrent.TimeUnit.SECONDS), "the old HDR application is in flight");
        Thread newHdr = new Thread(() -> r.setHdrMode(false, 800f)); newHdr.start();
        newHdr.join(500);
        check(!newHdr.isAlive(), "a newer HDR update does not wait on the older native call");
        Fake.releaseOldHdr.countDown(); oldHdr.join(5000); newHdr.join(5000);
        check(!oldHdr.isAlive() && !newHdr.isAlive(), "both HDR callers finish");
        r.submitFrame(new byte[16], 16);
        check(!Fake.lastEnabled && Fake.lastPeak == 800f, "the next frame applies the latest complete HDR mode instead of losing it");
        r.cleanup(); r.setup(new Surface(), 1920, 1080, 60, false, true);
        check(!Fake.lastEnabled && Fake.lastPeak == 800f, "the latest HDR mode also survives renderer replacement");
        System.out.println(checks + " checks, " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
with tempfile.TemporaryDirectory(prefix='pyrowave-hdr-') as directory:
    work = pathlib.Path(directory)
    (work / 'PyroWaveDecoderRenderer.java').write_text(source, encoding='utf-8')
    (work / 'PyroHdrLockRegression.java').write_text(support, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work),
                    str(work / 'PyroWaveDecoderRenderer.java'), str(work / 'PyroHdrLockRegression.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'PyroHdrLockRegression']).returncode)
