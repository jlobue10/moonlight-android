#!/usr/bin/env python3
"""Text gate for two UI input paths: the custom refresh rate is formatted with Locale.ROOT (a
comma-decimal locale produced "59,94", which the guarded parse silently deleted), the custom
bitrate parse is guarded against NumberFormatException, and Game.displayMessage() never hands a
null text to Toast (which throws on API 30+). --baseline reads HEAD.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
    return (ROOT / path).read_text(encoding='utf-8')


def block(source, marker):
    start = source.index(marker)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


settings = read('app/src/main/java/com/limelight/preferences/StreamSettings.java')
game = read('app/src/main/java/com/limelight/Game.java')
server_helper = read('app/src/main/java/com/limelight/utils/ServerHelper.java')
pcview = read('app/src/main/java/com/limelight/PcView.java')
ui_helper = read('app/src/main/java/com/limelight/utils/UiHelper.java')
bitrate = block(settings, 'bitrateEditPref.setOnPreferenceChangeListener(')
display = block(game, '    public void displayMessage(final String message) {')
stop = block(game, '    private void stopConnection() {')
unpair = block(pcview, '    private void doUnpair(final ComputerDetails computer) {')
toast_safe = block(ui_helper, '    public static void toastSafe(Context context, String text, int duration) {') if 'toastSafe(' in ui_helper else ''
failures = 0
for ok, name in (
    ('text == null || text.isEmpty()' in toast_safe and 'Toast.makeText(context, text, duration).show()' in toast_safe,
     'UiHelper.toastSafe substitutes a text for a null or empty message'),
    ('Toast.makeText(Game.this, e.getMessage()' not in stop and 'UiHelper.toastSafe(Game.this, e.getMessage()' in stop,
     'the quit-on-stop worker never toasts a raw exception message'),
    ('Toast.makeText(parent, toastMessage,' not in server_helper and server_helper.count('UiHelper.toastSafe(parent, toastMessage') == 2,
     'both doQuit overloads toast through toastSafe'),
    ('Toast.makeText(PcView.this, toastMessage,' not in unpair and 'UiHelper.toastSafe(PcView.this, toastMessage' in unpair,
     'the unpair thread toasts through toastSafe'),
    ('String.format(Locale.ROOT, "%.3f", refreshRate)' in settings, 'the custom refresh rate is formatted with Locale.ROOT'),
    ('catch (NumberFormatException' in bitrate and bitrate.index('Float.parseFloat(value)') < bitrate.index('catch (NumberFormatException'),
     'the custom bitrate parse is guarded against NumberFormatException'),
    ('message != null' in display and 'Toast.makeText(Game.this, message,' not in display, 'displayMessage() never hands a null text to Toast'),
):
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok
sys.exit(1 if failures else 0)
