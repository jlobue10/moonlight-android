#!/usr/bin/env python3
"""Run the production commit-text chunker and flush runnable with a fake main-thread Handler.

A commit that splits into more than one chunk must still schedule the flush; the queue
used to stay stuck for the session after any commit over 512 UTF-8 bytes.
"""
import pathlib, re, subprocess, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/Game.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True, encoding='utf-8')
          if '--baseline' in sys.argv else (ROOT / PATH).read_text(encoding='utf-8'))

def block(marker):
    start = source.index(marker); end = source.index('{', start); depth = 1
    while depth:
        end += 1; depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]

flush = block('    private final Runnable flushCommitTextQueue') + ';'
enqueue = block('    private void enqueueCommitText(')
deletion = block('    public boolean handleDeleteSurroundingText(').replace(
    'com.limelight.nvstream.input.KeyboardPacket.', 'KeyboardPacket.')
stop = block('    private void stopConnection(').replace('Game.this', 'CommitTextRegression.this')
destroy = block('    protected void onDestroy(')
destroy = destroy[:destroy.index('        if (prefConfig.enableFullExDisplay)')] + '\n}'
destroy = destroy.replace('super.onDestroy();', '')  # Android Activity boundary
motion_guard = block('    public boolean handleMotionEvent(')
motion_guard = motion_guard[:motion_guard.index('        int deviceId')] + '\nreturn true;\n}'
helpers = '\n'.join(block(marker) for marker in (
    '    private void enqueueTextInput(', '    private void stopCommitText(', '    private Runnable sendImeKeys(',
    '    private void stopTouchInput(', '    private void stopShortcutKeys(', '    public void sendKeys(')
    if marker in source).replace('com.limelight.nvstream.input.KeyboardPacket.', 'KeyboardPacket.')
queue_field = re.search(r'private final Queue<[^;]+;', source).group()
constants = '\n'.join(re.search(r'private static final int ' + name + r' = \d+;', source).group()
                      for name in ('UTF8_CHUNK_SIZE', 'MAX_IME_DELETE', 'IME_KEYS_PER_STEP')
                      if re.search(r'private static final int ' + name + r' = \d+;', source))
stopped_field = 'private boolean commitTextStopped;' if 'commitTextStopped' in source else ''
prefix = r'''
import java.nio.charset.StandardCharsets;
import java.util.*;
public class CommitTextRegression {
    static class Handler {
        static final List<Handler> all = new ArrayList<>();
        final List<Runnable> pending = new ArrayList<>();
        Handler() { all.add(this); }
        Handler(Looper ignored) { this(); }
        boolean post(Runnable r) { pending.add(r); return true; }
        boolean postDelayed(Runnable r, long delay) { pending.add(r); return true; }
        void removeCallbacksAndMessages(Object token) { pending.clear(); }
        void drain() { while (!pending.isEmpty()) { Conn.inFlush = 0; pending.remove(0).run(); } }
        static List<Runnable> callbacks() {
            List<Runnable> result = new ArrayList<>();
            for (Handler h : all) result.addAll(h.pending);
            return result;
        }
        static void drainAll() { for (Handler h : new ArrayList<>(all)) h.drain(); }
    }
    static class Looper { static Looper getMainLooper() { return new Looper(); } }
    static class GameMenu { static final long KEY_UP_DELAY = 25; }
    static byte getModifier(short key) { return key == 17 ? (byte)2 : 0; }
    static class Conn {
        static final List<String> hostInput = new ArrayList<>();
        final List<String> sent = new ArrayList<>();
        void sendUtf8Text(String s) { sent.add(s); hostInput.add("text:" + s); }
        static int inFlush, maxInFlush;
        void sendKeyboardInput(short code, byte action, byte mods, byte flags) {
            hostInput.add((action == KeyboardPacket.KEY_DOWN ? "down:" : "up:") + code);
            if (++inFlush > maxInFlush) maxInFlush = inFlush;
        }
        void stop() {}
    }
    static class KeyEvent { static final int KEYCODE_DEL = 67, KEYCODE_FORWARD_DEL = 112; }
    static class KeyboardPacket { static final byte KEY_DOWN = 3, KEY_UP = 4; }
    static class Translator { short translate(int key, int modifiers, int id) { return (short)key; } }
    static class Pref { boolean enableCommitText = true; }
    static class Controller { void stop() {} }
    static class TouchContext { int cancellations; void cancelTouch() { ++cancellations; } }
    static class View {}
    static class MotionEvent {}
    boolean touchInputStopped, grabbedInput = true;
    static class StreamLog { static void stop() {} }
    static CommitTextRegression instance;
    final TouchContext[] touchContextMap = {new TouchContext(), null};
    final TouchContext[] trackpadContextMap = {null, new TouchContext()};
    final Handler timerHandler = new Handler();
    static class UiHelper { static void notifyStreamEnded(Object owner) {} }
    // The background network teardown is outside this fixture. The production
    // main-thread stopConnection path, including queue cancellation, runs normally.
    static class Thread { public void run() {} void start() {} void sleep(int millis) {} }
    static class Http { void quitApp() {} }
    static class Toast {
        static final int LENGTH_LONG = 1;
        static Toast makeText(Object context, String text, int length) { return new Toast(); }
        void show() {}
    }
    static class R { static class string { static final int applist_quit_success = 1; } }
    static class Resources { String getString(int id) { return ""; } }
    Resources getResources() { return new Resources(); }
    void runOnUiThread(Runnable task) {}
    void updatePipAutoEnter() {}
    boolean connected = true, connecting, quitOnStop;
    String appName = "fixture";
    Http httpConn;
    Controller controllerHandler = new Controller();
    Translator keyboardTranslator = new Translator();
    Pref prefConfig = new Pref();
    // CONSTANTS
    // QUEUE_FIELD
    private final Handler commitTextHandler = new Handler();
    private final Handler shortcutHandler = new Handler(Looper.getMainLooper());
    private final Set<Runnable> pendingShortcutReleases = new HashSet<>();
    Conn conn = new Conn();
    static int checks, failures;
    static void check(boolean ok, String message) { ++checks; if (!ok) ++failures; System.out.println((ok ? "PASS " : "FAIL ") + message); }
    static String repeat(String s, int n) { StringBuilder b = new StringBuilder(); for (int i = 0; i < n; i++) b.append(s); return b.toString(); }
'''
suffix = r'''
    public static void main(String[] args) {
        CommitTextRegression r = new CommitTextRegression();
        String big = repeat("x", 1000);
        r.enqueueCommitText(big);
        r.commitTextHandler.drain();
        check(r.conn.sent.size() == 2 && String.join("", r.conn.sent).equals(big),
              "a commit over one chunk is flushed in order (" + r.conn.sent.size() + " sends)");
        r.enqueueCommitText("a");
        r.commitTextHandler.drain();
        check(r.conn.sent.size() == 3 && r.conn.sent.get(2).equals("a"), "a later single-character commit still flushes");
        r.enqueueCommitText(big); r.enqueueCommitText("b");
        r.commitTextHandler.drain();
        check(r.conn.sent.size() == 6 && String.join("", r.conn.sent.subList(3, 6)).equals(big + "b"),
              "a commit queued behind a busy queue is flushed after it");
        String emoji = repeat("x", 510) + "\uD83D\uDE00" + "y";  // 4-byte code point straddling the 512 boundary
        r.enqueueCommitText(emoji);
        r.commitTextHandler.drain();
        String first = r.conn.sent.get(6);
        check(r.conn.sent.size() == 8 && first.length() == 510 && r.conn.sent.get(7).equals("\uD83D\uDE00y"),
              "a code point straddling the chunk boundary is not split");
        r.enqueueCommitText(""); r.enqueueCommitText(null);
        r.commitTextHandler.drain();
        check(r.conn.sent.size() == 8, "empty and null commits send nothing");

        Conn.hostInput.clear();
        r.enqueueCommitText("abc");
        r.handleDeleteSurroundingText(1, 0);
        r.enqueueCommitText("d");
        r.commitTextHandler.drain();
        check(Conn.hostInput.equals(Arrays.asList("text:abc", "down:67", "up:67", "text:d")),
              "commit, delete, replacement retain IME order before the first flush");

        Conn.hostInput.clear();
        r.enqueueCommitText(big);
        r.commitTextHandler.pending.remove(0).run();
        r.handleDeleteSurroundingText(1, 0);
        r.commitTextHandler.drain();
        check(Conn.hostInput.size() == 4 && Conn.hostInput.get(1).startsWith("text:") &&
              Conn.hostInput.get(2).equals("down:67"), "delete waits for the remaining paste chunks");

        Conn.hostInput.clear();
        r.handleDeleteSurroundingText(1, 2);
        r.commitTextHandler.drain();
        check(Conn.hostInput.equals(Arrays.asList("down:67", "up:67", "down:112", "up:112", "down:112", "up:112")),
              "deleteSurroundingText also deletes the requested following characters");

        // Deletions are paced: never more than one step's key pairs per flush, and an
        // IME "clear the field" count is bounded instead of spinning the main thread.
        Conn.hostInput.clear(); Conn.maxInFlush = 0;
        r.handleDeleteSurroundingText(200, 0);
        r.commitTextHandler.drain();
        check(Conn.hostInput.size() == 400 && Conn.maxInFlush <= 32,
              "a long deletion is spread over flush steps (" + Conn.maxInFlush + " packets per step)");
        Conn.hostInput.clear();
        r.handleDeleteSurroundingText(Integer.MAX_VALUE, Integer.MAX_VALUE);
        r.commitTextHandler.drain();
        check(Conn.hostInput.size() == 4 * 512, "an unbounded deletion request is clamped");
        Conn.hostInput.clear();
        r.handleDeleteSurroundingText(-5, 0);
        r.commitTextHandler.drain();
        check(Conn.hostInput.isEmpty(), "a negative deletion count sends nothing");

        Conn.hostInput.clear();
        r.enqueueCommitText(big);
        r.handleDeleteSurroundingText(1, 0);
        r.stopConnection();
        check(r.touchContextMap[0].cancellations == 1 && r.trackpadContextMap[1].cancellations == 1,
              "disconnect cancels both touchscreen and physical-trackpad contexts, including partially initialized maps");
        check(!r.handleMotionEvent(null, null), "late motion events cannot revive a retired activity's input");
        check(r.commitTextQueue.isEmpty() && r.commitTextHandler.pending.isEmpty(),
              "disconnect cancels pending IME work and releases queued payloads");
        // NvConnection's JNI input entry points address the active native session;
        // an old Java wrapper is not a session fence. Model that new destination.
        Conn.hostInput.clear();
        r.commitTextQueue.add(() -> r.conn.sendUtf8Text("stale"));
        r.commitTextHandler.pending.add(r.flushCommitTextQueue);
        r.commitTextHandler.drain();
        check(Conn.hostInput.isEmpty() && r.commitTextQueue.isEmpty(),
              "a retired queue cannot type into the next native session even if its flush still runs");
        r.enqueueCommitText("late");
        r.handleDeleteSurroundingText(1, 0);
        r.commitTextHandler.drain();
        check(Conn.hostInput.isEmpty(), "late IME callbacks after disconnect cannot revive the queue");
        CommitTextRegression destroyed = new CommitTextRegression();
        destroyed.onDestroy();
        check(destroyed.touchContextMap[0].cancellations == 1 && destroyed.trackpadContextMap[1].cancellations == 1,
              "direct activity destruction also retires both touch context maps");

        Handler.drainAll(); Conn.hostInput.clear();
        CommitTextRegression shortcut = new CommitTextRegression();
        shortcut.sendKeys(new short[]{17, 86});
        check(Conn.hostInput.equals(Arrays.asList("down:17", "down:86")), "shortcut holds its keys until the delayed release");
        Handler.drainAll();
        check(Conn.hostInput.equals(Arrays.asList("down:17", "down:86", "up:86", "up:17")),
              "normal shortcut releases in reverse order");
        Conn.hostInput.clear(); shortcut.sendKeys(new short[]{17, 86});
        List<Runnable> oldCallbacks = Handler.callbacks();
        shortcut.stopConnection();
        check(Conn.hostInput.equals(Arrays.asList("down:17", "down:86", "up:86", "up:17")) && Handler.callbacks().isEmpty(),
              "disconnect releases owned shortcut keys before native stop and removes their callbacks");
        int releasedEvents = Conn.hostInput.size(); shortcut.stopConnection();
        check(Conn.hostInput.size() == releasedEvents, "repeated shortcut shutdown sends no duplicate releases");
        Conn.hostInput.clear();
        for (Runnable callback : oldCallbacks) callback.run();
        Handler.drainAll();
        check(Conn.hostInput.isEmpty(), "a stale shortcut release cannot reach the next native session");
        shortcut.sendKeys(new short[]{17, 86});
        check(Conn.hostInput.isEmpty() && Handler.callbacks().isEmpty(), "a late menu action cannot revive shortcut input");
        Handler.drainAll(); Conn.hostInput.clear();
        CommitTextRegression shortcutDestroyed = new CommitTextRegression();
        shortcutDestroyed.sendKeys(new short[]{17, 86}); shortcutDestroyed.onDestroy();
        check(Conn.hostInput.equals(Arrays.asList("down:17", "down:86", "up:86", "up:17")) && Handler.callbacks().isEmpty(),
              "direct destruction also releases and retires shortcuts");
        Handler.drainAll(); Conn.hostInput.clear();
        CommitTextRegression disconnected = new CommitTextRegression(); disconnected.connected = false;
        disconnected.sendKeys(new short[]{17, 86});
        check(Conn.hostInput.isEmpty(), "no shortcut is sent without a connected stream");
        System.out.println(checks + " checks, " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
with tempfile.TemporaryDirectory(prefix='commit-text-') as directory:
    work = pathlib.Path(directory)
    path = work / 'CommitTextRegression.java'
    prefix = prefix.replace('// CONSTANTS', constants).replace('// QUEUE_FIELD', queue_field + '\n' + stopped_field)
    path.write_text(prefix + flush + '\n' + enqueue + '\n' + deletion + '\n' + stop + '\n' + destroy + '\n' + motion_guard + '\n' + helpers + '\n' + suffix, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(path)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'CommitTextRegression']).returncode)
