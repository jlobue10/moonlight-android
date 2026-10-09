#!/usr/bin/env python3
"""Run the real downloader against two controlled concurrent HTTP responses."""
import pathlib,subprocess,tempfile,sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
path='app/src/main/java/com/limelight/utils/DepthModelDownloader.java'
source=subprocess.check_output(['git','show','HEAD:'+path],cwd=ROOT,text=True) if '--baseline' in sys.argv else (ROOT/path).read_text()
STUBS={
'android/content/Context.java': 'package android.content; import java.io.*; public class Context { public File dir; public Context(File d){dir=d;} public Context getApplicationContext(){return this;} }',
'android/os/Looper.java':'package android.os; public class Looper { public static Looper getMainLooper(){return new Looper();}}',
'android/os/Handler.java':'package android.os; public class Handler { public Handler(Looper l){} public void post(Runnable r){r.run();}}',
'com/limelight/LimeLog.java':'package com.limelight;public class LimeLog {public static void severe(String s){}public static void info(String s){}public static void warning(String s){}}',
'com/limelight/utils/DepthModel.java':\
'''package com.limelight.utils;import java.io.*;import android.content.Context;
public class DepthModel {public String fileName="model.bin",downloadUrl="https://example.invalid/model",sha256="63c1dd951ffedf6f7fd968ad4efa39b8ed584f162f46e715114ee184f8de9201";public long sizeBytes=4;
public boolean isBundled(){return false;}public static File modelDirectory(Context c){return c.dir;}
public File localFile(Context c){return new File(c.dir,fileName);}}''',
'okhttp3/Request.java':'package okhttp3;public class Request {public static class Builder{public Builder url(String u){return this;}public Request build(){return new Request();}}}',
'okhttp3/OkHttpClient.java':\
'''package okhttp3;import java.util.concurrent.*;public class OkHttpClient {
public Call newCall(Request r){return new Call();}public static class Builder {
public Builder connectTimeout(long n,TimeUnit t){return this;}public Builder readTimeout(long n,TimeUnit t){return this;}
public Builder followRedirects(boolean b){return this;}public Builder followSslRedirects(boolean b){return this;}public OkHttpClient build(){return new OkHttpClient();}}}''',
'okhttp3/Call.java':\
'''package okhttp3;import java.util.concurrent.*;import java.util.concurrent.atomic.*;import java.io.*;
public class Call {public static AtomicInteger ids=new AtomicInteger();public final int id=ids.incrementAndGet();public volatile boolean cancelled;
public static CountDownLatch firstAtEof=new CountDownLatch(1),secondReading=new CountDownLatch(1),releaseFirst=new CountDownLatch(1),releaseSecond=new CountDownLatch(1);
public void cancel(){cancelled=true;}public Response execute()throws IOException{return new Response(this);}
public static void waitFor(CountDownLatch l)throws IOException {try{if(!l.await(3,TimeUnit.SECONDS))throw new IOException("test timeout");}catch(InterruptedException e){throw new IOException(e);}}}''',
'okhttp3/Response.java':\
'''package okhttp3;public class Response implements AutoCloseable {Call call;Response(Call c){call=c;}public boolean isSuccessful(){return true;}public int code(){return 200;}public ResponseBody body(){return new ResponseBody(call);}public void close(){}}''',
'okhttp3/ResponseBody.java':\
'''package okhttp3;import java.io.*;public class ResponseBody {Call call;ResponseBody(Call c){call=c;}public long contentLength(){return 4;}
public InputStream byteStream(){return new InputStream(){boolean sent;
public int read(){throw new UnsupportedOperationException();}
public int read(byte[] b)throws IOException {
 if(!sent){if(call.id==2){Call.secondReading.countDown();Call.waitFor(Call.releaseSecond);}sent=true;for(int i=0;i<4;i++)b[i]=65;return 4;}
 if(call.id==1){Call.firstAtEof.countDown();Call.waitFor(Call.releaseFirst);if(call.cancelled)throw new IOException("cancelled");}return -1;}};}}''',
'DownloadTest.java':\
'''import java.io.*;import java.nio.file.*;import java.util.concurrent.*;import com.limelight.utils.*;import android.content.*;import okhttp3.Call;
public class DownloadTest {static class Result implements DepthModelDownloader.Listener {CountDownLatch done=new CountDownLatch(1);boolean success;
public void onProgress(long r,long t){}public void onSuccess(DepthModel m){success=true;done.countDown();}public void onFailure(DepthModel m,String r){done.countDown();}}
public static void main(String[] args)throws Exception {Context c=new Context(new File(args[0]));DepthModel m=new DepthModel();Result first=new Result(),second=new Result();
DepthModelDownloader a=new DepthModelDownloader(c),b=new DepthModelDownloader(c);a.download(m,first);Call.waitFor(Call.firstAtEof);
b.download(m,second);Call.waitFor(Call.secondReading);a.cancel();Call.releaseFirst.countDown();Call.waitFor(first.done);
Call.releaseSecond.countDown();Call.waitFor(second.done);
boolean ok=!first.success && second.success && java.util.Arrays.equals(Files.readAllBytes(m.localFile(c).toPath()),new byte[]{65,65,65,65});
System.out.println((ok?"PASS ":"FAIL ")+"cancelled download cannot delete or corrupt a concurrent retry");if(!ok)System.exit(1);}}'''
}
STUBS['com/limelight/utils/DepthModelDownloader.java']=source
with tempfile.TemporaryDirectory(prefix='depth-download-') as directory:
 w=pathlib.Path(directory);(w/'models').mkdir()
 for name,code in STUBS.items():
  p=w/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(code)
 subprocess.run(['java','com.sun.tools.javac.Main','-d',str(w/'classes'),*map(str,w.rglob('*.java'))],check=True)
 raise SystemExit(subprocess.run(['java','-cp',str(w/'classes'),'DownloadTest',str(w/'models')]).returncode)
