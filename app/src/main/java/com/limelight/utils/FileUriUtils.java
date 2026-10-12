package com.limelight.utils;

import android.content.Context;
import android.net.Uri;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

/**
 * Description
 * Date: 2024-03-20
 * Time: 13:46
 */
public class FileUriUtils {
    // Keyboard and shortcut profiles are small JSON documents. Bound provider
    // input before buffering it so selecting a large file cannot exhaust memory.
    private static final int MAX_IMPORT_BYTES = 4 * 1024 * 1024;

    public static String openUriForRead(Context context, Uri uri) throws IOException {
        if (uri == null) {
            return "";
        }
        try (InputStream inputStream = context.getContentResolver().openInputStream(uri)) {
            if (inputStream == null) {
                throw new IOException("Unable to open the selected profile");
            }
            ByteArrayOutputStream result = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int count;
            while ((count = inputStream.read(buffer)) != -1) {
                if (count > MAX_IMPORT_BYTES - result.size()) {
                    throw new IOException("Profile exceeds the 4 MiB import limit");
                }
                result.write(buffer, 0, count);
            }
            // Propagate read/close failures to the import handler; never commit a
            // partial document or report a provider failure as an empty file.
            return new String(result.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    public static boolean openUriForWrite(Context context, Uri uri, String content) {
        if (uri == null) {
            return false;
        }

        try {
            //从uri构造输出流
            OutputStream outputStream = context.getContentResolver().openOutputStream(uri);
            //写入文件
            outputStream.write(content.getBytes());
            outputStream.flush();
            outputStream.close();
            return true;
        } catch (Exception e) {
            e.getLocalizedMessage();
        }
        return false;
    }

    public static boolean writerFileString(File file, String content) {
        FileOutputStream fileOutputStream = null;
        try {
            fileOutputStream = new FileOutputStream(file);
            fileOutputStream.write(content.getBytes());
        } catch (Exception e) {
            e.printStackTrace();
            return false;
        } finally {
            if (fileOutputStream != null) {
                try {
                    fileOutputStream.close();
                } catch (IOException e) {
                    e.printStackTrace();
                }
            }
        }
        return true;
    }
}
