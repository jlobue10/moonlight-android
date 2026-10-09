package com.limelight.utils;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import com.limelight.LimeLog;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Locale;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import okhttp3.Call;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;

/**
 * Downloads a {@link DepthModel} file into the app's private storage and verifies it.
 *
 * The file is streamed to a temporary name, its SHA-256 is computed while it streams, and it is
 * renamed into place only when both the size and the digest match what the publishing release
 * states. A mismatch, a short read or a cancel leaves no usable file behind, so
 * {@link DepthModel#isAvailable} can trust the size check alone afterwards.
 *
 * Progress and the final result are delivered on the main thread.
 */
public final class DepthModelDownloader {

    public interface Listener {
        /** bytesRead out of totalBytes (totalBytes may be -1 while unknown). */
        void onProgress(long bytesRead, long totalBytes);

        void onSuccess(DepthModel model);

        /** The failure is final for this attempt; the partially written file has been deleted. */
        void onFailure(DepthModel model, String reason);
    }

    private static final int BUFFER_SIZE = 256 * 1024;
    /** Hard ceiling on what we are willing to write, in case a server answers with something else. */
    private static final long MAX_ACCEPTED_BYTES = 512L * 1024 * 1024;

    private final Context context;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final AtomicBoolean cancelled = new AtomicBoolean(false);
    private volatile Call call;

    public DepthModelDownloader(Context context) {
        this.context = context.getApplicationContext();
    }

    public synchronized void cancel() {
        cancelled.set(true);
        Call c = call;
        if (c != null) {
            c.cancel();
        }
    }

    public void download(DepthModel model, Listener listener) {
        if (model.isBundled()) {
            mainHandler.post(() -> listener.onSuccess(model));
            return;
        }
        Thread t = new Thread(() -> run(model, listener), "DepthModelDownload");
        t.setDaemon(true);
        t.start();
    }

    private void run(DepthModel model, Listener listener) {
        File dir = DepthModel.modelDirectory(context);
        File target = model.localFile(context);
        if (!dir.isDirectory() && !dir.mkdirs()) {
            fail(listener, model, null, "cannot create " + dir);
            return;
        }
        final File temp;
        try {
            // A cancelled attempt may still be unwinding when a retry starts.
            // Give each writer its own file so it cannot corrupt/delete the retry.
            temp = File.createTempFile(model.fileName + ".", ".part", dir);
        } catch (IOException e) {
            fail(listener, model, null, e.getMessage());
            return;
        }
        if (cancelled.get()) {
            fail(listener, model, temp, "cancelled");
            return;
        }

        OkHttpClient client = new OkHttpClient.Builder()
                .connectTimeout(20, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .followRedirects(true)
                .followSslRedirects(true)
                .build();

        Request request = new Request.Builder().url(model.downloadUrl).build();
        call = client.newCall(request);
        if (cancelled.get()) call.cancel();

        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            fail(listener, model, temp, "SHA-256 unavailable");
            return;
        }

        long written = 0;
        try (Response response = call.execute()) {
            if (!response.isSuccessful()) {
                fail(listener, model, temp, "HTTP " + response.code());
                return;
            }
            ResponseBody body = response.body();
            if (body == null) {
                fail(listener, model, temp, "empty response");
                return;
            }
            long total = body.contentLength();
            if (total > 0 && total != model.sizeBytes) {
                fail(listener, model, temp, "server reports " + total + " bytes, expected " + model.sizeBytes);
                return;
            }
            if (total <= 0) {
                total = model.sizeBytes;
            }

            try (InputStream in = body.byteStream(); FileOutputStream out = new FileOutputStream(temp)) {
                byte[] buf = new byte[BUFFER_SIZE];
                long lastReport = 0;
                int n;
                while ((n = in.read(buf)) != -1) {
                    if (cancelled.get()) {
                        fail(listener, model, temp, "cancelled");
                        return;
                    }
                    written += n;
                    if (written > model.sizeBytes || written > MAX_ACCEPTED_BYTES) {
                        fail(listener, model, temp, "file is larger than the published size");
                        return;
                    }
                    out.write(buf, 0, n);
                    digest.update(buf, 0, n);
                    if (written - lastReport >= 1024 * 1024 || written == total) {
                        lastReport = written;
                        final long w = written, t = total;
                        mainHandler.post(() -> listener.onProgress(w, t));
                    }
                }
                out.getFD().sync();
            }
        } catch (IOException e) {
            fail(listener, model, temp, cancelled.get() ? "cancelled" : e.getMessage());
            return;
        }

        if (written != model.sizeBytes) {
            fail(listener, model, temp, "received " + written + " bytes, expected " + model.sizeBytes);
            return;
        }
        String hex = toHex(digest.digest());
        if (!hex.equals(model.sha256)) {
            LimeLog.severe("Depth model " + model.fileName + " SHA-256 mismatch: got " + hex);
            fail(listener, model, temp, "checksum mismatch");
            return;
        }

        synchronized (this) {
            if (cancelled.get()) {
                fail(listener, model, temp, "cancelled");
                return;
            }
            // Both paths are in one private directory; Android's rename replaces
            // atomically. Keep a verified existing model if publication fails.
            if (!temp.renameTo(target)) {
                fail(listener, model, temp, "cannot move the file into place");
                return;
            }
        }
        LimeLog.info("Depth model " + model.fileName + " downloaded and verified (" + written + " bytes)");
        mainHandler.post(() -> listener.onSuccess(model));
    }

    private void fail(Listener listener, DepthModel model, File temp, String reason) {
        if (temp != null && temp.exists() && !temp.delete()) {
            LimeLog.warning("Could not delete " + temp);
        }
        LimeLog.warning("Depth model download of " + model.fileName + " failed: " + reason);
        mainHandler.post(() -> listener.onFailure(model, reason));
    }

    private static String toHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(String.format(Locale.ROOT, "%02x", b));
        }
        return sb.toString();
    }
}
