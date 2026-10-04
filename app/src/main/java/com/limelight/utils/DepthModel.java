package com.limelight.utils;

import android.content.Context;

import java.io.File;

/**
 * The monocular depth models the AI SBS 3D modes can run.
 *
 * MiDaS is bundled as an asset; the Depth Anything V2 Small variants are downloaded on demand
 * from the release that published them and verified against the SHA-256 printed in that
 * release's notes (see docs/3D_DEPTH_MODELS.md for provenance and licences). All three share the
 * same semantics the renderer relies on: a single RGB image in, a relative *inverse* depth map out
 * (larger = nearer), which the renderer min-max normalises before use. Tensor shapes and data
 * types are read from the interpreter at load time, so a descriptor only needs to say where the
 * file comes from and how large the square input is.
 */
public enum DepthModel {
    /** MiDaS v2, Qualcomm AI Hub w8a8 export: uint8 256x256 in, uint8 256x256 out. Bundled. */
    MIDAS_V2_256("midas", "midas-midas-v2-w8a8.tflite", 256, null, null, 0),

    /**
     * Depth Anything V2 Small, 252x252, float16 weights, built for the LiteRT GPU delegate
     * (coldbricks/finally-models, Apache-2.0). float32 [1,252,252,3] RGB in 0..1 in (ImageNet
     * normalisation baked in), float32 [1,252,252,1] inverse depth out.
     */
    DEPTH_ANYTHING_V2_SMALL_252("dav2_252", "depth-anything-v2-small-252-gpu-v2.tflite", 252,
            "https://github.com/coldbricks/finally-models/releases/download/depth-anything-v2-small-252-gpu-v2/depth-anything-v2-small-252-gpu-v2.tflite",
            "5eeaa55d868ca29583e95a9b648e9ee816b7024f6d1821d0cf21269ccc00bee6", 97885536L),

    /**
     * Depth Anything V2 Small, 364x364, float16 weights, GPU-friendly graph
     * (illuminazionetech/VRClip "depth-model" release, Apache-2.0 weights). float32 [1,364,364,3]
     * RGB in 0..1 in (normalisation baked in), float32 [1,364,364] inverse depth out.
     */
    DEPTH_ANYTHING_V2_SMALL_364("dav2_364", "depth_anything_v2_small_live_364.tflite", 364,
            "https://github.com/illuminazionetech/VRClip/releases/download/depth-model/depth_anything_v2_small_live_364.tflite",
            "875590dad368b1f3c97d1051ca4f3c48d75f51dc48c0c95f9224500028b6e273", 49659680L);

    public static final String PREF_KEY = "depth_model_list";
    public static final DepthModel DEFAULT = MIDAS_V2_256;
    private static final String MODEL_DIR = "depth-models";

    /** Value stored in the depth_model_list preference. */
    public final String prefValue;
    /** Asset name (bundled) or file name inside the app's depth-models directory (downloaded). */
    public final String fileName;
    /** Square input edge in pixels, used to size GL buffers before the interpreter exists. */
    public final int inputSize;
    /** Download URL, or null when the model ships inside the APK. */
    public final String downloadUrl;
    /** Lower-case hex SHA-256 of the file as published by its release, null for bundled models. */
    public final String sha256;
    /** Exact file size in bytes, 0 for bundled models. */
    public final long sizeBytes;

    DepthModel(String prefValue, String fileName, int inputSize, String downloadUrl, String sha256, long sizeBytes) {
        this.prefValue = prefValue;
        this.fileName = fileName;
        this.inputSize = inputSize;
        this.downloadUrl = downloadUrl;
        this.sha256 = sha256;
        this.sizeBytes = sizeBytes;
    }

    public boolean isBundled() {
        return downloadUrl == null;
    }

    public static DepthModel fromPrefValue(String value) {
        for (DepthModel m : values()) {
            if (m.prefValue.equals(value)) {
                return m;
            }
        }
        return DEFAULT;
    }

    public static File modelDirectory(Context context) {
        return new File(context.getFilesDir(), MODEL_DIR);
    }

    /** The on-disk location of a downloaded model (meaningless for bundled ones). */
    public File localFile(Context context) {
        return new File(modelDirectory(context), fileName);
    }

    /**
     * True when the model can be loaded right now. A downloaded file counts only when it has the
     * published size; the SHA-256 is verified once, when the download completes, and the file is
     * only moved into place after that check passes.
     */
    public boolean isAvailable(Context context) {
        if (isBundled()) {
            return true;
        }
        File f = localFile(context);
        return f.isFile() && f.length() == sizeBytes;
    }

    /** Short label for the performance overlay. */
    public String shortName() {
        switch (this) {
            case DEPTH_ANYTHING_V2_SMALL_252: return "DAv2-252";
            case DEPTH_ANYTHING_V2_SMALL_364: return "DAv2-364";
            default: return "MiDaS-256";
        }
    }

    /** Human-readable size for dialogs, e.g. "98 MB". */
    public String sizeLabel() {
        return Math.round(sizeBytes / 1_000_000.0) + " MB";
    }
}
