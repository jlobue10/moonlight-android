#!/usr/bin/env python3
"""Exercise the complete CachedAppAssetLoader on the JVM.

Android views, animation dispatch, AsyncTask and storage are boundary fakes.
Animations are deliberately completed after a tile has been rebound. Executor
tests use real JVM workers; only their idle timeout is shortened. --baseline
reads production Java from HEAD. No Android rendering or network I/O is used.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True)
            if '--baseline' in sys.argv else (ROOT / path).read_text())


files = {
    'android/content/Context.java': 'package android.content; public class Context {}',
    'android/content/res/Resources.java': 'package android.content.res; public class Resources {}',
    'android/graphics/Bitmap.java': '''package android.graphics; public class Bitmap {
 public enum Config { ARGB_8888 }
 public static Bitmap createBitmap(int w,int h,Config c) { return new Bitmap(); }
}''',
    'android/graphics/drawable/Drawable.java': 'package android.graphics.drawable; public class Drawable {}',
    'android/graphics/drawable/BitmapDrawable.java': '''package android.graphics.drawable;
import android.graphics.Bitmap; import android.content.res.Resources;
public class BitmapDrawable extends Drawable {
 public final Bitmap bitmap; public BitmapDrawable(Resources res,Bitmap bitmap) { this.bitmap=bitmap; }
}''',
    'android/view/View.java': '''package android.view; public class View {
 public static final int VISIBLE=0,INVISIBLE=4,GONE=8; private int visibility=VISIBLE;
 public void setVisibility(int v) { visibility=v; } public int getVisibility() { return visibility; }
 public android.content.Context getContext() { return new android.content.Context(); }
 public android.content.res.Resources getResources() { return new android.content.res.Resources(); }
}''',
    'android/view/animation/Animation.java': '''package android.view.animation; public class Animation {
 public interface AnimationListener { void onAnimationStart(Animation a); void onAnimationEnd(Animation a); void onAnimationRepeat(Animation a); }
 public final int id; private AnimationListener listener;
 public Animation(int id) { this.id=id; }
 public void setAnimationListener(AnimationListener listener) { this.listener=listener; }
 public void finish() { if(listener!=null)listener.onAnimationEnd(this); }
}''',
    'android/view/animation/AnimationUtils.java': '''package android.view.animation; public class AnimationUtils {
 public static Animation loadAnimation(android.content.Context c,int id) { return new Animation(id); }
}''',
    'android/widget/ImageView.java': '''package android.widget;
import android.graphics.*; import android.graphics.drawable.*; import android.view.animation.*;
public class ImageView extends android.view.View {
 private Drawable drawable; public Animation animation; public int animations;
 public Drawable getDrawable() { return drawable; } public void setImageDrawable(Drawable d) { drawable=d; }
 public void setImageBitmap(Bitmap b) { drawable=new BitmapDrawable(getResources(),b); }
 public void startAnimation(Animation a) { animation=a; ++animations; }
 public Bitmap bitmap() { return drawable instanceof BitmapDrawable ? ((BitmapDrawable)drawable).bitmap : null; }
}''',
    'android/widget/TextView.java': '''package android.widget; public class TextView extends android.view.View {
 public String text; public void setText(String value) { text=value; }
}''',
    'android/os/AsyncTask.java': '''package android.os;
import java.util.concurrent.Executor; public abstract class AsyncTask<P,U,R> {
 private boolean cancelled;
 protected abstract R doInBackground(P... args);
 protected void onPostExecute(R result) {} protected void onProgressUpdate(U... args) {}
 protected void publishProgress(U... args) { onProgressUpdate(args); }
 public boolean isCancelled() { return cancelled; } public boolean cancel(boolean interrupt) { cancelled=true;return true; }
 public AsyncTask<P,U,R> executeOnExecutor(Executor executor,P... args) { return this; }
 public void complete(R result) { onPostExecute(result); }
}''',
    'com/limelight/R.java': '''package com.limelight; public class R {
 public static class anim { public static final int boxart_fadein=1,boxart_fadeout=2; }
}''',
    'com/limelight/nvstream/http/ComputerDetails.java': '''package com.limelight.nvstream.http;
public class ComputerDetails { public String uuid="host"; }''',
    'com/limelight/nvstream/http/NvApp.java': '''package com.limelight.nvstream.http;
public class NvApp { private final int id; public NvApp(int id) { this.id=id; }
 public int getAppId() { return id; } public String getAppName() { return "app-"+id; }
}''',
    'com/limelight/grid/assets/NetworkAssetLoader.java': '''package com.limelight.grid.assets;
public class NetworkAssetLoader { public java.io.InputStream getBitmapStream(CachedAppAssetLoader.LoaderTuple t) { return null; } }''',
    'com/limelight/grid/assets/DiskAssetLoader.java': '''package com.limelight.grid.assets;
public class DiskAssetLoader {
 public boolean checkCacheExists(CachedAppAssetLoader.LoaderTuple t) { return true; }
 public ScaledBitmap loadBitmapFromCache(CachedAppAssetLoader.LoaderTuple t,int scale) { return null; }
 public void populateCacheWithStream(CachedAppAssetLoader.LoaderTuple t,java.io.InputStream in) {}
}''',
    'com/limelight/grid/assets/MemoryAssetLoader.java': '''package com.limelight.grid.assets;
import java.util.*; public class MemoryAssetLoader {
 private final Map<Integer,ScaledBitmap> cache=new HashMap<>();
 public ScaledBitmap loadBitmapFromCache(CachedAppAssetLoader.LoaderTuple t) { return cache.get(t.app.getAppId()); }
 public void populateCache(CachedAppAssetLoader.LoaderTuple t,ScaledBitmap b) { cache.put(t.app.getAppId(),b); }
 public void clearCache() { cache.clear(); }
}''',
    'com/limelight/grid/assets/AssetLoaderLifecycle.java': r'''
package com.limelight.grid.assets;
import android.graphics.Bitmap;
import android.os.AsyncTask;
import android.view.View;
import android.view.animation.Animation;
import android.widget.*;
import com.limelight.R;
import com.limelight.nvstream.http.*;
import java.lang.reflect.Field;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
public class AssetLoaderLifecycle {
 static int checks,failures;
 static void check(boolean ok,String name) { ++checks;if(!ok){++failures;System.out.println("FAIL "+name);} }
 static ScaledBitmap art() { return new ScaledBitmap(300,400,new Bitmap()); }
 @SuppressWarnings("unchecked")
 static AsyncTask<Object,Object,ScaledBitmap> task(ImageView image) {
  return (AsyncTask<Object,Object,ScaledBitmap>)(Object)((CachedAppAssetLoader.AsyncDrawable)image.getDrawable()).getLoaderTask();
 }
 public static void main(String[] args) throws Exception {
  ComputerDetails computer=new ComputerDetails(); MemoryAssetLoader memory=new MemoryAssetLoader();
  CachedAppAssetLoader loader=new CachedAppAssetLoader(computer,1,new NetworkAssetLoader(),memory,new DiskAssetLoader(),new Bitmap());
  ScaledBitmap a=art(),b=art(); NvApp appA=new NvApp(1),appB=new NvApp(2);
  for(String mode:new String[]{"cached","loading","completed","cancelled","replaced"}) {
   memory.clearCache();ImageView image=new ImageView();TextView text=new TextView();
   loader.populateImageView(appA,image,text);AsyncTask<Object,Object,ScaledBitmap> old=task(image);
   // A visible network placeholder takes the asynchronous fade-out path.
   image.setVisibility(View.VISIBLE);old.complete(a);Animation fade=image.animation;
   check(fade.id==R.anim.boxart_fadeout,"visible placeholder fades out: "+mode);
   if(mode.equals("cached")) {
    memory.populateCache(new CachedAppAssetLoader.LoaderTuple(computer,appB),b);
    check(loader.populateImageView(appB,image,text),"replacement comes from memory");
   } else if(mode.equals("loading") || mode.equals("completed")) {
    loader.populateImageView(appB,image,text);
    if(mode.equals("completed"))task(image).complete(b);
   } else if(mode.equals("cancelled")) old.cancel(true);
   else image.setImageBitmap(b.bitmap); // Ownership changes even without AsyncTask cancellation.
   Object drawable=image.getDrawable();int animations=image.animations;
   fade.finish();
   check(image.getDrawable()==drawable,"late fade cannot replace current drawable: "+mode);
   check(image.animations==animations,"late fade cannot start a new animation: "+mode);
   if(mode.equals("cached") || mode.equals("completed"))
    check(image.bitmap()==b.bitmap && text.text.equals("app-2"),"rebound tile retains app B's art and label: "+mode);
  }
  // The ordinary visible-placeholder transition and invisible disk result still work.
  for(boolean visible:new boolean[]{false,true}) {
   memory.clearCache();ImageView image=new ImageView();TextView text=new TextView();
   loader.populateImageView(appA,image,text);AsyncTask<Object,Object,ScaledBitmap> current=task(image);
   if(visible)image.setVisibility(View.VISIBLE);
   current.complete(a);if(visible)image.animation.finish();
   check(image.bitmap()==a.bitmap && image.getVisibility()==View.VISIBLE,"current load displays art: "+visible);
   check(image.animation.id==R.anim.boxart_fadein && text.getVisibility()==View.GONE,"current art fades in without placeholder text: "+visible);
  }
  // Actual JVM core workers must retire while idle and be recreated on demand.
  List<ThreadPoolExecutor> pools=new ArrayList<>();
  try {
   for(String name:new String[]{"cacheExecutor","foregroundExecutor","networkExecutor"}) {
    Field field=CachedAppAssetLoader.class.getDeclaredField(name);field.setAccessible(true);
    ThreadPoolExecutor pool=(ThreadPoolExecutor)field.get(loader);pools.add(pool);
    check(pool.getKeepAliveTime(TimeUnit.SECONDS)>0 && pool.getKeepAliveTime(TimeUnit.SECONDS)<=60,
          "idle retirement has a bounded production timeout: "+name);
    pool.setKeepAliveTime(20,TimeUnit.MILLISECONDS);pool.prestartAllCoreThreads();
    CountDownLatch started=new CountDownLatch(pool.getCorePoolSize()),release=new CountDownLatch(1);
    AtomicInteger done=new AtomicInteger();
    for(int i=0;i<pool.getCorePoolSize();++i)pool.execute(()->{started.countDown();try{release.await();}catch(InterruptedException e){throw new AssertionError(e);}done.incrementAndGet();});
    if(!started.await(2,TimeUnit.SECONDS))throw new AssertionError("workers did not start");
    Future<?> queued=pool.submit(done::incrementAndGet);
    // A busy worker survives the shortened idle timeout and drains queued work.
    Thread.sleep(40);check(done.get()==0,"active work is not interrupted: "+name);
    release.countDown();queued.get(2,TimeUnit.SECONDS);
    long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(2);
    while(pool.getPoolSize()!=0 && System.nanoTime()<deadline)Thread.sleep(5);
    check(done.get()==pool.getCorePoolSize()+1 && pool.getPoolSize()==0,"all idle workers retire after draining work: "+name);
    check(pool.submit(()->42).get(2,TimeUnit.SECONDS)==42,"pool accepts work after idle retirement: "+name);
   }
  } finally { for(ThreadPoolExecutor pool:pools) { pool.shutdownNow();pool.awaitTermination(2,TimeUnit.SECONDS); } }
  System.out.println(checks+" asset-loader lifecycle checks, "+failures+" failures");
  if(failures!=0)System.exit(1);
 }
}''',
}
for name in ['CachedAppAssetLoader.java', 'ScaledBitmap.java']:
    files['com/limelight/grid/assets/' + name] = read('app/src/main/java/com/limelight/grid/assets/' + name)
with tempfile.TemporaryDirectory(prefix='asset-lifecycle-') as directory:
    work = Path(directory)
    for name, contents in files.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(work)] +
                   [str(work / name) for name in files], check=True)
    sys.exit(subprocess.run(['java', '-cp', str(work), 'com.limelight.grid.assets.AssetLoaderLifecycle'], timeout=30).returncode)
