#!/usr/bin/env python3
"""Exercise production NvConnection start/stop against a fake process-wide JNI bridge.

HTTP and native operations are deterministic boundaries; the real semaphore,
ownership fields, and complete start/stop methods are compiled unchanged.
--baseline reads HEAD. No Android SDK, host, or controller is required.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/nvstream/NvConnection.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())


def block(marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


code = r'''
import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.concurrent.*;
import static java.lang.Thread.sleep;
public class ConnectionOwnership {
    static class AudioRenderer {}
    static class VideoDecoderRenderer { int getCapabilities() { return 0; } }
    static class NvConnectionListener {
        void stageStarting(String s) {} void stageComplete(String s) {}
        boolean stageFailed(String s, int ports, int error) { return false; }
        void displayMessage(String s) {}
    }
    static class XmlPullParserException extends Exception {}
    static class HostHttpResponseException extends IOException { int getErrorCode() { return 0; } }
    static class Config {
        Config getApp() { return this; } String getAppName() { return "test"; }
        int getRefreshRate() { return 60; } int getBitrate() { return 10000; }
        Config getAudioConfiguration() { return this; } int toInt() { return 0; }
        int getSupportedVideoFormats() { return 1; } int getClientRefreshRateX100() { return 6000; }
        int getColorSpace() { return 0; } int getColorRange() { return 0; }
    }
    static class Key { byte[] getEncoded() { return new byte[16]; } }
    static class Address { String address = "test"; }
    static class Context {
        NvConnectionListener connListener; Config streamConfig = new Config();
        Address serverAddress = new Address(); Key riKey = new Key();
        String serverAppVersion, serverGfeVersion, rtspSessionUrl;
        int videoCapabilities, riKeyId, serverCodecModeSupport, negotiatedWidth,
            negotiatedHeight, negotiatedPacketSize, negotiatedRemoteStreaming;
    }
    static class MoonBridge {
        static final int ML_PORT_FLAG_TCP_47984=1, ML_PORT_FLAG_TCP_47989=2;
        static volatile NvConnectionListener listener;
        static volatile int starts, stops, interrupts, cleanups, result;
        static volatile boolean blocked;
        static CountDownLatch nativeEntered, nativeInterrupt, allowNativeReturn;
        static void reset() {
            listener=null; starts=stops=interrupts=cleanups=result=0; blocked=false;
            nativeEntered=new CountDownLatch(1); nativeInterrupt=new CountDownLatch(1);
            allowNativeReturn=new CountDownLatch(1);
        }
        static void setupBridge(VideoDecoderRenderer v, AudioRenderer a, NvConnectionListener l) { listener=l; }
        static int startConnection(Object... args) {
            ++starts; nativeEntered.countDown();
            if (blocked) await(allowNativeReturn);
            return result;
        }
        static void interruptConnection() { ++interrupts; nativeInterrupt.countDown(); }
        static void stopConnection() { ++stops; }
        static void cleanupBridge() { ++cleanups; listener=null; }
    }
    static class NvConnection {
        @@FIELDS@@
        final Context context = new Context();
        final CountDownLatch httpEntered = new CountDownLatch(1), httpResume = new CountDownLatch(1);
        volatile Thread startThread;
        boolean holdHttp;
        int getNegotiatedBitrate() { return 10000; }
        boolean startApp() throws IOException, XmlPullParserException {
            startThread=Thread.currentThread(); httpEntered.countDown();
            if (holdHttp) await(httpResume);
            return true;
        }
        @@STOP@@
        @@START@@
    }
    static int checks, failures;
    static void check(boolean ok, String name) {
        ++checks; if (!ok) ++failures;
        System.out.println((ok ? "PASS " : "FAIL ") + name);
    }
    static void await(CountDownLatch latch) {
        try { if (!latch.await(3, TimeUnit.SECONDS)) throw new AssertionError("timed out"); }
        catch (InterruptedException e) { throw new AssertionError(e); }
    }
    static void join(Thread thread) throws Exception {
        thread.join(3000); if (thread.isAlive()) throw new AssertionError("thread stuck");
    }
    static void start(NvConnection c) {
        c.start(new AudioRenderer(), new VideoDecoderRenderer(), new NvConnectionListener());
        await(c.httpEntered);
    }
    static void reset() {
        check(NvConnection.connectionAllowed.availablePermits()==1, "exactly one permit between scenarios");
        MoonBridge.reset();
    }
    public static void main(String[] args) throws Exception {
        reset();
        NvConnection active=new NvConnection(); start(active); join(active.startThread);
        NvConnection cancelled=new NvConnection(); cancelled.holdHttp=true; start(cancelled);
        cancelled.stop();
        check(MoonBridge.interrupts==0 && MoonBridge.stops==0 && MoonBridge.listener==active.context.connListener,
              "cancelling HTTP setup cannot interrupt another connection's native session");
        check(NvConnection.connectionAllowed.availablePermits()==0, "a non-owner stop cannot release the active permit");
        cancelled.httpResume.countDown(); active.stop(); join(cancelled.startThread);
        check(MoonBridge.starts==1 && MoonBridge.stops==1, "cancelled waiter never starts JNI after acquiring the permit");
        check(MoonBridge.listener==null, "the active owner cleans up its bridge");

        reset();
        NvConnection old=new NvConnection(); start(old); join(old.startThread); old.stop();
        NvConnection next=new NvConnection(); start(next); join(next.startThread);
        int interrupts=MoonBridge.interrupts, stops=MoonBridge.stops;
        old.stop();
        check(MoonBridge.interrupts==interrupts && MoonBridge.stops==stops && MoonBridge.listener==next.context.connListener,
              "a repeated stale stop cannot interrupt or clean up the replacement session");
        next.stop();

        reset(); MoonBridge.result=-1;
        NvConnection failed=new NvConnection(); start(failed); join(failed.startThread);
        check(MoonBridge.listener==null && MoonBridge.cleanups==1,
              "native start failure releases the retained Activity/renderers immediately");
        check(NvConnection.connectionAllowed.availablePermits()==1, "native start failure returns its permit");
        MoonBridge.result=0;
        NvConnection recovered=new NvConnection(); start(recovered); join(recovered.startThread);
        interrupts=MoonBridge.interrupts; failed.stop();
        check(MoonBridge.interrupts==interrupts && MoonBridge.listener==recovered.context.connListener,
              "late teardown after failure leaves a recovered session intact");
        recovered.stop();

        reset(); MoonBridge.blocked=true;
        NvConnection pending=new NvConnection(); start(pending); await(MoonBridge.nativeEntered);
        CountDownLatch unusedStopped=new CountDownLatch(1);
        Thread unusedStop=new Thread(()->{new NvConnection().stop(); unusedStopped.countDown();}); unusedStop.start();
        check(unusedStopped.await(200,TimeUnit.MILLISECONDS) && MoonBridge.interrupts==0,
              "a non-owner stop neither waits for nor interrupts another pending native start");
        Thread stop=new Thread(pending::stop); stop.start(); await(MoonBridge.nativeInterrupt);
        check(MoonBridge.stops==0, "the owner interrupts JNI without waiting for the start/stop monitor");
        MoonBridge.allowNativeReturn.countDown(); join(pending.startThread); join(stop); join(unusedStop);
        check(MoonBridge.stops==1 && MoonBridge.cleanups==1,
              "native start and teardown remain serialized after interruption");

        reset(); NvConnection preCancelled=new NvConnection(); preCancelled.stop(); start(preCancelled); join(preCancelled.startThread);
        check(MoonBridge.starts==0 && MoonBridge.interrupts==0 && MoonBridge.stops==0,
              "stop before start does not call the process-wide native bridge");
        check(NvConnection.connectionAllowed.availablePermits()==1, "pre-cancelled start preserves permit balance");
        System.out.println(checks+" checks; "+failures+" failures");
        System.exit(failures==0 ? 0 : 1);
    }
}
'''
fields = source[source.index('    private static Semaphore connectionAllowed'):source.index('    private final boolean isMonkey')]
code = code.replace('@@FIELDS@@', fields).replace('@@STOP@@', block('    public void stop()'))
code = code.replace('@@START@@', block('    public void start(final AudioRenderer'))
with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    (work / 'ConnectionOwnership.java').write_text(code)
    subprocess.run(['java', 'com.sun.tools.javac.Main', str(work / 'ConnectionOwnership.java')], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', temp, 'ConnectionOwnership'], timeout=30).returncode)
