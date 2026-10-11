#!/usr/bin/env python3
"""Run the production cache path builder and the host uuid validator against path-traversal input.

A host-chosen uniqueid names cache files; it must never escape the cache directory, and the
validator must reject anything that is not a plain identifier.
"""
import pathlib, re, subprocess, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
CACHE = 'app/src/main/java/com/limelight/utils/CacheHelper.java'
NVHTTP = 'app/src/main/java/com/limelight/nvstream/http/NvHTTP.java'

def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
            if '--baseline' in sys.argv else (ROOT / path).read_text(encoding='utf-8'))

def block(source, marker):
    start = source.index(marker); end = source.index('{', start); depth = 1
    while depth:
        end += 1; depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]

cache = read(CACHE)
nvhttp = read(NVHTTP)
open_path = block(cache, '    public static File openPath(')
safe = block(cache, '    static String safeComponent(') if 'static String safeComponent(' in cache else ''
validator = (block(nvhttp, '    public static boolean isValidComputerUuid(') if 'isValidComputerUuid(' in nvhttp
             else '    public static boolean isValidComputerUuid(String uuid) { return uuid != null && !uuid.isEmpty(); }')
code = r'''
import java.io.File;
import java.util.*;
public class HostIdentifierRegression {
    static int checks, failures;
    static void check(boolean ok, String message) { ++checks; if (!ok) ++failures; System.out.println((ok ? "PASS " : "FAIL ") + message); }
    static boolean inside(File root, File f) {
        try { return f.getCanonicalPath().startsWith(root.getCanonicalPath() + File.separator); } catch (Exception e) { return false; }
    }
''' + open_path + '\n' + safe + '\n' + validator + r'''
    public static void main(String[] args) throws Exception {
        File root = new File(System.getProperty("java.io.tmpdir"), "moonlight-cache-" + System.nanoTime());
        root.mkdirs();
        String[] rogue = {"../../shared_prefs", "..", ".", "a/b", "a\\b", "", "/etc/passwd"};
        boolean confined = true;
        for (String r : rogue) {
            File f = openPath(false, root, "boxart", r);
            if (!inside(root, f)) { confined = false; System.out.println("escaped: " + r + " -> " + f); }
            File g = openPath(false, root, "applist", r);
            if (!inside(root, g)) { confined = false; System.out.println("escaped: " + r + " -> " + g); }
        }
        check(confined, "every rogue path component stays inside the cache root");
        File good = openPath(false, root, "boxart", "ABCDEF01-2345-6789-abcd-ef0123456789", "7.png");
        check(inside(root, good) && good.getName().equals("7.png") && good.getParentFile().getName().equals("ABCDEF01-2345-6789-abcd-ef0123456789"),
              "a normal uuid and file name map to their own directory and file");
        check(isValidComputerUuid("ABCDEF01-2345-6789-abcd-ef0123456789") && isValidComputerUuid("deadbeef"),
              "GUID-like identifiers are accepted");
        String sixtyFive = new String(new char[65]).replace('\0', 'a');
        check(!isValidComputerUuid("../../shared_prefs") && !isValidComputerUuid("a/b") && !isValidComputerUuid("a b") &&
              !isValidComputerUuid("") && !isValidComputerUuid(null) && !isValidComputerUuid(sixtyFive) && !isValidComputerUuid("x\u0000y"),
              "path separators, spaces, empties, over-long and control characters are rejected");
        System.out.println(checks + " checks, " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
with tempfile.TemporaryDirectory(prefix='host-identifiers-') as directory:
    work = pathlib.Path(directory)
    path = work / 'HostIdentifierRegression.java'
    path.write_text(code, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(path)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'HostIdentifierRegression']).returncode)
