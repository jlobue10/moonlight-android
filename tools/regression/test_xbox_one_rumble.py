#!/usr/bin/env python3
"""Run the production XboxOneController rumble encoder against a recording transport.

GIP motor bytes are 0..127. The short motor values are signed, so an intensity >= 50 % (0x8000)
sign-extended through `>> 9` and reached the controller as 0xC0..0xFF.
"""
import pathlib, subprocess, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/binding/input/driver/XboxOneController.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True, encoding='utf-8')
          if '--baseline' in sys.argv else (ROOT / PATH).read_text(encoding='utf-8'))


def block(marker):
    start = source.index(marker); end = source.index('{', start); depth = 1
    while depth:
        end += 1; depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


methods = '\n'.join(block(m) for m in ('    private void sendRumblePacket()',
                                       '    public void rumble(short lowFreqMotor, short highFreqMotor)',
                                       '    public void rumbleTriggers(short leftTrigger, short rightTrigger)')).replace('@Override', '')
code = r'''
import java.util.*;
public class XboxOneRumbleRegression {
    static class LimeLog { static void warning(String s) {} }
    static class UsbEndpoint {}
    static class UsbDeviceConnection {
        final List<byte[]> sent = new ArrayList<>();
        int bulkTransfer(UsbEndpoint e, byte[] data, int length, int timeout) { sent.add(Arrays.copyOf(data, length)); return length; }
    }
    final UsbDeviceConnection connection = new UsbDeviceConnection();
    final UsbEndpoint outEndpt = new UsbEndpoint();
    byte seqNum;
    short leftTriggerMotor, rightTriggerMotor, lowFreqMotor, highFreqMotor;
    static int checks, failures;
    static void check(boolean ok, String message) { ++checks; if (!ok) ++failures; System.out.println((ok ? "PASS " : "FAIL ") + message); }
''' + methods + r'''
    public static void main(String[] args) {
        XboxOneRumbleRegression r = new XboxOneRumbleRegression();
        r.rumble((short) 0xFFFF, (short) 0x8000);
        r.rumble((short) 0x4000, (short) 0x0200);
        r.rumbleTriggers((short) 0xC000, (short) 0x7FFF);
        byte[] full = r.connection.sent.get(0), half = r.connection.sent.get(1), triggers = r.connection.sent.get(2);
        check((full[8] & 0xFF) == 0x7F && (full[9] & 0xFF) == 0x40, "full and half intensities encode as 0x7F and 0x40 (not sign-extended)");
        check((half[8] & 0xFF) == 0x20 && (half[9] & 0xFF) == 0x01, "a quarter intensity encodes as 0x20 and the smallest step as 0x01");
        check((triggers[6] & 0xFF) == 0x60 && (triggers[7] & 0xFF) == 0x3F, "trigger motors are masked the same way (0x60, 0x3F)");
        boolean inRange = true;
        for (byte[] packet : r.connection.sent) for (int i = 6; i < 10; i++) inRange &= (packet[i] & 0xFF) <= 0x7F;
        check(inRange, "every motor byte stays within GIP's 0..127");
        check(full[0] == 0x09 && full[3] == 0x09 && full[5] == 0x0F && (full[10] & 0xFF) == 0xFF && full[12] == (byte) 0xFF, "the GIP rumble frame layout is unchanged");
        check(full[2] == 0 && half[2] == 1 && triggers[2] == 2, "the sequence number increments per packet");
        System.out.println(checks + " checks, " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
with tempfile.TemporaryDirectory(prefix='xbox-one-rumble-') as directory:
    work = pathlib.Path(directory)
    path = work / 'XboxOneRumbleRegression.java'
    path.write_text(code, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(path)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'XboxOneRumbleRegression']).returncode)
