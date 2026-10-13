package com.limelight.grid.assets;

import android.app.ActivityManager;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.ImageDecoder;
import android.os.Build;

import com.limelight.LimeLog;
import com.limelight.utils.CacheHelper;

import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

public class DiskAssetLoader {
    // 5 MB
    private static final long MAX_ASSET_SIZE = 5 * 1024 * 1024;

    // The compressed-file limit does not bound bitmap memory or texture size.
    // Keep ordinary cover scaling, but cap either axis for extreme aspect ratios.
    private static final int MAX_DECODED_DIMENSION = 2048;

    // The per-read socket timeout bounds silence only; the whole download gets this budget.
    private static final long ASSET_READ_BUDGET_MS = 30000;

    // Standard box art is 300x400
    private static final int STANDARD_ASSET_WIDTH = 300;
    private static final int STANDARD_ASSET_HEIGHT = 400;

    private final boolean isLowRamDevice;
    private final File cacheDir;

    public DiskAssetLoader(Context context) {
        this.cacheDir = context.getCacheDir();
        this.isLowRamDevice =
                ((ActivityManager) context.getSystemService(Context.ACTIVITY_SERVICE)).isLowRamDevice();
    }

    public boolean checkCacheExists(CachedAppAssetLoader.LoaderTuple tuple) {
        return CacheHelper.cacheFileExists(cacheDir, "boxart", tuple.computer.uuid, tuple.app.getAppId() + ".png");
    }

    // https://developer.android.com/topic/performance/graphics/load-bitmap.html
    public static int calculateInSampleSize(BitmapFactory.Options options, int reqWidth, int reqHeight) {
        // Raw height and width of image
        final int height = options.outHeight;
        final int width = options.outWidth;
        int inSampleSize = 1;

        if (height > reqHeight || width > reqWidth) {

            final int halfHeight = height / 2;
            final int halfWidth = width / 2;

            // Calculates the largest inSampleSize value that is a power of 2 and keeps both
            // height and width larger than the requested height and width.
            while ((halfHeight / inSampleSize) >= reqHeight && (halfWidth / inSampleSize) >= reqWidth) {
                inSampleSize *= 2;
            }
        }

        // The cover-fit loop above stops when either axis reaches its requested
        // size. A very thin image can therefore retain an enormous other axis.
        // Use ceiling division and long arithmetic to cover decoder rounding.
        while ((width + (long) inSampleSize - 1) / inSampleSize > MAX_DECODED_DIMENSION ||
                (height + (long) inSampleSize - 1) / inSampleSize > MAX_DECODED_DIMENSION) {
            inSampleSize *= 2;
        }

        return inSampleSize;
    }

    public ScaledBitmap loadBitmapFromCache(CachedAppAssetLoader.LoaderTuple tuple, int sampleSize) {
        File file = getFile(tuple.computer.uuid, tuple.app.getAppId());

        // Don't bother with anything if it doesn't exist
        if (!file.exists()) {
            return null;
        }

        // Make sure the cached asset doesn't exceed the maximum size
        if (file.length() > MAX_ASSET_SIZE) {
            LimeLog.warning("Removing cached tuple exceeding size threshold: "+tuple);
            file.delete();
            return null;
        }

        Bitmap bmp;

        // For OSes prior to P, we have to use the ugly BitmapFactory API
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) {
            // Lookup bounds of the downloaded image
            BitmapFactory.Options decodeOnlyOptions = new BitmapFactory.Options();
            decodeOnlyOptions.inJustDecodeBounds = true;
            BitmapFactory.decodeFile(file.getAbsolutePath(), decodeOnlyOptions);
            if (decodeOnlyOptions.outWidth <= 0 || decodeOnlyOptions.outHeight <= 0) {
                // Dimensions set to -1 on error. Return value always null.
                return null;
            }

            LimeLog.info("Tuple "+tuple+" has cached art of size: "+decodeOnlyOptions.outWidth+"x"+decodeOnlyOptions.outHeight);

            // Load the image scaled to the appropriate size
            BitmapFactory.Options options = new BitmapFactory.Options();
            options.inSampleSize = calculateInSampleSize(decodeOnlyOptions,
                    STANDARD_ASSET_WIDTH / sampleSize,
                    STANDARD_ASSET_HEIGHT / sampleSize);
            if (isLowRamDevice) {
                options.inPreferredConfig = Bitmap.Config.RGB_565;
                options.inDither = true;
            }
            else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                options.inPreferredConfig = Bitmap.Config.HARDWARE;
            }

            bmp = BitmapFactory.decodeFile(file.getAbsolutePath(), options);
            if (bmp != null) {
                LimeLog.info("Tuple "+tuple+" decoded from disk cache with sample size: "+options.inSampleSize);
                return new ScaledBitmap(decodeOnlyOptions.outWidth, decodeOnlyOptions.outHeight, bmp);
            }
        }
        else {
            // On P, we can get a bitmap back in one step with ImageDecoder
            final ScaledBitmap scaledBitmap = new ScaledBitmap();
            try {
                scaledBitmap.bitmap = ImageDecoder.decodeBitmap(ImageDecoder.createSource(file), new ImageDecoder.OnHeaderDecodedListener() {
                    @Override
                    public void onHeaderDecoded(ImageDecoder imageDecoder, ImageDecoder.ImageInfo imageInfo, ImageDecoder.Source source) {
                        scaledBitmap.originalWidth = imageInfo.getSize().getWidth();
                        scaledBitmap.originalHeight = imageInfo.getSize().getHeight();

                        double scale = Math.max((double) STANDARD_ASSET_WIDTH / scaledBitmap.originalWidth,
                                (double) STANDARD_ASSET_HEIGHT / scaledBitmap.originalHeight);
                        scale = Math.min(scale, (double) MAX_DECODED_DIMENSION /
                                Math.max(scaledBitmap.originalWidth, scaledBitmap.originalHeight));
                        int targetWidth = Math.max(1, (int) Math.round(scaledBitmap.originalWidth * scale));
                        int targetHeight = Math.max(1, (int) Math.round(scaledBitmap.originalHeight * scale));
                        imageDecoder.setTargetSize(targetWidth, targetHeight);
                        if (isLowRamDevice) {
                            imageDecoder.setMemorySizePolicy(ImageDecoder.MEMORY_POLICY_LOW_RAM);
                        }
                    }
                });
                return scaledBitmap;
            } catch (IOException e) {
                e.printStackTrace();
                return null;
            }
        }

        return null;
    }

    public File getFile(String computerUuid, int appId) {
        return CacheHelper.openPath(false, cacheDir, "boxart", computerUuid, appId + ".png");
    }

    public void deleteAssetsForComputer(String computerUuid) {
        File dir = CacheHelper.openPath(false, cacheDir, "boxart", computerUuid);
        File[] files = dir.listFiles();
        if (files != null) {
            for (File f : files) {
                f.delete();
            }
        }
    }

    public void populateCacheWithStream(CachedAppAssetLoader.LoaderTuple tuple, InputStream input) {
        populateCacheWithStream(tuple, input, ASSET_READ_BUDGET_MS);
    }

    public void populateCacheWithStream(CachedAppAssetLoader.LoaderTuple tuple, InputStream input, long budgetMs) {
        // Staged and renamed: a concurrent loader for the same asset, or a reader, never sees
        // a partially written file. Buffered output can fail only when close flushes its
        // final bytes, so success is decided after the stream closed.
        boolean success = false;
        File staging = null;
        try {
            staging = CacheHelper.openCacheFileForStaging(cacheDir, "boxart", tuple.computer.uuid, tuple.app.getAppId() + ".png");
            try (final OutputStream out = new BufferedOutputStream(new FileOutputStream(staging))) {
                CacheHelper.writeInputStreamToOutputStream(input, out, MAX_ASSET_SIZE,
                        System.nanoTime() + budgetMs * 1_000_000L);
            }
            CacheHelper.commitStagedCacheFile(staging, cacheDir, "boxart", tuple.computer.uuid, tuple.app.getAppId() + ".png");
            success = true;
        } catch (IOException e) {
            e.printStackTrace();
        } finally {
            if (!success) {
                LimeLog.warning("Unable to populate cache with tuple: "+tuple);
                if (staging != null) {
                    staging.delete();
                }
            }
        }
    }
}
