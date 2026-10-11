#!/usr/bin/env python3
"""Run the production .art shortcut parser (ShortcutTrampoline.parseArtReader) against bounded
and unbounded input, and the ServerCommand list bound (NvHTTP.boundServerCmds).

A content:// file from any app used to be read with readLine(): one huge line was an OutOfMemoryError
the IOException handler never saw.
"""
import pathlib, subprocess, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
ST = 'app/src/main/java/com/limelight/ShortcutTrampoline.java'
SH = 'app/src/main/java/com/limelight/utils/ShortcutHelper.java'
NV = 'app/src/main/java/com/limelight/nvstream/http/NvHTTP.java'

def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
            if '--baseline' in sys.argv else (ROOT / path).read_text(encoding='utf-8'))

def block(source, marker):
    start = source.index(marker); end = source.index('{', start); depth = 1
    while depth:
        end += 1; depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]

def field(source, name):
    import re
    m = re.search(r'static final int ' + name + r' = [^;]+;', source)
    return m.group() if m else ''

st = read(ST)
nv = read(NV)
sh = read(SH)
exporter = block(sh, '    static String artValue(') if 'artValue(' in sh else 'static String artValue(String v) { return v; }'
parser = '\n'.join(filter(None, [field(st, 'ART_MAX_BYTES'), field(st, 'ART_MAX_LINE'), field(st, 'ART_MAX_ENTRIES'),
                                  block(st, '    static void applyArtLine(') if 'applyArtLine(' in st else '',
                                  block(st, '    static Map<String, String> parseArtReader(') if 'parseArtReader(' in st else '']))
bound = '\n'.join(filter(None, [field(nv, 'MAX_SERVER_COMMANDS'), field(nv, 'MAX_SERVER_COMMAND_LENGTH'),
                                 block(nv, '    static List<String> boundServerCmds(') if 'boundServerCmds(' in nv else '']))
if not parser.strip() or not bound.strip():
    print('FAIL production helpers missing (baseline source)'); raise SystemExit(1)
code = r'''
import java.io.*;
import java.util.*;
public class ArtFileRegression {
    static int checks, failures;
    static void check(boolean ok, String message) { ++checks; if (!ok) ++failures; System.out.println((ok ? "PASS " : "FAIL ") + message); }
''' + exporter + r'''
''' + parser + '\n' + bound + r'''
    static String repeat(char c, int n) { char[] a = new char[n]; Arrays.fill(a, c); return new String(a); }
    public static void main(String[] args) throws Exception {
        Map<String, String> data = parseArtReader(new StringReader("# comment\n[Name] Steam Game\r\n\n[UUID] abc-123\n"));
        check(data.size() == 2 && data.get("Name").equals("Steam Game") && data.get("UUID").equals("abc-123"),
              "a normal .art file parses (comments, blank lines, CRLF)");
        try { parseArtReader(new StringReader("Name Steam\n")); check(false, "a line without a bracket is rejected"); }
        catch (IOException e) { check(true, "a line without a bracket is rejected"); }
        try { parseArtReader(new StringReader("[Name] " + repeat('x', 10000))); check(false, "an over-long line is rejected"); }
        catch (IOException e) { check(true, "an over-long line is rejected"); }
        try { parseArtReader(new StringReader(repeat('#', 70000))); check(false, "an over-size file is rejected before it is buffered"); }
        catch (IOException e) { check(true, "an over-size file is rejected before it is buffered"); }
        StringBuilder many = new StringBuilder();
        for (int i = 0; i < 300; i++) many.append("[k").append(i).append("] v\n");
        try { parseArtReader(new StringReader(many.toString())); check(false, "too many entries are rejected"); }
        catch (IOException e) { check(true, "too many entries are rejected"); }

        try { parseArtReader(new StringReader("[host_uuid] a\n[app_name] x\n[host_uuid] b\n")); check(false, "a repeated key is rejected instead of retargeting the shortcut"); }
        catch (IOException e) { check(true, "a repeated key is rejected instead of retargeting the shortcut"); }
        check(artValue("Desk\n[host_uuid] evil").equals("Desk [host_uuid] evil"), "an exported value cannot carry a line of its own");
        check(artValue("  plain \r\n").equals("plain"), "an exported value is trimmed");
        Map<String, String> exported = parseArtReader(new StringReader("[host_name] " + artValue("Desk\n[host_uuid] evil") + "\n[host_uuid] good\n"));
        check("good".equals(exported.get("host_uuid")), "the sanitised export keeps the real host uuid");

        List<String> big = new ArrayList<>();
        for (int i = 0; i < 1000; i++) big.add(repeat('c', 1000));
        List<String> bounded = boundServerCmds(big);
        check(bounded.size() == 64 && bounded.get(0).length() == 256, "the server command list is capped in count and length");
        check(boundServerCmds(Arrays.asList("a", "b")).equals(Arrays.asList("a", "b")), "a normal command list is unchanged and keeps its order");
        check(boundServerCmds(null) == null, "a missing list stays missing");
        System.out.println(checks + " checks, " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
with tempfile.TemporaryDirectory(prefix='art-file-') as directory:
    work = pathlib.Path(directory)
    path = work / 'ArtFileRegression.java'
    path.write_text(code, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(path)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'ArtFileRegression']).returncode)
