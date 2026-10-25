#!/usr/bin/env python3
"""Run the production TouchpadGate and TrackpadGestureDetector against scripted touchpad input.

No device is touched: MotionEvent, InputDevice, KeyEvent, Handler and Toast are deterministic
stand-ins, and the connection records every key and mouse packet. Covers the typing pause and
edge rejection, the Ctrl+Shift+M toggle, 3/4-finger swipes and taps turning into Windows
shortcuts, Alt released after an Alt+Tab switch, late or typing-guarded fingers ignored, pinch
versus scroll, the Android 14 classified pinch, and reset() releasing held modifiers.
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
    text = (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
            if '--baseline' in sys.argv else (ROOT / path).read_text(encoding='utf-8'))
    return re.sub(r'^(?:package|import) [^\n]+\n', '', text, flags=re.M)


fixture = r'''
import java.util.*;
class Looper { static Looper getMainLooper() { return new Looper(); } }
class Context {
    static final int MODE_PRIVATE = 0;
    static final Map<String, SharedPreferences> stores = new HashMap<>();
    SharedPreferences getSharedPreferences(String name, int mode) { return stores.computeIfAbsent(name, k -> new SharedPreferences()); }
}
class SharedPreferences {
    final Map<String, Boolean> values = new HashMap<>();
    boolean getBoolean(String k, boolean d) { return values.getOrDefault(k, d); }
    Editor edit() { return new Editor(); }
    class Editor { Editor putBoolean(String k, boolean v) { values.put(k, v); return this; } void apply() {} }
}
class Toast {
    static final int LENGTH_SHORT = 0, LENGTH_LONG = 1;
    static final List<String> shown = new ArrayList<>();
    final String text; Toast(String t) { text = t; }
    static Toast makeText(Context c, CharSequence t, int d) { return new Toast(t.toString()); }
    void show() { shown.add(text); } void cancel() {}
}
class Build { static class VERSION { static int SDK_INT = 34; } static class VERSION_CODES { static final int TIRAMISU = 33, UPSIDE_DOWN_CAKE = 34; } }
class Handler {
    static long now;
    static final List<Job> jobs = new ArrayList<>();
    static class Job { Handler owner; Runnable r; long due; Job(Handler h, Runnable r, long d) { owner=h; this.r=r; due=d; } }
    Handler(Looper ignored) {}
    void post(Runnable r) { postDelayed(r, 0); }
    void postDelayed(Runnable r, long delay) { jobs.add(new Job(this, r, now + delay)); }
    void removeCallbacks(Runnable r) { jobs.removeIf(j -> j.owner == this && j.r == r); }
    void removeCallbacksAndMessages(Object ignored) { jobs.removeIf(j -> j.owner == this); }
    // Run everything due up to and including time t
    static void drainUntil(long t) {
        int budget = 1000;
        for (;;) {
            jobs.sort(Comparator.comparingLong(j -> j.due));
            if (jobs.isEmpty() || jobs.get(0).due > t || --budget == 0) break;
            Job j = jobs.remove(0); now = Math.max(now, j.due); j.r.run();
        }
        if (budget == 0) throw new AssertionError("unbounded callbacks");
    }
    static void drain() { drainUntil(Long.MAX_VALUE); }
}
class KeyboardTranslator {
    static final int VK_TAB = 9, VK_LEFT = 37, VK_RIGHT = 39, VK_D = 68, VK_LWIN = 91, VK_LSHIFT = 160, VK_LCONTROL = 162, VK_LMENU = 164;
}
class KeyboardPacket { static final byte KEY_DOWN = 3, KEY_UP = 4, MODIFIER_SHIFT = 1, MODIFIER_CTRL = 2, MODIFIER_ALT = 4; }
class MouseButtonPacket { static final byte BUTTON_LEFT = 1, BUTTON_MIDDLE = 2, BUTTON_RIGHT = 3, BUTTON_X1 = 4, BUTTON_X2 = 5; }
class NvConnection {
    final List<String> log = new ArrayList<>();
    final Set<Short> keysHeld = new HashSet<>();
    final Set<Byte> buttonsHeld = new HashSet<>();
    int wheel;
    void sendKeyboardInput(short key, byte direction, byte modifiers, byte flags) {
        if (direction == KeyboardPacket.KEY_DOWN) keysHeld.add(key); else keysHeld.remove(key);
        log.add((direction == KeyboardPacket.KEY_DOWN ? "key+" : "key-") + key + (modifiers != 0 ? "/m" + modifiers : ""));
    }
    void sendMouseButtonDown(byte b) { buttonsHeld.add(b); log.add("btn+" + b); }
    void sendMouseButtonUp(byte b) { buttonsHeld.remove(b); log.add("btn-" + b); }
    void sendMouseHighResScroll(short v) { wheel += v; log.add("wheel" + (v > 0 ? "+" : "") + v); }
    void sendMouseHighResHScroll(short v) { log.add("hwheel" + v); }
    void sendMouseMove(short x, short y) { log.add("move"); }
    void sendMousePosition(short x, short y, short w, short h) { log.add("pos"); }
}
class InputDevice {
    static final int SOURCE_TOUCHPAD = 0x00100008;
    static class MotionRange {
        final float min, max, res; MotionRange(float min, float max, float res) { this.min = min; this.max = max; this.res = res; }
        float getMin() { return min; } float getMax() { return max; } float getRange() { return max - min; } float getResolution() { return res; }
    }
    final String name; final MotionRange x, y;
    InputDevice(String name, float w, float h, float res) { this.name = name; x = new MotionRange(0, w, res); y = new MotionRange(0, h, res); }
    String getName() { return name; }
    MotionRange getMotionRange(int axis) { return axis == MotionEvent.AXIS_X ? x : y; }
    MotionRange getMotionRange(int axis, int source) { return getMotionRange(axis); }
}
class KeyEvent {
    static final int KEYCODE_A = 29, KEYCODE_M = 41, KEYCODE_I = 37, KEYCODE_SHIFT_LEFT = 59, KEYCODE_SHIFT_RIGHT = 60, KEYCODE_ALT_LEFT = 57,
        KEYCODE_ALT_RIGHT = 58, KEYCODE_CTRL_LEFT = 113, KEYCODE_CTRL_RIGHT = 114, KEYCODE_META_LEFT = 117, KEYCODE_META_RIGHT = 118,
        KEYCODE_FUNCTION = 119, KEYCODE_CAPS_LOCK = 115, KEYCODE_NUM_LOCK = 143, KEYCODE_SCROLL_LOCK = 116, KEYCODE_SYSRQ = 120;
    final int code; final long time; final boolean ctrl, shift;
    KeyEvent(int code, long time, boolean ctrl, boolean shift) { this.code = code; this.time = time; this.ctrl = ctrl; this.shift = shift; }
    int getKeyCode() { return code; } long getEventTime() { return time; }
    boolean isCtrlPressed() { return ctrl; } boolean isShiftPressed() { return shift; } boolean isAltPressed() { return false; }
    boolean isPrintingKey() { return code == KEYCODE_A; } int getRepeatCount() { return 0; } int getScanCode() { return 0; }
    static boolean isModifierKey(int c) {
        return c == KEYCODE_SHIFT_LEFT || c == KEYCODE_SHIFT_RIGHT || c == KEYCODE_ALT_LEFT || c == KEYCODE_ALT_RIGHT ||
            c == KEYCODE_CTRL_LEFT || c == KEYCODE_CTRL_RIGHT || c == KEYCODE_META_LEFT || c == KEYCODE_META_RIGHT || c == KEYCODE_FUNCTION;
    }
    static String keyCodeToString(int c) { return "KEY_" + c; }
}
class MotionEvent {
    static final int ACTION_DOWN = 0, ACTION_UP = 1, ACTION_MOVE = 2, ACTION_CANCEL = 3, ACTION_POINTER_DOWN = 5, ACTION_POINTER_UP = 6;
    static final int AXIS_X = 0, AXIS_Y = 1, AXIS_GESTURE_PINCH_SCALE_FACTOR = 52, FLAG_CANCELED = 0x20;
    static final int CLASSIFICATION_NONE = 0, CLASSIFICATION_TWO_FINGER_SWIPE = 3, CLASSIFICATION_PINCH = 5;
    int action, actionIndex, buttonState, flags, classification; long time; float scale = 1;
    final List<Integer> ids = new ArrayList<>(); final List<float[]> pts = new ArrayList<>(); InputDevice device;
    int getActionMasked() { return action; } int getActionIndex() { return actionIndex; } long getEventTime() { return time; }
    int getPointerCount() { return ids.size(); } float getX(int i) { return pts.get(i)[0]; } float getY(int i) { return pts.get(i)[1]; }
    int getPointerId(int i) { return ids.get(i); } int findPointerIndex(int id) { return ids.indexOf(id); }
    InputDevice getDevice() { return device; } int getDeviceId() { return device == null ? -1 : device.hashCode(); }
    int getButtonState() { return buttonState; } int getFlags() { return flags; } int getClassification() { return classification; }
    float getAxisValue(int axis) { return axis == AXIS_GESTURE_PINCH_SCALE_FACTOR ? scale : 0; }
}
// Drives a contact sequence: fingers land (DOWN then POINTER_DOWN), move together, lift
class Pad {
    final InputDevice device; final List<Integer> ids = new ArrayList<>(); final Map<Integer, float[]> pos = new HashMap<>(); int nextId;
    Pad(InputDevice d) { device = d; }
    MotionEvent event(int action, int actionIndex, long time) {
        MotionEvent e = new MotionEvent(); e.action = action; e.actionIndex = actionIndex; e.time = time; e.device = device;
        for (int id : ids) { e.ids.add(id); e.pts.add(pos.get(id).clone()); }
        return e;
    }
    MotionEvent down(float x, float y, long time) {
        int id = nextId++; ids.add(id); pos.put(id, new float[]{x, y});
        return event(ids.size() == 1 ? MotionEvent.ACTION_DOWN : MotionEvent.ACTION_POINTER_DOWN, ids.size() - 1, time);
    }
    MotionEvent moveAll(float dx, float dy, long time) { for (int id : ids) { pos.get(id)[0] += dx; pos.get(id)[1] += dy; } return event(MotionEvent.ACTION_MOVE, 0, time); }
    MotionEvent moveOne(int index, float dx, float dy, long time) { int id = ids.get(index); pos.get(id)[0] += dx; pos.get(id)[1] += dy; return event(MotionEvent.ACTION_MOVE, 0, time); }
    MotionEvent up(long time) {
        int index = ids.size() - 1;
        MotionEvent e = event(ids.size() == 1 ? MotionEvent.ACTION_UP : MotionEvent.ACTION_POINTER_UP, index, time);
        pos.remove(ids.remove(index)); return e;
    }
}
public class TouchpadGesturesRegression {
    static int checks, failures;
    static void check(boolean ok, String message) { ++checks; if (!ok) ++failures; System.out.println((ok ? "PASS " : "FAIL ") + message); }
    static final InputDevice PAD = new InputDevice("generic touchpad", 1200, 800, 10f); // 10 units per mm, 120 x 80 mm
    static final InputDevice SAMSUNG = new InputDevice("sec_touchpad", 1200, 800, 10f);

    static class GateHost implements TouchpadGate.Host {
        int cancels; final List<Boolean> toggles = new ArrayList<>();
        public void cancelTouchpadTouches() { ++cancels; }
        public void onTouchpadToggled(boolean off) { toggles.add(off); }
    }
    static class GestureHost implements TrackpadGestureDetector.Host {
        int cancels; final List<String> combos = new ArrayList<>();
        public void cancelTrackpadTouches() { ++cancels; }
        public void sendKeyCombo(short[] keys) { StringBuilder b = new StringBuilder(); for (short k : keys) b.append(k).append('+'); combos.add(b.toString()); }
    }
    static TrackpadGestureDetector detector(NvConnection n, GestureHost h, boolean swap) {
        Handler.jobs.clear(); Handler.now = 0; Context.stores.clear(); Toast.shown.clear();
        return new TrackpadGestureDetector(new Context(), n, swap, h);
    }
    // Feed every event of a swipe: n fingers land 10 ms apart, move dx/dy in 5 steps, lift
    static boolean swipe(TrackpadGestureDetector d, Pad p, int fingers, float dx, float dy, long t0) {
        boolean allSwallowed = true;
        for (int i = 0; i < fingers; i++) { boolean s = d.onTouchpadEvent(p.down(400 + i * 60, 400, t0 + i * 10)); if (i >= 2) allSwallowed &= s; }
        long t = t0 + fingers * 10 + 60; // past SETTLE_MS
        for (int i = 0; i < 5; i++) { allSwallowed &= d.onTouchpadEvent(p.moveAll(dx / 5, dy / 5, t)); t += 20; }
        for (int i = 0; i < fingers; i++) { allSwallowed &= d.onTouchpadEvent(p.up(t)); t += 10; }
        Handler.now = t;
        return allSwallowed;
    }

    public static void main(String[] args) {
        // ---- TouchpadGate ----
        {
            GateHost h = new GateHost(); TouchpadGate g = new TouchpadGate(h, 500, 0); Pad p = new Pad(PAD);
            check(!g.shouldIgnore(p.down(600, 400, 100)), "before any key is typed a touch passes (no sentinel overflow)");
            g.shouldIgnore(p.up(110));
            g.onKeyDown(new KeyEvent(KeyEvent.KEYCODE_A, 1000, false, false));
            MotionEvent d = p.down(600, 400, 1200);
            check(g.shouldIgnore(d), "a touch starting 200 ms after a key press is dropped by a 500 ms typing pause");
            check(g.shouldIgnore(p.moveAll(50, 0, 1250)) && g.shouldIgnore(p.up(1300)), "the dropped touch stays dropped for its whole sequence");
            check(!g.shouldIgnore(p.down(600, 400, 1600)) && !g.shouldIgnore(p.up(1650)), "a touch starting after the pause passes");
            g.onKeyDown(new KeyEvent(KeyEvent.KEYCODE_SHIFT_LEFT, 2000, false, false));
            check(!g.shouldIgnore(p.down(600, 400, 2100)), "a modifier key does not start the pause");
            g.onKeyDown(new KeyEvent(KeyEvent.KEYCODE_A, 2150, false, false));
            check(h.cancels == 1 && g.shouldIgnore(p.moveAll(10, 0, 2200)), "typing while a finger is down cancels it and drops the rest of the sequence");
            g.shouldIgnore(p.up(2300));
        }
        {
            GateHost h = new GateHost(); TouchpadGate g = new TouchpadGate(h, 0, 3); Pad p = new Pad(PAD);
            check(g.shouldIgnore(p.down(15, 400, 100)), "a touch starting 1.5 mm from the left edge is dropped by a 3 mm margin");
            g.shouldIgnore(p.up(110));
            check(g.shouldIgnore(p.down(600, 790, 200)), "a touch starting 1 mm from the bottom edge is dropped");
            g.shouldIgnore(p.up(210));
            check(!g.shouldIgnore(p.down(600, 400, 300)), "a touch starting in the middle passes");
            check(!g.shouldIgnore(p.moveAll(-590, 0, 350)) && !g.shouldIgnore(p.up(400)), "a touch that travels to the edge is unaffected");
            check(!g.shouldIgnore(p.down(45, 400, 500)), "a touch starting 4.5 mm in passes a 3 mm margin");
            g.shouldIgnore(p.up(510));
        }
        {
            GateHost h = new GateHost(); TouchpadGate g = new TouchpadGate(h, 0, 0); Pad p = new Pad(PAD);
            check(g.onKeyDown(new KeyEvent(KeyEvent.KEYCODE_M, 100, true, true)), "Ctrl+Shift+M is consumed");
            check(g.isOff() && h.toggles.equals(Arrays.asList(true)) && h.cancels == 1, "the toggle switches the pad off, cancels touches and tells the host");
            check(g.onKeyDown(new KeyEvent(KeyEvent.KEYCODE_M, 130, true, true)), "a key repeat while held is swallowed without toggling");
            check(g.isOff(), "...and the pad stays off");
            check(g.onKeyUp(new KeyEvent(KeyEvent.KEYCODE_M, 200, true, true)), "the release of the toggle key is swallowed");
            check(g.shouldIgnore(p.down(600, 400, 300)) && g.shouldIgnore(p.up(310)), "touches are dropped while the pad is off");
            check(!g.onKeyDown(new KeyEvent(KeyEvent.KEYCODE_A, 400, false, false)), "other keys still pass to the host");
            check(!g.onKeyDown(new KeyEvent(KeyEvent.KEYCODE_M, 500, true, false)), "Ctrl+M without Shift is not the toggle");
            g.onKeyDown(new KeyEvent(KeyEvent.KEYCODE_M, 600, true, true)); g.onKeyUp(new KeyEvent(KeyEvent.KEYCODE_M, 650, true, true));
            check(!g.isOff() && h.toggles.equals(Arrays.asList(true, false)), "a second Ctrl+Shift+M switches the pad back on");
            check(!g.shouldIgnore(p.down(600, 400, 700)), "touches pass again");
            g.shouldIgnore(p.up(710));
        }

        // ---- TrackpadGestureDetector: swipes ----
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            boolean swallowed = swipe(d, p, 3, 0, -200, 1000); Handler.drain();
            check(swallowed, "every event of a 3-finger gesture is kept from the trackpad contexts");
            check(h.cancels == 1, "the contexts are cancelled when the third finger lands");
            check(h.combos.equals(Arrays.asList("91+9+")), "3 fingers up sends Win+Tab (Task View)");
            check(n.keysHeld.isEmpty() && n.buttonsHeld.isEmpty(), "nothing is left held on the host");
        }
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            swipe(d, p, 3, 0, 200, 1000); Handler.drain();
            check(h.combos.equals(Arrays.asList("91+68+")), "3 fingers down sends Win+D (show desktop)");
            n = new NvConnection(); h = new GestureHost(); d = detector(n, h, false); p = new Pad(SAMSUNG);
            swipe(d, p, 3, 0, 200, 1000); Handler.drain();
            check(h.combos.equals(Arrays.asList("91+9+")), "the Samsung cover (sec_touchpad) reports the vertical direction inverted and is corrected");
        }
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            swipe(d, p, 3, 200, 0, 1000);
            check(n.log.indexOf("key+164") == 0 && n.log.contains("key+9/m4"), "3 fingers right holds Alt and presses Tab");
            check(n.keysHeld.contains((short) 164), "Alt is still held while the fingers are down");
            Handler.drain();
            check(!n.keysHeld.contains((short) 164) && n.keysHeld.isEmpty(), "Alt is released after the fingers lift so the highlighted window is selected");
            check(n.log.indexOf("key-164") == n.log.size() - 1, "Alt goes up last");
        }
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            swipe(d, p, 3, -200, 0, 1000); Handler.drain();
            check(n.log.contains("key+160/m4") && n.log.contains("key+9/m5"), "3 fingers left sends Shift+Tab under Alt");
            check(n.keysHeld.isEmpty(), "Shift and Alt are both released");
        }
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            swipe(d, p, 4, -200, 0, 1000); Handler.drain();
            check(h.combos.equals(Arrays.asList("162+91+39+")), "4 fingers left sends Ctrl+Win+Right (next virtual desktop)");
            n = new NvConnection(); h = new GestureHost(); d = detector(n, h, false); p = new Pad(PAD);
            swipe(d, p, 4, 200, 0, 1000); Handler.drain();
            check(h.combos.equals(Arrays.asList("162+91+37+")), "4 fingers right sends Ctrl+Win+Left");
        }
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, true); Pad p = new Pad(PAD);
            swipe(d, p, 3, -200, 0, 1000); Handler.drain(); // with swapped axes a raw horizontal move is vertical
            check(h.combos.equals(Arrays.asList("91+9+")), "swapped axes turn a raw horizontal swipe into the vertical gesture");
        }

        // ---- taps, late fingers, typing guard ----
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            d.onTouchpadEvent(p.down(400, 400, 1000)); d.onTouchpadEvent(p.down(460, 400, 1010)); d.onTouchpadEvent(p.down(520, 400, 1020));
            d.onTouchpadEvent(p.up(1100)); d.onTouchpadEvent(p.up(1110)); d.onTouchpadEvent(p.up(1120));
            check(n.log.equals(Arrays.asList("btn+4")), "a 3-finger tap presses mouse back (X1)");
            Handler.drain();
            check(n.log.equals(Arrays.asList("btn+4", "btn-4")) && n.buttonsHeld.isEmpty(), "...and releases it shortly after");
            n = new NvConnection(); h = new GestureHost(); d = detector(n, h, false); p = new Pad(PAD);
            for (int i = 0; i < 4; i++) d.onTouchpadEvent(p.down(400 + 60 * i, 400, 1000 + 10 * i));
            for (int i = 0; i < 4; i++) d.onTouchpadEvent(p.up(1100 + 10 * i));
            Handler.drain();
            check(n.log.equals(Arrays.asList("btn+5", "btn-5")), "a 4-finger tap clicks mouse forward (X2)");
        }
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            d.onTouchpadEvent(p.down(400, 400, 1000)); d.onTouchpadEvent(p.down(460, 400, 1010));
            boolean third = d.onTouchpadEvent(p.down(520, 400, 1400)); // 400 ms after the first finger
            boolean move = d.onTouchpadEvent(p.moveAll(0, -200, 1450));
            check(!third && !move && h.combos.isEmpty() && h.cancels == 0, "a third finger landing late is a resting thumb: no gesture, events pass to the contexts");
            d.onTouchpadEvent(p.up(1500)); d.onTouchpadEvent(p.up(1510)); d.onTouchpadEvent(p.up(1520));
        }
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            d.onKeyDown(new KeyEvent(KeyEvent.KEYCODE_A, 900, false, false));
            boolean swallowed = swipe(d, p, 3, 0, -200, 1000); Handler.drain();
            check(!swallowed && h.combos.isEmpty(), "a 3-finger swipe right after typing is a palm: ignored");
            d.onKeyDown(new KeyEvent(KeyEvent.KEYCODE_CTRL_LEFT, 2900, false, false));
            swipe(d, p, 3, 0, -200, 3000); Handler.drain();
            check(h.combos.equals(Arrays.asList("91+9+")), "a modifier key does not arm the typing guard");
        }

        // ---- pinch versus scroll ----
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            d.onTouchpadEvent(p.down(500, 400, 1000)); d.onTouchpadEvent(p.down(600, 400, 1010));
            boolean held = d.onTouchpadEvent(p.moveAll(0, 10, 1030));
            check(held, "the first small two-finger movement is held back until pinch and scroll can be told apart");
            boolean scroll = d.onTouchpadEvent(p.moveAll(0, 30, 1050));
            check(!scroll && n.log.isEmpty() && h.cancels == 0, "fingers moving together are a scroll: handed to the trackpad contexts, nothing sent");
            check(!d.onTouchpadEvent(p.moveAll(0, 30, 1070)), "...and later scroll movement passes straight through");
            d.onTouchpadEvent(p.up(1100)); d.onTouchpadEvent(p.up(1110));
        }
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            d.onTouchpadEvent(p.down(500, 400, 1000)); d.onTouchpadEvent(p.down(600, 400, 1010)); // 100 units = 10 mm apart
            boolean held = d.onTouchpadEvent(p.moveOne(0, -15, 0, 1030)); // 115 apart: 1.5 mm, still undecided
            check(held && n.log.isEmpty(), "a 1.5 mm change of finger distance is held back, nothing sent yet");
            boolean zoom = d.onTouchpadEvent(p.moveOne(1, 15, 0, 1040)); // 130 apart: 3 mm > the 2.5 mm decision distance, +30 %
            check(zoom && !n.log.isEmpty() && n.log.get(0).equals("key+162") && n.wheel == 120 && h.cancels == 1,
                  "fingers spreading 30 % is a pinch: Ctrl is held, one wheel notch up is sent, the contexts are cancelled");
            d.onTouchpadEvent(p.moveOne(1, 20, 0, 1060)); // 150 apart: a further +15 %
            check(n.wheel == 240, "each further 15 % of spread adds a notch");
            d.onTouchpadEvent(p.moveOne(1, -60, 0, 1080)); // 90 apart: shrink by 40 %
            check(n.wheel < 240, "fingers closing send wheel down");
            check(d.onTouchpadEvent(p.up(1100)) && !n.keysHeld.contains((short) 162), "lifting a finger releases Ctrl and the rest of the sequence is swallowed");
            check(d.onTouchpadEvent(p.up(1110)) && n.keysHeld.isEmpty(), "the last UP is swallowed too and nothing is held");
            check(!d.onTouchpadEvent(p.down(500, 400, 1300)), "the next contact sequence starts clean");
            d.onTouchpadEvent(p.up(1310));
        }
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            MotionEvent pinch = p.down(500, 400, 1000); pinch.classification = MotionEvent.CLASSIFICATION_PINCH;
            check(d.onPinchGesture(pinch) && n.keysHeld.contains((short) 162), "an Android 14 classified pinch holds Ctrl");
            MotionEvent move = p.moveAll(0, 0, 1020); move.classification = MotionEvent.CLASSIFICATION_PINCH; move.scale = 1.4f;
            check(d.onPinchGesture(move) && n.wheel == 240, "a 1.4x scale factor is two notches up");
            MotionEvent up = p.up(1040); up.classification = MotionEvent.CLASSIFICATION_PINCH;
            check(d.onPinchGesture(up) && n.keysHeld.isEmpty(), "the pinch ending releases Ctrl");
            MotionEvent plain = p.down(500, 400, 1100);
            check(!d.onPinchGesture(plain), "an unclassified event is not a pinch");
            d.onPinchGesture(p.up(1110));
            Build.VERSION.SDK_INT = 33;
            MotionEvent old = p.down(500, 400, 1200); old.classification = MotionEvent.CLASSIFICATION_PINCH;
            check(!d.onPinchGesture(old), "below Android 14 the classified pinch path is off");
            Build.VERSION.SDK_INT = 34; p.up(1210);
        }

        // ---- reset ----
        {
            NvConnection n = new NvConnection(); GestureHost h = new GestureHost(); TrackpadGestureDetector d = detector(n, h, false); Pad p = new Pad(PAD);
            for (int i = 0; i < 3; i++) d.onTouchpadEvent(p.down(400 + 60 * i, 400, 1000 + 10 * i));
            long t = 1100; for (int i = 0; i < 5; i++) { d.onTouchpadEvent(p.moveAll(40, 0, t)); t += 20; }
            check(n.keysHeld.contains((short) 164), "mid-switch Alt is held");
            d.reset();
            check(n.keysHeld.isEmpty(), "reset() (focus loss, stream end) releases Alt and Tab");
            int events = n.log.size(); Handler.drain();
            check(n.log.size() == events, "no delayed key event fires after reset");
        }
        System.out.println(checks + " checks; " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
with tempfile.TemporaryDirectory(prefix='touchpad-gestures-') as directory:
    work = Path(directory)
    for name in ('TouchpadGate', 'TrackpadGestureDetector'):
        (work / (name + '.java')).write_text(source(name), encoding='utf-8')
    (work / 'TouchpadGesturesRegression.java').write_text(fixture, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-encoding', 'UTF-8', '-d', str(work),
                    *map(str, work.glob('*.java'))], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'TouchpadGesturesRegression']).returncode)
