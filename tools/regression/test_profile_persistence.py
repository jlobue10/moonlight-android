#!/usr/bin/env python3
"""Exercise the real ProfilesManager, Gson and JVM filesystem.

Android context/preferences are boundary fakes. A child process with a file-size
limit models a failed write without changing the parent's limits. Gson matches
the app dependency; AUDIT_GSON_JAR supplies an offline copy, otherwise download
the pinned Maven artifact into the temporary test directory and verify its hash.
Linux/JDK 17+. --baseline reads HEAD. Native Android filesystem behavior remains
a device gate.
"""
from pathlib import Path
import hashlib
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
GSON_SHA256 = '94855942d4992f112946d3de1c334e709237b8126d8130bf07807c018a4a2120'
if 'com.google.code.gson:gson:2.13.1' not in (ROOT / 'app/build.gradle').read_text():
    raise SystemExit('Update the regression Gson version/hash to match the app dependency')


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
    return (ROOT / path).read_text()


files = {
    'android/content/Context.java': '''package android.content;
import java.io.File;
public class Context {
 private final File dir;
 public Context(File dir) { this.dir=dir; }
 public Context getApplicationContext() { return this; }
 public File getFilesDir() { return dir; }
}''',
    'android/content/SharedPreferences.java': '''package android.content;
import java.util.*;
public interface SharedPreferences {
 interface Editor {} interface OnSharedPreferenceChangeListener {}
 Map<String,?> getAll(); String getString(String k,String d); int getInt(String k,int d);
 long getLong(String k,long d); float getFloat(String k,float d); boolean getBoolean(String k,boolean d);
 Set<String> getStringSet(String k,Set<String> d); boolean contains(String k); Editor edit();
 void registerOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener l);
 void unregisterOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener l);
}''',
    'android/widget/Toast.java': 'package android.widget; public class Toast {}',
    'androidx/annotation/NonNull.java': 'package androidx.annotation; public @interface NonNull {}',
    'androidx/preference/PreferenceManager.java': '''package androidx.preference;
public class PreferenceManager {
 public static android.content.SharedPreferences getDefaultSharedPreferences(android.content.Context c) { return null; }
}''',
    'com/limelight/LimeLog.java': '''package com.limelight;
public class LimeLog { public static void info(String s) {} public static void warning(String s) {} }''',
    'com/limelight/preferences/PreferenceConfiguration.java': 'package com.limelight.preferences; public class PreferenceConfiguration {}',
    'com/limelight/profiles/ProfilePersistenceRegression.java': r'''
package com.limelight.profiles;
import android.content.Context;
import com.google.gson.Gson;
import java.nio.file.*;
import java.util.*;
public class ProfilePersistenceRegression {
 static int checks,failures;
 static void check(boolean ok,String label) { ++checks; if(!ok){++failures;System.out.println("FAIL "+label);} }
 static ProfilesManager fresh(Context context) {
  ProfilesManager.instance=null;
  ProfilesManager manager=ProfilesManager.getInstance();
  check(manager.load(context),"profile file loads");
  return manager;
 }
 public static void main(String[] args) throws Exception {
  Path root=Path.of(args[1]); Context context=new Context(root.toFile());
  Path file=root.resolve("profiles/profiles.json");
  if(args[0].equals("limited")) {
   byte[] original=Files.readAllBytes(file);
   ProfilesManager manager=fresh(context);
   manager.getProfiles().get(0).setName("x".repeat(16384));
   check(!manager.save(context),"filesystem write failure is reported");
   check(Arrays.equals(original,Files.readAllBytes(file)),"filesystem write failure preserves the previous file byte for byte");
   try(var entries=Files.list(file.getParent())) {
    check(entries.count()==1,"failed save removes its temporary file");
   }
  } else {
   ProfilesManager manager=fresh(context);
   UUID id=UUID.randomUUID();
   SettingsProfile profile=new SettingsProfile(id,"Saved ü",1,2,new LinkedHashMap<>(Map.of("bitrate",42)));
   manager.add(profile); manager.setActive(id);
   check(manager.save(context),"ordinary save succeeds");
   byte[] original=Files.readAllBytes(file);
   manager=fresh(context);
   check(manager.getProfiles().size()==1 && manager.getActive()!=null && manager.getActive().getName().equals("Saved ü"),
         "UTF-8 profile and active selection survive a fresh load");
   manager.getActive().getOptions().put("bad",Double.NaN);
   check(!manager.save(context),"Gson serialization failure is reported");
   check(Arrays.equals(original,Files.readAllBytes(file)),"serialization failure preserves every saved profile");
   manager.getActive().getOptions().remove("bad");
   try(var entries=Files.list(file.getParent())) {
    check(entries.count()==1,"serialization failure cleans up temporary state");
   }
   // A valid prefix followed by an invalid entry must not replace the live map.
   String other=new Gson().toJson(new SettingsProfile(UUID.randomUUID(),"Other",3,4,Map.of()));
   for(String document:new String[]{
      "{\"profiles\":["+other+",null]}",
      "{\"profiles\":["+other+",{\"name\":\"Missing UUID\"}]}",
      "{\"profiles\":null}", "null", "{}", "{truncated"
   }) {
    Files.write(file,original);
    manager=fresh(context);
    Files.writeString(file,document);
    check(!manager.load(context),"invalid profile document is rejected: "+document);
    check(manager.getProfiles().size()==1 && manager.getProfiles().get(0).getUuid().equals(id) && manager.getActive()!=null,
          "failed load preserves the complete live profile map and active selection");
   }
   Files.write(file,original);
   check(manager.load(context),"valid reload recovers after failed loads");
   manager.getActive().setName("Updated");
   check(manager.save(context),"save can replace an existing file after recovery");
   manager=fresh(context);
   check(manager.getActive().getName().equals("Updated"),"replacement is visible to a fresh load");
   // A nonempty directory cannot be replaced with the staged file.
   Files.delete(file); Files.createDirectory(file);
   Path sentinel=file.resolve("keep"); Files.writeString(sentinel,"keep");
   check(!manager.save(context),"failed rename is reported");
   check(Files.readString(sentinel).equals("keep"),"failed rename leaves the target intact");
   try(var entries=Files.list(file.getParent())) {
    check(entries.count()==1,"failed rename cleans up its temporary file");
   }
   Files.delete(sentinel); Files.delete(file);
   check(manager.save(context),"save succeeds after the rename obstruction is removed");
   Files.writeString(file,"{\"profiles\":[]}");
   check(manager.load(context) && manager.getProfiles().isEmpty() && manager.getActive()==null,
         "a valid empty profile collection replaces the previous snapshot");
  }
  System.out.println(checks+" checks; "+failures+" failures");
  if(failures!=0)System.exit(1);
 }
}'''
}
for name in ['ProfilesManager', 'SettingsProfile']:
    path = 'com/limelight/profiles/' + name + '.java'
    files[path] = read('app/src/main/java/' + path)


def limit_output_files():
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (512, 512))


with tempfile.TemporaryDirectory() as temp:
    work = Path(temp)
    jar = Path(os.environ['AUDIT_GSON_JAR']) if 'AUDIT_GSON_JAR' in os.environ else work / 'gson.jar'
    if 'AUDIT_GSON_JAR' not in os.environ:
        with urllib.request.urlopen('https://repo.maven.apache.org/maven2/com/google/code/gson/gson/2.13.1/gson-2.13.1.jar', timeout=30) as response:
            jar.write_bytes(response.read())
    if hashlib.sha256(jar.read_bytes()).hexdigest() != GSON_SHA256:
        raise SystemExit('Gson artifact does not match the pinned SHA-256')
    for name, content in files.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    classes = work / 'classes'
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-cp', str(jar), '-d', str(classes),
                    *[str(work / name) for name in files]], check=True)
    command = ['java', '-cp', os.pathsep.join([str(classes), str(jar)]),
               'com.limelight.profiles.ProfilePersistenceRegression']
    ordinary = work / 'ordinary'
    ordinary.mkdir()
    normal = subprocess.run(command + ['ordinary', str(ordinary)], capture_output=True, text=True, timeout=30)
    print(normal.stdout, end='')
    if normal.returncode:
        print(normal.stderr, end='', file=sys.stderr)
    limited = work / 'limited'
    (limited / 'profiles').mkdir(parents=True)
    (limited / 'profiles/profiles.json').write_text(json.dumps({'profiles': [{
        'uuid': '00000000-0000-0000-0000-000000000001', 'name': 'Saved',
        'createdUtc': 1, 'modifiedUtc': 2, 'options': {}}]}))
    failed_write = subprocess.run(command + ['limited', str(limited)], preexec_fn=limit_output_files,
                                  capture_output=True, text=True, timeout=30)
    print(failed_write.stdout, end='')
    if failed_write.returncode:
        print(failed_write.stderr, end='', file=sys.stderr)
    raise SystemExit(normal.returncode != 0 or failed_write.returncode != 0)
