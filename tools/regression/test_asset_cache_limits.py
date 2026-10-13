#!/usr/bin/env python3
"""Run production asset/cache classes on the JVM with Android decoder boundaries.

Real PNG metadata, temporary files and /dev/full cover dimension limits and cache
I/O failure cleanup. Decoder stubs record allocation requests, not pixel memory;
this does not replace an Android hardware/software bitmap integration test.
--baseline reads production Java from HEAD.
"""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
            if '--baseline' in sys.argv else (ROOT / path).read_text())


def png(width, height):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress((b'\0' + bytes(width * 4)) * height)) + chunk(b'IEND', b''))


files = {
    'android/app/ActivityManager.java': '''package android.app;
public class ActivityManager { public boolean isLowRamDevice() { return false; } }''',
    'android/content/Context.java': '''package android.content;
import java.io.File; public class Context {
 public static final String ACTIVITY_SERVICE="activity"; private final File root;
 public Context(File root) { this.root=root; } public File getCacheDir() { return root; }
 public Object getSystemService(String name) { return new android.app.ActivityManager(); }
}''',
    'android/os/Build.java': '''package android.os; public class Build {
 public static class VERSION { public static int SDK_INT=28; }
 public static class VERSION_CODES { public static final int O=26,P=28; }
}''',
    'android/graphics/Bitmap.java': '''package android.graphics; public class Bitmap {
 public enum Config { RGB_565,HARDWARE } public final int width,height;
 public Bitmap(int w,int h) { width=w;height=h; }
}''',
    'android/graphics/Metadata.java': '''package android.graphics;
import java.io.*; import javax.imageio.*; import javax.imageio.stream.*;
class Metadata {
 static int[] read(File file) throws IOException {
  try(ImageInputStream in=ImageIO.createImageInputStream(file)) {
   ImageReader reader=ImageIO.getImageReaders(in).next();
   try { reader.setInput(in); return new int[]{reader.getWidth(0),reader.getHeight(0)}; }
   finally { reader.dispose(); }
  }
 }
}''',
    'android/graphics/BitmapFactory.java': '''package android.graphics;
import java.io.*; public class BitmapFactory {
 public static class Options { public int outWidth,outHeight,inSampleSize=1;
  public boolean inJustDecodeBounds,inDither; public Bitmap.Config inPreferredConfig; }
 public static Bitmap decodeFile(String path,Options o) {
  try {
   int[] size=Metadata.read(new File(path)); o.outWidth=size[0];o.outHeight=size[1];
   if(o.inJustDecodeBounds)return null;
   return new Bitmap((int)((size[0]+(long)o.inSampleSize-1)/o.inSampleSize),
                     (int)((size[1]+(long)o.inSampleSize-1)/o.inSampleSize));
  } catch(IOException e) { o.outWidth=o.outHeight=-1;return null; }
 }
}''',
    'android/graphics/ImageDecoder.java': '''package android.graphics;
import java.io.*; public class ImageDecoder {
 public static final int MEMORY_POLICY_LOW_RAM=0; int width,height;
 public static class Source { final File file; Source(File f){file=f;} }
 public static class Size { final int[] size; Size(int[] s){size=s;}
  public int getWidth(){return size[0];} public int getHeight(){return size[1];} }
 public static class ImageInfo { final Size size; ImageInfo(int[] s){size=new Size(s);}
  public Size getSize(){return size;} }
 public interface OnHeaderDecodedListener { void onHeaderDecoded(ImageDecoder decoder,ImageInfo info,Source source); }
 public static Source createSource(File file){return new Source(file);}
 public void setTargetSize(int w,int h){width=w;height=h;}
 public void setMemorySizePolicy(int policy){}
 public static Bitmap decodeBitmap(Source source,OnHeaderDecodedListener listener) throws IOException {
  ImageDecoder decoder=new ImageDecoder(); listener.onHeaderDecoded(decoder,new ImageInfo(Metadata.read(source.file)),source);
  return new Bitmap(decoder.width,decoder.height);
 }
}''',
    'com/limelight/LimeLog.java': '''package com.limelight; public class LimeLog {
 public static void info(String text){} public static void warning(String text){} }''',
    'com/limelight/grid/assets/CachedAppAssetLoader.java': '''package com.limelight.grid.assets;
public class CachedAppAssetLoader {
 public static class Computer { public String uuid="pc"; }
 public static class App { public int getAppId(){return 7;} }
 public static class LoaderTuple { public final Computer computer=new Computer(); public final App app=new App(); }
}''',
    'AssetCacheRegression.java': r'''
import android.content.Context;
import android.graphics.*;
import android.os.Build;
import com.limelight.grid.assets.*;
import com.limelight.utils.CacheHelper;
import java.io.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
public class AssetCacheRegression {
 static int checks,failures;
 static void check(boolean ok,String name) { ++checks; if(!ok){++failures;System.out.println("FAIL "+name);} }
 static class ReadFault extends FilterInputStream {
  final FileInputStream file; final boolean failRead,failClose; int reads,closes;
  ReadFault(File path,boolean read,boolean close) throws IOException {
   super(new FileInputStream(path));file=(FileInputStream)in;failRead=read;failClose=close;
  }
  public int read(byte[] b,int off,int len) throws IOException {
   if(failRead && reads++>0)throw new IOException("injected read failure");
   return super.read(b,off,Math.min(len,5));
  }
  public void close() throws IOException { ++closes;super.close();if(failClose)throw new IOException("injected close failure"); }
 }
 static class BorrowedInput extends ByteArrayInputStream {
  boolean closed; BorrowedInput(byte[] data){super(data);}
  public void close() throws IOException{closed=true;super.close();}
 }
 public static void main(String[] args) throws Exception {
  Path root=Paths.get(args[0]);DiskAssetLoader loader=new DiskAssetLoader(new Context(root.toFile()));
  CachedAppAssetLoader.LoaderTuple tuple=new CachedAppAssetLoader.LoaderTuple();
  Path cached=loader.getFile("pc",7).toPath();Files.createDirectories(cached.getParent());
  for(int sdk:new int[]{26,28})for(String name:new String[]{"normal","wide","tall","longwide","longtall"}) {
   Files.copy(root.resolve(name+".png"),cached,StandardCopyOption.REPLACE_EXISTING);Build.VERSION.SDK_INT=sdk;
   ScaledBitmap image=loader.loadBitmapFromCache(tuple,1);
   check(image!=null,"valid PNG accepted: "+sdk+"/"+name);
   if(image!=null) {
    check(image.bitmap.width>0 && image.bitmap.height>0 && image.bitmap.width<=2048 && image.bitmap.height<=2048,
          "bounded positive decode allocation: "+sdk+"/"+name+" got "+image.bitmap.width+"x"+image.bitmap.height);
    check(image.originalWidth>0 && image.originalHeight>0,"original dimensions retained: "+sdk+"/"+name);
    if(name.equals("normal"))check(image.bitmap.width==300 && image.bitmap.height==400,
                                 "normal cover sizing preserved: "+sdk);
   }
  }
  BitmapFactory.Options bounds=new BitmapFactory.Options();
  for(int[] size:new int[][]{{Integer.MAX_VALUE,1},{1,Integer.MAX_VALUE},{4097,1},{1,4097}}) {
   bounds.outWidth=size[0];bounds.outHeight=size[1];int sample=DiskAssetLoader.calculateInSampleSize(bounds,300,400);
   check(sample>0 && (sample&(sample-1))==0 && (size[0]+(long)sample-1)/sample<=2048 &&
         (size[1]+(long)sample-1)/sample<=2048,"legacy sampling handles ceil rounding and int bounds");
  }
  Path text=root.resolve("text");String original="cached app list";Files.write(text,original.getBytes(StandardCharsets.UTF_8));
  for(boolean read:new boolean[]{false,true})for(boolean close:new boolean[]{false,true}) {
   ReadFault in=new ReadFault(text.toFile(),read,close);String value=null;IOException error=null;
   try{value=CacheHelper.readInputStreamToString(in);}catch(IOException e){error=e;}
   check(in.closes==1 && !in.file.getFD().valid(),"reader closes its real descriptor: read="+read+" close="+close);
   check(read ? error!=null && error.getMessage().equals("injected read failure") : error==null && original.equals(value),
         "read result and primary error preserved: read="+read+" close="+close);
   in.file.close();
  }
  byte[] data="valid cached asset bytes".getBytes(StandardCharsets.UTF_8);
  BorrowedInput borrowed=new BorrowedInput(data);loader.populateCacheWithStream(tuple,borrowed);
  check(Arrays.equals(Files.readAllBytes(cached),data),"successful buffered write is flushed and retained");
  check(!borrowed.closed,"writer leaves borrowed network input to its caller");
  loader.populateCacheWithStream(tuple,new InputStream(){public int read()throws IOException{throw new IOException("broken network input");}});
  check(!Files.exists(cached),"input failure removes incomplete cache file");
  // The write is staged next to the target: when the staging file cannot even be created
  // (directory not writable) nothing is committed and the existing entry is not torn.
  Files.write(cached,data);
  java.util.Set<java.nio.file.attribute.PosixFilePermission> perms=Files.getPosixFilePermissions(cached.getParent());
  Files.setPosixFilePermissions(cached.getParent(),java.util.EnumSet.of(
   java.nio.file.attribute.PosixFilePermission.OWNER_READ,java.nio.file.attribute.PosixFilePermission.OWNER_EXECUTE));
  loader.populateCacheWithStream(tuple,new ByteArrayInputStream("replacement".getBytes(StandardCharsets.UTF_8)));
  Files.setPosixFilePermissions(cached.getParent(),perms);
  check(Arrays.equals(Files.readAllBytes(cached),data),"a staging failure leaves the existing entry untouched");
  try(var entries=Files.list(cached.getParent())){check(entries.count()==1,"a staging failure leaves no partial file");}
  Files.deleteIfExists(cached);
  loader.populateCacheWithStream(tuple,new ByteArrayInputStream(new byte[5*1024*1024+1]));
  check(!Files.exists(cached),"oversized download removes incomplete cache file");
  // A32-02: a writer that is still copying never exposes a partial file; the finished file is
  // complete and the directory holds exactly one entry.
  {
   final java.util.concurrent.CountDownLatch gate=new java.util.concurrent.CountDownLatch(1);
   final byte[] whole=Files.readAllBytes(root.resolve("normal.png"));
   InputStream latched=new InputStream(){
    int pos; public int read()throws IOException{
     if(pos==whole.length/2){try{gate.await();}catch(InterruptedException e){throw new IOException(e);}}
     return pos<whole.length?(whole[pos++]&255):-1;}
   };
   Thread writer=new Thread(()->loader.populateCacheWithStream(tuple,latched));
   writer.start();
   long until=System.nanoTime()+2_000_000_000L; while(System.nanoTime()<until && writer.getState()!=Thread.State.WAITING){Thread.sleep(10);}
   check(!Files.exists(cached),"a partially written asset is not visible at the cache path");
   gate.countDown(); writer.join(10000);
   check(Arrays.equals(Files.readAllBytes(cached),whole),"the committed asset is complete");
   try(var entries=Files.list(cached.getParent())){check(entries.count()==1,"no staging file is left behind");}
  }
  // A32-03: a trickling stream is cut off at the read budget and leaves nothing behind.
  {
   final int[] reads={0};
   InputStream slow=new InputStream(){public int read()throws IOException{++reads[0];try{Thread.sleep(200);}catch(InterruptedException e){throw new IOException(e);}return 'x';}};
   Files.deleteIfExists(cached);
   loader.populateCacheWithStream(tuple,slow,1000);
   check(!Files.exists(cached) && reads[0]<=4096*2,"a trickling download is cut off at the time budget");
   try(var entries=Files.list(cached.getParent())){check(entries.count()==0,"a cut-off download leaves no staging file");}
  }
  System.out.println(checks+" asset/cache checks, "+failures+" failures");
  if(failures!=0)System.exit(1);
 }
}''',
}
for name in ['utils/CacheHelper.java', 'grid/assets/DiskAssetLoader.java', 'grid/assets/ScaledBitmap.java']:
    files['com/limelight/' + name] = read('app/src/main/java/com/limelight/' + name)
with tempfile.TemporaryDirectory(prefix='asset-cache-') as directory:
    work = Path(directory)
    for name, contents in files.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    cache = work / 'cache'
    cache.mkdir()
    for name, width, height in [('normal', 600, 800), ('wide', 1024, 1), ('tall', 1, 1024),
                                ('longwide', 32000, 1), ('longtall', 1, 32000)]:
        (cache / (name + '.png')).write_bytes(png(width, height))
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work)] +
                   [str(work / name) for name in files], check=True)
    sys.exit(subprocess.run(['java', '-cp', str(work), 'AssetCacheRegression', str(cache)], timeout=30).returncode)
