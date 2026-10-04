package com.limelight.utils;

import android.content.Context;
import android.os.Build;

import com.limelight.BuildConfig;
import com.limelight.LimeLog;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.logging.FileHandler;
import java.util.logging.Formatter;
import java.util.logging.LogRecord;

/**
 * Writes LimeLog output of the current (or last) stream to files/logs/stream.log so it can be
 * shared from the settings screen ("Share stream log"). Headsets rarely have adb at hand; this is
 * how XR, 3D and controller problems get diagnosed. One file, overwritten per stream, 2 MB cap.
 */
public final class StreamLog {
    private static final String DIR = "logs";
    private static final String FILE = "stream.log";
    private static final String LOGCAT_FILE = "logcat.txt";
    private static final int LIMIT_BYTES = 2 * 1024 * 1024;

    private static FileHandler handler;

    private StreamLog() {}

    public static synchronized void start(Context context) {
        stop();
        try {
            File dir = new File(context.getFilesDir(), DIR);
            if (!dir.isDirectory() && !dir.mkdirs()) {
                return;
            }
            handler = new FileHandler(new File(dir, FILE).getAbsolutePath(), LIMIT_BYTES, 1, false);
            handler.setFormatter(new Formatter() {
                private final SimpleDateFormat time = new SimpleDateFormat("HH:mm:ss.SSS", Locale.ROOT);

                @Override
                public String format(LogRecord record) {
                    return time.format(new Date(record.getMillis())) + " " + record.getLevel().getName().charAt(0)
                            + " " + record.getMessage() + "\n";
                }
            });
            LimeLog.addHandler(handler);
            LimeLog.info("Stream log: " + Build.MANUFACTURER + " " + Build.MODEL + ", Android " + Build.VERSION.RELEASE
                    + " (API " + Build.VERSION.SDK_INT + "), app " + BuildConfig.VERSION_NAME);
        } catch (IOException | SecurityException e) {
            handler = null;
        }
    }

    public static synchronized void stop() {
        if (handler != null) {
            LimeLog.removeHandler(handler);
            handler.flush();
            handler.close();
            handler = null;
        }
    }

    public static File file(Context context) {
        return new File(new File(context.getFilesDir(), DIR), FILE);
    }

    /**
     * Dumps this process's logcat (allowed without READ_LOGS for the app's own output since
     * Jelly Bean) to files/logs/logcat.txt; it carries the Jetpack XR and MediaCodec lines that never
     * go through LimeLog. Returns null when logcat is unavailable.
     */
    public static File dumpLogcat(Context context) {
        File dir = new File(context.getFilesDir(), DIR);
        if (!dir.isDirectory() && !dir.mkdirs()) {
            return null;
        }
        File out = new File(dir, LOGCAT_FILE);
        Process process = null;
        try {
            process = new ProcessBuilder("logcat", "-d", "-v", "time", "-t", "4000").redirectErrorStream(true).start();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8));
                 Writer writer = new OutputStreamWriter(new FileOutputStream(out), StandardCharsets.UTF_8)) {
                String line;
                long written = 0;
                while ((line = reader.readLine()) != null && written < LIMIT_BYTES) {
                    writer.write(line);
                    writer.write('\n');
                    written += line.length() + 1;
                }
            }
            return out.length() > 0 ? out : null;
        } catch (IOException | SecurityException e) {
            return null;
        } finally {
            if (process != null) {
                process.destroy();
            }
        }
    }
}
