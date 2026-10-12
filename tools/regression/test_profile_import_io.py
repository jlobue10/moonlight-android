#!/usr/bin/env python3
"""Exercise production profile import I/O and activity-result handling on the JVM.

The Android resolver, fragment lifecycle, preference store, JSON-object parser and
toasts are boundary fakes. Input streams model provider failures and oversized
documents; the production FileUriUtils and onActivityResult run unchanged.
--baseline reads HEAD.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
    return (ROOT / path).read_text()


def block(source, marker):
    start = source.index(marker)
    end, depth = source.index('{', start), 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


settings = read('app/src/main/java/com/limelight/preferences/StreamSettings.java')
method = block(settings, '        public void onActivityResult(')
files = {
    'com/limelight/utils/FileUriUtils.java': read('app/src/main/java/com/limelight/utils/FileUriUtils.java'),
    'android/net/Uri.java': 'package android.net; public class Uri {}',
    'android/content/ContentResolver.java': '''package android.content;
import java.io.*; import android.net.Uri;
public class ContentResolver {
 public InputStream stream;
 public InputStream openInputStream(Uri uri) throws IOException { return stream; }
 public OutputStream openOutputStream(Uri uri) { return new ByteArrayOutputStream(); }
}''',
    'android/content/Context.java': '''package android.content;
public class Context {
 public final ContentResolver resolver=new ContentResolver();
 public ContentResolver getContentResolver() { return resolver; }
}''',
    'ProfileImportIo.java': r'''
import android.content.Context;
import android.net.Uri;
import com.limelight.utils.FileUriUtils;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
class Parent {
    public void onActivityResult(int requestCode,int resultCode,ProfileImportIo.Intent data) {}
}
public class ProfileImportIo extends Parent {
    static final int READ_REQUEST_CODE=1001,READ_REQUEST_SPECIAL_CODE=1002;
    static class Activity extends Context {
        static final int RESULT_OK=-1,MODE_PRIVATE=0;
        final SharedPreferences prefs=new SharedPreferences();
        SharedPreferences getSharedPreferences(String name,int mode) { return prefs; }
    }
    static class Intent {
        final Uri uri; Intent(Uri value) { uri=value; } Uri getData() { return uri; }
    }
    static class SharedPreferences {
        final Map<String,String> data=new HashMap<>();
        String getString(String key,String fallback) { return data.getOrDefault(key,fallback); }
        class Editor {
            boolean clear; final Map<String,String> values=new HashMap<>();
            Editor clear() { clear=true; return this; }
            Editor putString(String key,String value) { values.put(key,value); return this; }
            void apply() { if(clear)data.clear(); data.putAll(values); }
        }
        Editor edit() { return new Editor(); }
    }
    // JSON interpretation is outside this I/O regression. The normal-profile
    // branch receives one known valid object whenever it reaches this boundary.
    static class JSONObject {
        JSONObject(String text) {}
        Iterator<String> keys() { return Collections.singleton("key").iterator(); }
        String getString(String key) { return "value"; }
    }
    static class TextUtils { static boolean isEmpty(String s) { return s==null || s.isEmpty(); } }
    static class KeyBoardControllerConfigurationLoader {
        static final String OSC_PREFERENCE="profile",OSC_PREFERENCE_VALUE="keyboard";
    }
    static class GameMenu { static final String PREF_NAME="menu",KEY_NAME="shortcut"; }
    static class R { static class string {
        static final int pref_empty_file=1,pref_import_success=2,pref_error_occurred=3;
    }}
    static String lastToast="";
    static class Toast {
        static final int LENGTH_SHORT=0; final String message;
        Toast(String value) { message=value; }
        static Toast makeText(Object context,String value,int duration) { return new Toast(value); }
        void show() { lastToast=message; }
    }
    Activity activity=new Activity();
    Activity getActivity() { return activity; }
    Activity requireActivity() { return activity; }
    SharedPreferences getPrefs() { return activity.prefs; }
    String getString(int id) { return "message:"+id; }
@@METHOD@@
    static int checks,failures;
    static void check(boolean ok,String name) { ++checks; if(!ok){++failures;System.out.println("FAIL "+name);} }
    static class ProviderStream extends InputStream {
        final byte[] data; final boolean failRead,failClose; int position,closes;
        ProviderStream(String text,boolean read,boolean close) {
            data=text.getBytes(StandardCharsets.UTF_8); failRead=read; failClose=close;
        }
        public int read() throws IOException { byte[] one=new byte[1]; return read(one,0,1)<0 ? -1 : one[0]&255; }
        public int read(byte[] out,int offset,int length) throws IOException {
            if(position==data.length) {
                if(failRead)throw new IOException("provider disconnected");
                return -1;
            }
            int count=Math.min(length,data.length-position);
            System.arraycopy(data,position,out,offset,count); position+=count; return count;
        }
        public void close() throws IOException { ++closes; if(failClose)throw new IOException("provider close failed"); }
    }
    static class LargeStream extends InputStream {
        int reads,closes; final int size;
        LargeStream(int count) { size=count; }
        public int read() { return reads++<size ? 'x' : -1; }
        public int read(byte[] out,int offset,int count) {
            int n=Math.min(count,size-reads); if(n==0)return -1;
            Arrays.fill(out,offset,offset+n,(byte)'x'); reads+=n; return n;
        }
        public void close() { ++closes; }
    }
    void reset() { activity=new Activity(); activity.prefs.data.put("sentinel","saved"); lastToast=""; }
    void run() throws Exception {
        for(int request:new int[]{READ_REQUEST_CODE,READ_REQUEST_SPECIAL_CODE}) {
            for(int result:new int[]{0,Activity.RESULT_OK}) {
                reset(); boolean threw=false;
                try { onActivityResult(request,result,null); } catch(Exception e) { threw=true; }
                check(!threw && activity.prefs.data.size()==1 && lastToast.isEmpty(),"null result is ignored: "+request+"/"+result);
            }
            reset(); onActivityResult(request,Activity.RESULT_OK,new Intent(null));
            check(activity.prefs.data.size()==1 && lastToast.isEmpty(),"missing URI leaves profile unchanged");
            for(int fault=0;fault<3;fault++) {
                reset(); ProviderStream stream=new ProviderStream("{\"key\":\"value\"}\n",fault==0,fault==1);
                activity.resolver.stream=fault==2 ? null : stream;
                onActivityResult(request,Activity.RESULT_OK,new Intent(new Uri()));
                check(activity.prefs.data.size()==1 && "saved".equals(activity.prefs.data.get("sentinel")),
                        "provider failure cannot replace profile: "+request+"/"+fault);
                check(lastToast.startsWith("message:3"),"provider failure reports an import error: "+request+"/"+fault);
                if(fault<2)check(stream.closes>0,"failed stream is closed");
            }
            reset(); ProviderStream good=new ProviderStream("{\"key\":\"value\"}\n",false,false);
            activity.resolver.stream=good; onActivityResult(request,Activity.RESULT_OK,new Intent(new Uri()));
            check(lastToast.equals("message:2") && good.closes>0,"valid profile imports and closes stream: "+request);
        }
        reset(); String unicode="{\"name\":\"ゲーム 🎮\"}";
        ProviderStream good=new ProviderStream(unicode,false,false); activity.resolver.stream=good;
        check(FileUriUtils.openUriForRead(activity,new Uri()).equals(unicode),"UTF-8 profile text survives");
        final int limit=4*1024*1024;
        LargeStream exact=new LargeStream(limit); activity.resolver.stream=exact;
        check(FileUriUtils.openUriForRead(activity,new Uri()).length()==limit && exact.closes>0,"document at size limit is accepted");
        LargeStream large=new LargeStream(limit+1024*1024); activity.resolver.stream=large;
        boolean rejected=false;
        try { FileUriUtils.openUriForRead(activity,new Uri()); } catch(IOException e) { rejected=true; }
        check(rejected && large.reads<=limit+8192 && large.closes>0,"oversized document stops after a bounded read and closes");
        System.out.println(checks+" checks; "+failures+" failures");
        if(failures!=0)System.exit(1);
    }
    public static void main(String[] args) throws Exception { new ProfileImportIo().run(); }
}
'''.replace('@@METHOD@@', method),
}
# Catch Exception also compiles against the old helper, which swallowed I/O errors
# and did not declare IOException. The test still requires an IOException result.
files['ProfileImportIo.java'] = files['ProfileImportIo.java'].replace(
    'catch(IOException e) { rejected=true; }', 'catch(Exception e) { rejected=e instanceof IOException; }')
with tempfile.TemporaryDirectory(prefix='profile-import-') as temp:
    work = Path(temp)
    for name, text in files.items():
        path = work / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work),
                    *[str(work / name) for name in files]], check=True)
    raise SystemExit(subprocess.run(['java', '-Xmx96m', '-cp', str(work), 'ProfileImportIo'], timeout=20).returncode)
