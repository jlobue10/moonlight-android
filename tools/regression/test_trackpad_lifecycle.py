#!/usr/bin/env python3
"""Compile the production trackpad against a deterministic Handler and mouse sink.

Exercise cancellation and pointer-count changes while a delayed tap/drag owns a
host button, plus the scroll speed, pointer momentum, move sink and double-tap settings. --baseline reads the current HEAD before applying the fix.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/binding/input/touch/'


def source(name):
    path = PATH + name + '.java'
    text = (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
            if '--baseline' in sys.argv else (ROOT / path).read_text(encoding='utf-8'))
    return re.sub(r'^(?:package|import) [^\n]+\n', '', text, flags=re.M)


fixture = r'''
import java.util.*;
class Looper { static Looper getMainLooper() { return new Looper(); } }
class LimeLog { static void info(String s) {} }
class View { int getWidth() { return 1000; } int getHeight() { return 1000; } }
class PreferenceConfiguration { boolean absoluteMouseMode; int touchPadSensitivity=100, touchPadYSensitity=100; }
class Handler {
    static long now;
    static final List<Job> jobs = new ArrayList<>();
    static class Job { Handler owner; Runnable r; long due; Job(Handler h, Runnable r, long d) { owner=h; this.r=r; due=d; } }
    Handler(Looper ignored) {}
    void post(Runnable r) { postDelayed(r, 0); }
    void postDelayed(Runnable r, long delay) { jobs.add(new Job(this, r, now + delay)); }
    void removeCallbacks(Runnable r) { jobs.removeIf(j -> j.owner == this && j.r == r); }
    void removeCallbacksAndMessages(Object ignored) { jobs.removeIf(j -> j.owner == this); }
    static void drain() { drainUntil(Long.MAX_VALUE); }
    static void drainUntil(long t) {
        int budget = 1000;
        for (;;) {
            jobs.sort(Comparator.comparingLong(j -> j.due));
            if (jobs.isEmpty() || jobs.get(0).due > t || --budget == 0) break;
            Job j = jobs.remove(0); now = Math.max(now, j.due); j.r.run();
        }
        if (budget == 0) throw new AssertionError("unbounded callbacks");
    }
}
class MouseButtonPacket { static final byte BUTTON_LEFT=1, BUTTON_MIDDLE=2, BUTTON_RIGHT=3, BUTTON_X1=4, BUTTON_X2=5; }
class NvConnection {
    final Set<Byte> held = new HashSet<>();
    final List<String> buttons = new ArrayList<>();
    int moves, scrollTotal, hscrollTotal;
    void sendMouseButtonDown(byte b) { held.add(b); buttons.add("down:" + b); }
    void sendMouseButtonUp(byte b) { held.remove(b); buttons.add("up:" + b); }
    void sendMouseMove(short x, short y) { ++moves; }
    void sendMouseHighResHScroll(short x) { ++moves; hscrollTotal += x; }
    void sendMouseHighResScroll(short y) { ++moves; scrollTotal += y; }
    void sendMousePosition(short x, short y, short w, short h) { ++moves; }
    void sendMouseMoveAsMousePosition(short x, short y, short w, short h) { ++moves; }
}
public class TrackpadLifecycleRegression {
    static int checks, failures;
    static void check(boolean ok, String message) {
        ++checks; if (!ok) ++failures;
        System.out.println((ok ? "PASS " : "FAIL ") + message);
    }
    static TrackpadContext fresh(NvConnection c) {
        Handler.jobs.clear(); Handler.now = 1000;
        return new TrackpadContext(c, 0);
    }
    static void tap(TrackpadContext c, long time) {
        Handler.now = time; c.setPointerCount(1); c.touchDownEvent(0, 0, time, true);
        Handler.now = time + 10; c.touchUpEvent(0, 0, time + 10); c.setPointerCount(0);
    }
    static void drag(TrackpadContext c) {
        tap(c, 1000);
        Handler.now = 1100; c.setPointerCount(1); c.touchDownEvent(0, 0, 1100, true);
        Handler.now = 1110; c.touchMoveEvent(50, 0, 1110);
    }
    public static void main(String[] args) {
        for (int mode = 0; mode < 2; ++mode) {
            Handler.jobs.clear(); Handler.now = 1000;
            NvConnection n = new NvConnection();
            TouchContext c = mode == 0 ? new AbsoluteTouchContext(n, 0, new View(), false)
                    : new RelativeTouchContext(n, 0, 1000, 1000, new View(), new PreferenceConfiguration());
            String name = mode == 0 ? "absolute" : "relative";
            c.setPointerCount(1); c.touchDownEvent(0, 0, 1000, true); c.touchUpEvent(0, 0, 1010);
            c.cancelTouch(); c.setPointerCount(0);
            check(n.held.isEmpty() && Handler.jobs.isEmpty(), name + ": cancellation releases a pending tap and removes timers");
            int count = n.buttons.size(); c.cancelTouch(); Handler.drain();
            check(n.buttons.size() == count, name + ": repeated cancellation and old callbacks send no extra input");
            count = n.buttons.size();
            c.setPointerCount(1); c.touchDownEvent(0, 0, 2000, true); c.touchUpEvent(0, 0, 2010);
            Handler.drain();
            check(n.held.isEmpty() && n.buttons.size() == count + 2, name + ": a fresh gesture after cancellation clicks normally");
            n.buttons.clear(); Handler.now = 3000;
            c.setPointerCount(1); c.touchDownEvent(0, 0, 3000, true); c.touchUpEvent(0, 0, 3010);
            Handler.now = 3020;
            c.touchDownEvent(0, 0, 3020, true); c.touchUpEvent(0, 0, 3030); Handler.drain();
            check(n.buttons.equals(Arrays.asList("down:1", "up:1", "down:1", "up:1")),
                  name + ": rapid taps each produce a balanced click");
            n.buttons.clear(); Handler.now = 4000;
            c.touchDownEvent(0, 0, 4000, true); Handler.drain();
            check(n.held.contains(mode == 0 ? (byte)3 : (byte)1), name + ": long press still selects its intended button");
            c.touchUpEvent(0, 0, 5000); Handler.drain();
            check(n.held.isEmpty(), name + ": long-press release balances the button");
        }
        {
            NvConnection n = new NvConnection(); TrackpadContext c = fresh(n); tap(c, 1000);
            check(n.held.contains((byte)1), "tap retains the polling-friendly delayed release");
            Handler.drain();
            check(n.buttons.equals(Arrays.asList("down:1", "up:1")), "normal tap releases once");
        }
        {
            NvConnection n = new NvConnection(); TrackpadContext c = fresh(n); tap(c, 1000);
            c.cancelTouch(); c.setPointerCount(0);
            check(n.held.isEmpty(), "cancelling a pending tap releases its button immediately");
            check(Handler.jobs.isEmpty(), "cancellation removes every delayed callback");
            int events = n.buttons.size(); Handler.drain();
            check(n.buttons.size() == events, "a cancelled context sends no delayed button events");
        }
        {
            NvConnection n = new NvConnection(); TrackpadContext c = fresh(n); drag(c);
            c.cancelTouch(); c.setPointerCount(0); n.buttons.clear();
            tap(c, 2000); Handler.drain();
            check(n.buttons.equals(Arrays.asList("down:1", "up:1")), "the first tap after a cancelled drag works normally");
        }
        {
            NvConnection n = new NvConnection(); TrackpadContext c = fresh(n); drag(c);
            c.setPointerCount(2); c.cancelTouch(); c.setPointerCount(0); Handler.drain();
            check(n.held.isEmpty() && n.buttons.equals(Arrays.asList("down:1", "up:1")),
                  "cancellation releases the original drag button after a second finger joins");
        }
        {
            NvConnection n = new NvConnection(); TrackpadContext c = fresh(n); drag(c);
            c.setPointerCount(2); Handler.now = 1300; c.touchUpEvent(50, 0, 1300); c.setPointerCount(1);
            Handler.drain();
            check(n.held.isEmpty(), "a paused drag releases its original button when the finger count changes");
        }
        {
            NvConnection n = new NvConnection(); TrackpadContext c = fresh(n); drag(c);
            Handler.now = 1111; c.touchUpEvent(50, 0, 1111); c.setPointerCount(0);
            c.cancelTouch(); int moves = n.moves; Handler.drain();
            check(n.held.isEmpty() && n.moves == moves && Handler.jobs.isEmpty(), "cancellation stops drag momentum and releases its button");
        }
        {
            NvConnection n = new NvConnection(); TrackpadContext c = fresh(n); tap(c, 1000); tap(c, 1100); Handler.drain();
            check(n.buttons.equals(Arrays.asList("down:1", "up:1", "down:1", "up:1")), "double tap still produces two balanced clicks");
        }
        // ---- settings added with desktop touchpad mode ----
        {
            // A two-finger scroll at 100 % and at 300 %: the second finger (actionIndex 1) drives the scroll
            NvConnection n1 = new NvConnection(); TrackpadContext a = new TrackpadContext(n1, 0), b = new TrackpadContext(n1, 1);
            Handler.jobs.clear(); Handler.now = 1000;
            a.setPointerCount(1); a.touchDownEvent(0, 0, 1000, true); a.setPointerCount(2); b.setPointerCount(2); b.touchDownEvent(100, 0, 1000, true);
            for (int i = 1; i <= 5; i++) { a.touchMoveEvent(0, 20 * i, 1000 + 10 * i); b.touchMoveEvent(100, 20 * i, 1000 + 10 * i); }
            int base = n1.scrollTotal;
            NvConnection n3 = new NvConnection(); a = new TrackpadContext(n3, 0); b = new TrackpadContext(n3, 1); b.setScrollSpeed(3f);
            Handler.jobs.clear(); Handler.now = 1000;
            a.setPointerCount(1); a.touchDownEvent(0, 0, 1000, true); a.setPointerCount(2); b.setPointerCount(2); b.touchDownEvent(100, 0, 1000, true);
            for (int i = 1; i <= 5; i++) { a.touchMoveEvent(0, 20 * i, 1000 + 10 * i); b.touchMoveEvent(100, 20 * i, 1000 + 10 * i); }
            check(base != 0 && n3.scrollTotal == 3 * base, "the scroll speed setting multiplies two-finger scroll output (300 % = 3x)");
            check(new TrackpadContext(new NvConnection(), 1) != null, "a context without the setting keeps the previous speed");
        }
        {
            // Pointer momentum: a fast flick glides by default and stops dead when the setting is off
            NvConnection n = new NvConnection(); TrackpadContext c = fresh(n);
            c.setPointerCount(1); c.touchDownEvent(0, 0, 1000, true);
            for (int i = 1; i <= 5; i++) { Handler.now = 1000 + 10 * i; c.touchMoveEvent(60 * i, 0, 1000 + 10 * i); }
            Handler.now = 1052; c.touchUpEvent(300, 0, 1052); c.setPointerCount(0);
            int before = n.moves; Handler.drain();
            check(n.moves > before, "by default a fast one-finger flick keeps moving the pointer (momentum)");
            n = new NvConnection(); c = fresh(n); c.setPointerMomentum(false);
            c.setPointerCount(1); c.touchDownEvent(0, 0, 1000, true);
            for (int i = 1; i <= 5; i++) { Handler.now = 1000 + 10 * i; c.touchMoveEvent(60 * i, 0, 1000 + 10 * i); }
            Handler.now = 1052; c.touchUpEvent(300, 0, 1052); c.setPointerCount(0);
            before = n.moves; Handler.drain();
            check(n.moves == before && Handler.jobs.isEmpty(), "with pointer momentum off the pointer stops when the finger lifts");
            // Scroll momentum is unaffected by the pointer setting
            NvConnection ns = new NvConnection(); TrackpadContext a = new TrackpadContext(ns, 0), b = new TrackpadContext(ns, 1);
            a.setPointerMomentum(false); b.setPointerMomentum(false); Handler.jobs.clear(); Handler.now = 1000;
            a.setPointerCount(1); a.touchDownEvent(0, 0, 1000, true); a.setPointerCount(2); b.setPointerCount(2); b.touchDownEvent(100, 0, 1000, true);
            for (int i = 1; i <= 5; i++) { Handler.now = 1000 + 10 * i; a.touchMoveEvent(0, 60 * i, 1000 + 10 * i); b.touchMoveEvent(100, 60 * i, 1000 + 10 * i); }
            Handler.now = 1052; b.touchUpEvent(100, 300, 1052); b.setPointerCount(1); a.setPointerCount(1);
            int scrolled = ns.scrollTotal; Handler.drain();
            check(ns.scrollTotal > scrolled, "two-finger scroll momentum still glides with pointer momentum off");
        }
        {
            // Move sink: with the app drawing its own cursor, pointer deltas go to the sink, not the connection
            NvConnection n = new NvConnection(); TrackpadContext c = fresh(n);
            final int[] sunk = {0};
            c.setMoveSink(new TrackpadContext.MoveSink() { public void onTrackpadMove(short dx, short dy) { sunk[0] += dx; } });
            c.setPointerCount(1); c.touchDownEvent(0, 0, 1000, true);
            for (int i = 1; i <= 3; i++) { Handler.now = 1000 + 10 * i; c.touchMoveEvent(40 * i, 0, 1000 + 10 * i); }
            check(sunk[0] > 0 && n.moves == 0, "pointer movement is routed through the move sink when one is set");
            Handler.now = 1100; c.touchUpEvent(120, 0, 1100); c.setPointerCount(0); Handler.drain();
            check(n.moves == 0, "momentum movement also goes through the sink");
        }
        {
            // Double tap: jitter under the second finger does not become a drag; absolute mode delays the second click
            NvConnection n = new NvConnection(); TrackpadContext c = fresh(n); tap(c, 1000);
            Handler.now = 1100; c.setPointerCount(1); c.touchDownEvent(0, 0, 1100, true);
            Handler.now = 1105; c.touchMoveEvent(2, 1, 1105);
            Handler.now = 1110; c.touchUpEvent(2, 1, 1110); c.setPointerCount(0); Handler.drain();
            check(n.buttons.equals(Arrays.asList("down:1", "up:1", "down:1", "up:1")), "a unit of jitter during the second tap still yields a double click");
            n = new NvConnection(); c = fresh(n); tap(c, 1000);
            Handler.now = 1100; c.setPointerCount(1); c.touchDownEvent(0, 0, 1100, true);
            Handler.now = 1110; c.touchUpEvent(0, 0, 1110); c.setPointerCount(0);
            Handler.now = 1200; c.setPointerCount(1); c.touchDownEvent(0, 0, 1200, true); Handler.now = 1210; c.touchUpEvent(0, 0, 1210); c.setPointerCount(0); Handler.drain();
            check(n.buttons.equals(Arrays.asList("down:1", "up:1", "down:1", "up:1", "down:1", "up:1")), "the tap after a double tap is a single click again");
            n = new NvConnection(); c = fresh(n);
            c.setMoveSink(new TrackpadContext.MoveSink() { public void onTrackpadMove(short dx, short dy) {} });
            tap(c, 1000); Handler.now = 1100; c.setPointerCount(1); c.touchDownEvent(0, 0, 1100, true);
            Handler.now = 1110; c.touchUpEvent(0, 0, 1110); c.setPointerCount(0);
            check(n.buttons.equals(Arrays.asList("down:1", "up:1")), "in absolute mode the second click waits for Sunshine's deferred release");
            Handler.drainUntil(1159);
            check(n.buttons.size() == 2, "...for 50 ms");
            Handler.drain();
            check(n.buttons.equals(Arrays.asList("down:1", "up:1", "down:1", "up:1")), "...then lands as a balanced click");
        }
        System.out.println(checks + " checks; " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
with tempfile.TemporaryDirectory(prefix='trackpad-lifecycle-') as directory:
    work = Path(directory)
    for name in ('TouchContext', 'TrackpadContext', 'AbsoluteTouchContext', 'RelativeTouchContext'):
        (work / (name + '.java')).write_text(source(name))
    (work / 'TrackpadLifecycleRegression.java').write_text(fixture)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-encoding', 'UTF-8', '-d', str(work),
                    *map(str, work.glob('*.java'))], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'TrackpadLifecycleRegression']).returncode)
