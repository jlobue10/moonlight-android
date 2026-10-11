#!/usr/bin/env python3
"""Run the production app-list parser (NvHTTP.getAppListByReader) over a scripted pull parser.

Whitespace before the first <App> (a pretty-printed list) and a non-numeric <IDX> used to
throw NoSuchElementException / NumberFormatException out of the poller thread.
"""
import pathlib, subprocess, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
NVHTTP = 'app/src/main/java/com/limelight/nvstream/http/NvHTTP.java'
NVAPP = 'app/src/main/java/com/limelight/nvstream/http/NvApp.java'

def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
            if '--baseline' in sys.argv else (ROOT / path).read_text(encoding='utf-8'))

def block(source, marker):
    start = source.index(marker); end = source.index('{', start); depth = 1
    while depth:
        end += 1; depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]

parser = block(read(NVHTTP), '    public static LinkedList<NvApp> getAppListByReader(')
nvapp = read(NVAPP)
nvapp = nvapp[nvapp.index('public class NvApp'):].replace('public class NvApp', 'static class NvApp')
code = r'''
import java.io.*;
import java.util.*;
public class AppListRegression {
    static class LimeLog { static void warning(String s) {} static void info(String s) {} }
    static class XmlPullParserException extends Exception { XmlPullParserException(String m) { super(m); } }
    static class HostHttpResponseException extends IOException {}
    interface XmlPullParser {
        int START_DOCUMENT = 0, END_DOCUMENT = 1, START_TAG = 2, END_TAG = 3, TEXT = 4;
        void setInput(Reader r); int getEventType(); int next(); String getName(); String getText();
    }
    // A minimal scripted pull parser: tags become START/END events, everything between them TEXT.
    static class ScriptedParser implements XmlPullParser {
        final List<int[]> types = new ArrayList<>(); final List<String> values = new ArrayList<>(); int pos = -1;
        public void setInput(Reader r) {
            StringBuilder sb = new StringBuilder(); int c;
            try { while ((c = r.read()) >= 0) sb.append((char) c); } catch (IOException e) { throw new RuntimeException(e); }
            String xml = sb.toString(); int i = 0;
            while (i < xml.length()) {
                int lt = xml.indexOf('<', i);
                if (lt < 0) break;
                if (lt > i) { types.add(new int[]{TEXT}); values.add(xml.substring(i, lt)); }
                int gt = xml.indexOf('>', lt);
                String tag = xml.substring(lt + 1, gt).trim();
                if (tag.startsWith("/")) { types.add(new int[]{END_TAG}); values.add(tag.substring(1)); }
                else { types.add(new int[]{START_TAG}); values.add(tag.split("\\s+")[0]); }
                i = gt + 1;
            }
        }
        public int getEventType() { return pos < 0 ? START_DOCUMENT : (pos < types.size() ? types.get(pos)[0] : END_DOCUMENT); }
        public int next() { ++pos; return getEventType(); }
        public String getName() { return pos >= 0 && pos < values.size() ? values.get(pos) : null; }
        public String getText() { return getName(); }
    }
    static class XmlPullParserFactory {
        static XmlPullParserFactory newInstance() { return new XmlPullParserFactory(); }
        void setNamespaceAware(boolean b) {}
        XmlPullParser newPullParser() { return new ScriptedParser(); }
    }
    private static void verifyResponseStatus(XmlPullParser xpp) throws HostHttpResponseException, XmlPullParserException {}
''' + nvapp + '\n' + parser + r'''
    static int checks, failures;
    static void check(boolean ok, String message) { ++checks; if (!ok) ++failures; System.out.println((ok ? "PASS " : "FAIL ") + message); }
    static LinkedList<NvApp> parse(String xml) throws Exception { return getAppListByReader(new StringReader(xml)); }
    public static void main(String[] args) throws Exception {
        LinkedList<NvApp> list = parse("<root status_code=\"200\"><App><AppTitle>Steam</AppTitle><IDX>1</IDX><ID>7</ID></App></root>");
        check(list.size() == 1 && list.get(0).getAppName().equals("Steam") && list.get(0).getAppId() == 7, "a compact list parses");
        try {
            list = parse("<root status_code=\"200\">\n  <App>\n    <AppTitle>Steam</AppTitle>\n    <IDX>1</IDX>\n    <ID>7</ID>\n  </App>\n  <App>\n    <AppTitle>Desktop</AppTitle>\n    <ID>8</ID>\n  </App>\n</root>\n");
            check(list.size() == 2 && list.get(1).getAppId() == 8, "a pretty-printed list parses (whitespace before the first App)");
        } catch (RuntimeException e) { check(false, "a pretty-printed list parses: " + e); }
        try {
            list = parse("<root status_code=\"200\"><App><AppTitle>Odd</AppTitle><IDX>abc</IDX><ID>9</ID></App></root>");
            check(list.size() == 1 && list.get(0).getAppId() == 9, "a non-numeric IDX does not abort the list");
        } catch (RuntimeException e) { check(false, "a non-numeric IDX does not abort the list: " + e); }
        System.out.println(checks + " checks, " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
with tempfile.TemporaryDirectory(prefix='applist-parser-') as directory:
    work = pathlib.Path(directory)
    path = work / 'AppListRegression.java'
    path.write_text(code, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(path)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'AppListRegression']).returncode)
