#!/usr/bin/env python3
"""Run the production controller aggregation block with in-memory device contexts.

Android device discovery and transport are outside this fixture. --baseline
reads HEAD so the same cases can verify the unmodified implementation.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/binding/input/ControllerHandler.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True, encoding='utf-8')
          if '--baseline' in sys.argv else (ROOT / PATH).read_text(encoding='utf-8'))

def method(marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]

aggregation = method('    private void sendControllerInputPacket(')
aggregation = aggregation[:aggregation.index('        if (originalContext.mouseEmulationActive)')]
aggregation = aggregation.replace('private void sendControllerInputPacket(', 'private int[] aggregate(')
aggregation += '''
        return new int[]{inputMap, leftTrigger & 255, rightTrigger & 255,
                         leftStickX, leftStickY, rightStickX, rightStickY};
    }
'''
prefix = r'''
import java.util.*;
public class AggregationRegression {
    static class GenericControllerContext {
        boolean assignedControllerNumber = true, mouseEmulationActive;
        short controllerNumber;
        int inputMap;
        byte leftTrigger, rightTrigger;
        short leftStickX, leftStickY, rightStickX, rightStickY;
    }
    GenericControllerContext[] inputDeviceContextSnapshot = {};
    Map<Integer, GenericControllerContext> usbDeviceContexts = new LinkedHashMap<>();
    GenericControllerContext defaultContext = new GenericControllerContext();
    void assignControllerNumberIfNeeded(GenericControllerContext context) {}
    static int checks, failures;
    static void check(boolean ok, String message) {
        ++checks;
        if (!ok) ++failures;
        System.out.println((ok ? "PASS " : "FAIL ") + message);
    }
    static GenericControllerContext context(int trigger, int stick, int buttons) {
        GenericControllerContext c = new GenericControllerContext();
        c.leftTrigger = c.rightTrigger = (byte)trigger;
        c.leftStickX = c.leftStickY = c.rightStickX = c.rightStickY = (short)stick;
        c.inputMap = buttons;
        return c;
    }
    static void pair(int route, int aTrigger, int bTrigger, int aStick, int bStick) {
        AggregationRegression r = new AggregationRegression();
        GenericControllerContext a = context(aTrigger, aStick, 1);
        GenericControllerContext b = context(bTrigger, bStick, 2);
        r.defaultContext.controllerNumber = 15;
        if (route == 0) r.inputDeviceContextSnapshot = new GenericControllerContext[]{a, b};
        if (route == 1) {
            r.usbDeviceContexts.put(1, a);
            r.usbDeviceContexts.put(2, b);
        }
        if (route == 2) {
            r.inputDeviceContextSnapshot = new GenericControllerContext[]{a};
            r.defaultContext = b;
        }
        int[] out = r.aggregate(a);
        int trigger = Math.max(aTrigger, bTrigger);
        int stick = Math.abs(aStick) > Math.abs(bStick) ? aStick : bStick;
        check(out[1] == trigger && out[2] == trigger,
              "route " + route + " unsigned trigger max " + aTrigger + ", " + bTrigger);
        check(out[3] == stick && out[4] == stick && out[5] == stick && out[6] == stick,
              "route " + route + " signed stick max " + aStick + ", " + bStick);
        check(out[0] == 3, "route " + route + " button flags remain combined");
    }
'''
suffix = r'''
    public static void main(String[] args) {
        for (int route = 0; route < 3; ++route) {
            pair(route, 64, 128, 2000, 4000);
            pair(route, 128, 255, -1000, 2000);
            pair(route, 255, 128, 2000, -1000);
            pair(route, 0, 0, -32768, 32767);
            pair(route, 0, 255, 0, -5000);
        }
        AggregationRegression r = new AggregationRegression();
        GenericControllerContext a = context(90, 3000, 1);
        GenericControllerContext other = context(255, 32767, 2);
        other.controllerNumber = 1;
        GenericControllerContext unassigned = context(255, 32767, 4);
        unassigned.assignedControllerNumber = false;
        GenericControllerContext mouse = context(255, 32767, 8);
        mouse.mouseEmulationActive = true;
        r.defaultContext.controllerNumber = 15;
        r.inputDeviceContextSnapshot = new GenericControllerContext[]{a, other, unassigned, mouse};
        int[] out = r.aggregate(a);
        check(out[0] == 1 && out[1] == 90 && out[3] == 3000,
              "other players, unassigned devices and mouse-emulation contexts stay excluded");
        System.out.println(checks + " checks, " + failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
'''
with tempfile.TemporaryDirectory(prefix='controller-aggregation-') as directory:
    work = Path(directory)
    code = prefix + method('    private byte maxByMagnitude(')
    code += method('    private short maxByMagnitude(') + aggregation + suffix
    path = work / 'AggregationRegression.java'
    path.write_text(code, encoding='utf-8')
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(path)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'AggregationRegression']).returncode)
