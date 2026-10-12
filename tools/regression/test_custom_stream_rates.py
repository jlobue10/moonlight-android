#!/usr/bin/env python3
"""Execute the actual custom bitrate/refresh-rate preference listeners on the JVM.

Preference storage, toasts and reload are boundary fakes. The harness models the
EditTextPreference contract: returning true persists the original input after
the listener returns. It does not require an Android SDK. --baseline reads HEAD.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/preferences/StreamSettings.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())


def listener(name):
    start = source.index('{', source.index(name + '.setOnPreferenceChangeListener('))
    end, depth = start, 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


code = r'''
import java.util.*;
public class CustomStreamRates {
    static final class PreferenceConfiguration {
        static final String BITRATE_PREF_STRING="bitrate",CUSTOM_BITRATE_PREF_STRING="customBitrate",
                CUSTOM_REFRESH_RATE_PREF_STRING="customRefreshRate";
    }
    static final class R { static final class string {
        static final int pref_enter_value_0_9999=1,pref_set_success=2,pref_error_occurred=3;
    }}
    static final class TextUtils { static boolean isEmpty(String s) { return s==null || s.isEmpty(); } }
    static int lastToast,reloads,checks,failures;
    static final class Toast {
        static final int LENGTH_SHORT=0;
        final int message; Toast(int value) { message=value; }
        static Toast makeText(Object activity,int value,int duration) { return new Toast(value); }
        void show() { lastToast=message; }
    }
    static final class SharedPreferences {
        final Map<String,Object> values=new HashMap<>();
        final class Editor {
            final Map<String,Object> pending=new HashMap<>();
            Editor putInt(String key,int value) { pending.put(key,value); return this; }
            Editor putString(String key,String value) { pending.put(key,value); return this; }
            void apply() { values.putAll(pending); }
        }
        Editor edit() { return new Editor(); }
    }
    final SharedPreferences prefs=new SharedPreferences();
    SharedPreferences getPrefs() { return prefs; }
    Object getActivity() { return this; }
    int getString(int value) { return value; }
    void editAndReload(String key,String value) { prefs.edit().putString(key,value).apply(); ++reloads; }
    boolean bitrate(Object preference,Object newValue) @@BITRATE@@
    boolean refresh(Object preference,Object newValue) @@REFRESH@@
    void submit(boolean rate,String value) {
        boolean accepted=rate ? refresh(null,value) : bitrate(null,value);
        if(accepted)prefs.edit().putString(rate ? PreferenceConfiguration.CUSTOM_REFRESH_RATE_PREF_STRING :
                PreferenceConfiguration.CUSTOM_BITRATE_PREF_STRING,value).apply();
    }
    void reset() {
        prefs.values.clear(); prefs.values.put("bitrate",20000); prefs.values.put("customBitrate","20");
        prefs.values.put("customRefreshRate","59.94"); lastToast=reloads=0;
    }
    static void check(boolean ok,String name) { ++checks; if(!ok){++failures;System.out.println("FAIL "+name);} }
    void run() {
        for(String value:new String[]{"","abc","1,5","NaN","1e99","-1e99","-1","1e4","99999","-0.1"}) {
            reset(); Map<String,Object> before=new HashMap<>(prefs.values); submit(false,value);
            check(before.equals(prefs.values),"invalid bitrate leaves preferences unchanged: "+value);
            check(lastToast!=0 && lastToast!=R.string.pref_set_success && reloads==0,"invalid bitrate reports error: "+value);
        }
        String[] validBitrate={"0","0.001","20.5","9999"}; int[] expected={0,1,20500,9999000};
        for(int i=0;i<validBitrate.length;i++) {
            reset(); submit(false,validBitrate[i]);
            check(prefs.values.get("bitrate").equals(expected[i]) && prefs.values.get("customBitrate").equals(validBitrate[i]) &&
                    lastToast==R.string.pref_set_success,"valid bitrate converts Mbps to kbps: "+validBitrate[i]);
        }
        for(String value:new String[]{"","abc","59,94","NaN","1e99","-1e99","0","-1","0.0001"}) {
            reset(); Map<String,Object> before=new HashMap<>(prefs.values); submit(true,value);
            check(before.equals(prefs.values),"invalid refresh rate leaves preferences unchanged: "+value);
            check(lastToast!=0 && reloads==0,"invalid refresh rate does not schedule a reload: "+value);
        }
        String[] validRefresh={"59.94","60.000","1e2","59.9999","0.001","144.125"};
        String[] normalized={"59.94","60","100","60","0.001","144.125"};
        for(Locale locale:new Locale[]{Locale.US,Locale.GERMANY}) {
            Locale.setDefault(locale);
            for(int i=0;i<validRefresh.length;i++) {
                reset(); submit(true,validRefresh[i]);
                check(prefs.values.get("customRefreshRate").equals(normalized[i]) && reloads==1,
                        "normalized refresh rate survives framework persistence in "+locale+": "+validRefresh[i]);
            }
        }
        System.out.println(checks+" checks; "+failures+" failures");
        if(failures!=0)System.exit(1);
    }
    public static void main(String[] args) { new CustomStreamRates().run(); }
}
'''.replace('@@BITRATE@@', listener('bitrateEditPref')).replace('@@REFRESH@@', listener('customRefreshRatePref'))
with tempfile.TemporaryDirectory(prefix='custom-stream-rates-') as temp:
    work = Path(temp)
    java = work / 'CustomStreamRates.java'
    java.write_text(code)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work), str(java)], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(work), 'CustomStreamRates']).returncode)
