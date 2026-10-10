#!/usr/bin/env python3
"""Run the production commit-text chunker and flush runnable with a fake main-thread Handler.

A commit that splits into more than one chunk must still schedule the flush; the queue
used to stay stuck for the session after any commit over 512 UTF-8 bytes.
"""
import pathlib, subprocess, sys, tempfile
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
prefix = r'''
import java.nio.charset.StandardCharsets;
import java.util.*;
public class CommitTextRegression {
    static class Handler {
        final List<Runnable> pending = new ArrayList<>();
        boolean post(Runnable r) { pending.add(r); return true; }
        boolean postDelayed(Runnable r, long delay) { pending.add(r); return true; }
        void drain() { while (!pending.isEmpty()) pending.remove(0).run(); }
    }
    static class Conn { final List<String> sent = new ArrayList<>(); void sendUtf8Text(String s) { sent.add(s); } }
    private static final int UTF8_CHUNK_SIZE = 512;
    private final Queue<String> commitTextQueue = new ArrayDeque<>();
    private final Handler commitTextHandler = new Handler();
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
        System.out.println(checks + " checks, " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
with tempfile.TemporaryDirectory(prefix='commit-text-') as directory:
    work = pathlib.Path(directory)
    path = work / 'CommitTextRegression.java'
    path.write_text(prefix + flush + '\n' + enqueue + '\n' + suffix, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(path)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'CommitTextRegression']).returncode)
