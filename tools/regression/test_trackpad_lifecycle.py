#!/usr/bin/env python3
"""Compile the production trackpad against a deterministic Handler and mouse sink.

Exercise cancellation and pointer-count changes while a delayed tap/drag owns a
host button. --baseline reads the current HEAD before applying the fix.
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
            if '--baseline' in sys.argv else (ROOT / path).read_text())
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
    static void drain() {
        int budget = 1000;
        while (!jobs.isEmpty() && --budget > 0) {
            jobs.sort(Comparator.comparingLong(j -> j.due));
            Job j = jobs.remove(0); now = Math.max(now, j.due); j.r.run();
        }
        if (budget == 0) throw new AssertionError("unbounded callbacks");
    }
}
class MouseButtonPacket { static final byte BUTTON_LEFT=1, BUTTON_MIDDLE=2, BUTTON_RIGHT=3, BUTTON_X1=4, BUTTON_X2=5; }
class NvConnection {
    final Set<Byte> held = new HashSet<>();
    final List<String> buttons = new ArrayList<>();
    int moves;
    void sendMouseButtonDown(byte b) { held.add(b); buttons.add("down:" + b); }
    void sendMouseButtonUp(byte b) { held.remove(b); buttons.add("up:" + b); }
    void sendMouseMove(short x, short y) { ++moves; }
    void sendMouseHighResHScroll(short x) { ++moves; }
    void sendMouseHighResScroll(short y) { ++moves; }
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
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work),
                    *map(str, work.glob('*.java'))], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'TrackpadLifecycleRegression']).returncode)
