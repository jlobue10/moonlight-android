#!/usr/bin/env python3
"""Run the production ProConController.rumble() encoder against a recording transport.

The low motor's frequency byte must come from the top nibble of the amplitude.
"""
import pathlib, subprocess, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/binding/input/driver/ProConController.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True, encoding='utf-8')
          if '--baseline' in sys.argv else (ROOT / PATH).read_text(encoding='utf-8'))

def block(marker):
    start = source.index(marker); end = source.index('{', start); depth = 1
    while depth:
        end += 1; depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]

rumble = block('    public void rumble(short lowFreqMotor, short highFreqMotor)')
code = r'''
import java.util.*;
public class ProConRumbleRegression {
    byte sendPacketCount;
    final List<byte[]> sent = new ArrayList<>();
    void sendData(byte[] data, int length) { sent.add(Arrays.copyOf(data, length)); }
    static int checks, failures;
    static void check(boolean ok, String message) { ++checks; if (!ok) ++failures; System.out.println((ok ? "PASS " : "FAIL ") + message); }
''' + rumble + r'''
    public static void main(String[] args) {
        ProConRumbleRegression r = new ProConRumbleRegression();
        r.rumble((short) 0xF000, (short) 0);
        r.rumble((short) 0x000F, (short) 0);
        r.rumble((short) 0x8000, (short) 0);
        byte full = r.sent.get(0)[4], tiny = r.sent.get(1)[4], half = r.sent.get(2)[4];
        check(full == (byte) 0x41, "a full low-motor amplitude lowers the frequency byte by its top nibble (0x41)");
        check(tiny == (byte) 0x50, "a near-zero low-motor amplitude leaves the frequency byte at 0x50");
        check(half == (byte) 0x48, "a half amplitude lands in between (0x48)");
        check(r.sent.get(0)[8] == full && r.sent.get(0)[0] == 0x10, "both low-motor slots carry the byte in a 0x10 rumble command");
        check(r.sent.get(0)[1] == 0 && r.sent.get(1)[1] == 1 && r.sent.get(2)[1] == 2, "the packet counter increments per call");
        System.out.println(checks + " checks, " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
with tempfile.TemporaryDirectory(prefix='procon-rumble-') as directory:
    work = pathlib.Path(directory)
    path = work / 'ProConRumbleRegression.java'
    path.write_text(code, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(path)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'ProConRumbleRegression']).returncode)
