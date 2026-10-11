#!/usr/bin/env python3
"""Run both active production IME InputConnections against a
fake BaseInputConnection that reproduces AOSP's fallback mode (own editable copy, composing
text, commit/finish replayed as key events).

Composing text must reach the host once, through the ordered callback, and IME-synthesised
keys (Enter from performEditorAction) must go through the callback instead of overtaking it.
"""
import pathlib, subprocess, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
VIEWS = ('StreamContainer', 'ExternalControllerView')

def production_connection(view):
    path = 'app/src/main/java/com/limelight/ui/' + view + '.java'
    source = (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
              if '--baseline' in sys.argv else (ROOT / path).read_text(encoding='utf-8'))
    # Only adapt Android type names and the callback field to the fixture.
    source = source.replace('android.view.inputmethod.BaseInputConnection', 'BaseInputConnection')
    source = source.replace('android.text.Editable', 'Editable')
    source = source.replace('inputCallbacks', 'mInputCallbacks')
    marker = 'return new BaseInputConnection(this, false) {'
    start = source.index(marker); end = source.index('{', start); depth = 1
    while depth:
        end += 1; depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]
code = r'''
import java.util.*;
public class ImeConnectionRegression {
    static class KeyEvent {
        static final int ACTION_DOWN = 0, ACTION_UP = 1, ACTION_MULTIPLE = 2, KEYCODE_UNKNOWN = 0, KEYCODE_ENTER = 66;
        final int action, code; final String chars;
        KeyEvent(int action, int code) { this.action = action; this.code = code; this.chars = null; }
        KeyEvent(String chars) { this.action = ACTION_MULTIPLE; this.code = KEYCODE_UNKNOWN; this.chars = chars; }
        int getAction() { return action; } int getKeyCode() { return code; } String getCharacters() { return chars; }
        public String toString() { return chars != null ? "multi:" + chars : (action == ACTION_DOWN ? "down:" : "up:") + code; }
    }
    static class EditorInfo { static final int IME_ACTION_DONE = 6; }
    static class Editable {
        final StringBuilder text = new StringBuilder(); int composeStart = -1, composeEnd = -1;
        int length() { return text.length(); } public String toString() { return text.toString(); }
        void clear() { text.setLength(0); composeStart = composeEnd = -1; }
    }
    // AOSP BaseInputConnection in fallback mode (fullEditor == false).
    static class BaseInputConnection {
        static final List<String> keyEvents = new ArrayList<>();
        final Editable editable = new Editable();
        BaseInputConnection(Object view, boolean fullEditor) {}
        Editable getEditable() { return editable; }
        void replaceText(CharSequence cs, boolean composing) {
            Editable e = editable;
            int s = e.composeStart >= 0 ? e.composeStart : e.text.length(), t = e.composeStart >= 0 ? e.composeEnd : e.text.length();
            e.text.replace(s, t, cs.toString());
            if (composing) { e.composeStart = s; e.composeEnd = s + cs.length(); } else { e.composeStart = e.composeEnd = -1; }
        }
        void sendCurrentText() {
            if (editable.text.length() == 0) return;
            sendKeyEvent(new KeyEvent(editable.text.toString()));
            editable.clear();
        }
        public boolean setComposingText(CharSequence cs, int pos) { replaceText(cs, true); return true; }
        public boolean commitText(CharSequence cs, int pos) { replaceText(cs, false); sendCurrentText(); return true; }
        public boolean finishComposingText() { editable.composeStart = editable.composeEnd = -1; sendCurrentText(); return true; }
        public boolean deleteSurroundingText(int before, int after) { keyEvents.add("delete:" + before + "," + after); return true; }
        public boolean sendKeyEvent(KeyEvent event) { keyEvents.add(event.toString()); return true; }
        public boolean performEditorAction(int action) {
            sendKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_ENTER));
            sendKeyEvent(new KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_ENTER));
            return true;
        }
    }
    interface InputCallbacks {
        boolean handleCommitText(CharSequence text);
        boolean handleDeleteSurroundingText(int beforeLength, int afterLength);
        boolean handleImeKeyEvent(KeyEvent event);
    }
    static class Recorder implements InputCallbacks {
        boolean accept = true; final List<String> host = new ArrayList<>();
        public boolean handleCommitText(CharSequence text) { if (accept) host.add("text:" + text); return accept; }
        public boolean handleDeleteSurroundingText(int b, int a) { if (accept) host.add("delete:" + b + "," + a); return accept; }
        public boolean handleImeKeyEvent(KeyEvent e) { if (accept) host.add("key:" + e); return accept; }
    }
    InputCallbacks mInputCallbacks;
    BaseInputConnection make() {
        CONNECTION;
    }
    static int checks, failures;
    static void check(boolean ok, String message) { ++checks; if (!ok) ++failures; System.out.println((ok ? "PASS " : "FAIL ") + message); }
    static List<String> keys() { List<String> k = new ArrayList<>(BaseInputConnection.keyEvents); BaseInputConnection.keyEvents.clear(); return k; }
    public static void main(String[] args) {
        ImeConnectionRegression owner = new ImeConnectionRegression();
        Recorder rec = new Recorder(); owner.mInputCallbacks = rec;
        BaseInputConnection ic = owner.make();

        ic.setComposingText("hel", 1); ic.setComposingText("hello", 1); ic.commitText("hello", 1); ic.finishComposingText();
        check(keys().isEmpty() && rec.host.equals(Arrays.asList("text:hello")),
              "composed then committed text reaches the host once (" + rec.host + ")");

        rec.host.clear();
        ic.setComposingText("hi", 1); ic.finishComposingText();
        check(keys().isEmpty() && rec.host.equals(Arrays.asList("text:hi")),
              "text finished without a commit goes through the callback, not key events");

        rec.host.clear();
        ic.commitText("abc", 1); ic.performEditorAction(EditorInfo.IME_ACTION_DONE);
        check(keys().isEmpty() && rec.host.equals(Arrays.asList("text:abc", "key:down:66", "key:up:66")),
              "an editor action's Enter is routed through the callback behind the text");

        rec.host.clear();
        ic.deleteSurroundingText(2, 1);
        check(keys().isEmpty() && rec.host.equals(Arrays.asList("delete:2,1")), "deletions go through the callback");

        rec.host.clear(); rec.accept = false;
        ic.setComposingText("x", 1); ic.commitText("x", 1); ic.performEditorAction(EditorInfo.IME_ACTION_DONE);
        check(rec.host.isEmpty() && keys().equals(Arrays.asList("multi:x", "down:66", "up:66")),
              "when the callbacks decline (not streaming) the base fallback still produces key events");
        System.out.println(checks + " checks, " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
failed = False
for view in VIEWS:
    print(view, flush=True)
    with tempfile.TemporaryDirectory(prefix='ime-connection-') as directory:
        work = pathlib.Path(directory)
        path = work / 'ImeConnectionRegression.java'
        path.write_text(code.replace('CONNECTION', production_connection(view)), encoding='utf-8')
        subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(path)], check=True)
        failed |= subprocess.run(['java', '-cp', str(work), 'ImeConnectionRegression']).returncode != 0
raise SystemExit(failed)
