package com.limelight.utils;

import android.content.Context;
import android.content.res.AssetFileDescriptor;
import android.graphics.SurfaceTexture;
import android.opengl.GLES20;
import android.opengl.GLES30;
import android.opengl.GLSurfaceView;
import android.os.Build;
import android.util.Log;
import android.view.Surface;

import com.limelight.LimeLog;
import com.limelight.preferences.PreferenceConfiguration;

import org.opencv.android.OpenCVLoader;
import org.opencv.core.Core;
import org.opencv.core.CvType;
import org.opencv.core.Mat;
import org.opencv.core.Scalar;
import org.opencv.imgproc.Imgproc;
import org.tensorflow.lite.DataType;
import org.tensorflow.lite.Interpreter;
import org.tensorflow.lite.Tensor;
import org.tensorflow.lite.gpu.GpuDelegate;
import org.tensorflow.lite.gpu.GpuDelegateFactory;
import org.tensorflow.lite.nnapi.NnApiDelegate;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.MappedByteBuffer;
import java.nio.channels.FileChannel;
import java.util.Arrays;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.CancellationException;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import javax.microedition.khronos.egl.EGLConfig;
import javax.microedition.khronos.opengles.GL10;

public class Stereo3DRenderer implements GLSurfaceView.Renderer, SurfaceTexture.OnFrameAvailableListener {

    // Constants
    private static final int GL_TEXTURE_EXTERNAL_OES = 0x8D65;
    private static final float[] QUAD_VERTICES = {-1.0f, -1.0f, 1.0f, -1.0f, -1.0f, 1.0f, 1.0f, 1.0f};
    private static final float[] TEXTURE_VERTICES = {0.0f, 1.0f, 1.0f, 1.0f, 0.0f, 0.0f, 1.0f, 0.0f};
    private static final float[] TEXTURE_VERTICES_FLIPPED = {0.0f, 0.0f, 1.0f, 0.0f, 0.0f, 1.0f, 1.0f, 1.0f};
    // The depth model in use. Chosen in the constructor from the preferences (falling back to the
    // bundled MiDaS when a downloadable model is not on disk); the input size is taken from the
    // descriptor so GL resources can be sized before the interpreter exists, then confirmed from
    // the interpreter's tensors in adoptTensorLayout().
    private DepthModel depthModel = DepthModel.DEFAULT;
    private int modelInputHeight = DepthModel.DEFAULT.inputSize;
    private int modelInputWidth = DepthModel.DEFAULT.inputSize;
    // Synced ("movie") mode waits for the current frame's depth map, but never longer than this.
    private static final long MOVIE_MODE_MAX_DEPTH_WAIT_NS = 100_000_000L;
    private final int NUM_BUFFERS = 6;
    private final int NUM_INPUT_BUFFERS = 10;
    private final int NUM_SMOOTHED_BUFFERS = 3;
    private final int[] pboHandles = new int[2];
    private int pboIndex = 0;
    private boolean pboPrimed;

    public static boolean isMovieMode = true;
    private int PBO_SIZE = modelInputWidth * modelInputHeight * 4;

    // Public Static Fields
    public static volatile float fps = 0;
    public static volatile float threeDFps = 0;
    public static volatile float drawDelay = 0.0f;
    public static Boolean isDebugMode = false;
    public static Boolean isActive = false;
    public static String renderer = "CPU";

    private int calcFps;

    // Final Member Variables
    private final Context context;
    private final RenderHost host;
    private final OnSurfaceReadyListener onSurfaceReadyListener;
    private final Object frameLock = new Object();
    private final FloatBuffer quadVertexBuffer;
    private final FloatBuffer textureVertexBuffer;
    private final FloatBuffer flippedTextureVertexBuffer;
    private final AtomicBoolean frameAvailable = new AtomicBoolean(false);

    // OpenGL Handles
    private int bilateralBlurProgram;
    private int depthMapTextureId;
    private int dibr3dProgram;

    private int fboHandle;
    private int fboTextureId;
    private int filterFboHandle;
    private int filteredDepthMapTextureId;
    private int intermediateFboHandle;
    private int intermediateTextureId;
    private int simple3dProgram;
    private int videoTextureId;

    // Each GL surface owns an independent model, worker pair and buffer pools.
    private volatile DepthSession depthSession;
    private volatile long glGeneration;

    // Other Member Variables
    private long totalDrawTime = 0;
    private long lastFpsTime = 0;
    private ByteBuffer currentlyRenderingMap;
    private volatile boolean stopped;
    private PreferenceConfiguration prefConfig;
    private Surface videoSurface;
    private SurfaceTexture videoSurfaceTexture;

    private float ON_DRAW_CHANGE_TRESHOLD = 2.0f;


    public interface OnSurfaceReadyListener {
        void onStereo3DSurfaceReady(Surface surface);
    }

    /**
     * What the renderer needs from whatever owns its GL context: a GLSurfaceView on a flat
     * display, or an EGL thread drawing into an Android XR SurfaceEntity (com.limelight.xr).
     */
    public interface RenderHost {
        void requestRender();
        /** true = RENDERMODE_CONTINUOUSLY, false = RENDERMODE_WHEN_DIRTY */
        void setContinuousRendering(boolean continuous);
        void queueEvent(Runnable r);
        int getWidth();
        int getHeight();
        /** true when the host shows GL output upside down (XR SurfaceEntity); the eye pass compensates. */
        default boolean flipOutputVertically() { return false; }
    }

    public static RenderHost hostFor(final GLSurfaceView view) {
        return new RenderHost() {
            @Override public void requestRender() { view.requestRender(); }
            @Override public void setContinuousRendering(boolean continuous) {
                view.setRenderMode(continuous ? GLSurfaceView.RENDERMODE_CONTINUOUSLY : GLSurfaceView.RENDERMODE_WHEN_DIRTY);
            }
            @Override public void queueEvent(Runnable r) { view.queueEvent(r); }
            @Override public int getWidth() { return view.getWidth(); }
            @Override public int getHeight() { return view.getHeight(); }
        };
    }

    private static boolean openCvLoaded = false;

    // OpenCV (a ~23 MB native library) used to be loaded from a static initializer, which ran on the
    // first touch of ANY static member of this class -- including the perf overlay's stats fields and
    // the movie-mode flag -- i.e. on every stream start, even in 2D mode. Load it only when a 3D
    // renderer is actually constructed.
    private static synchronized void ensureOpenCvLoaded() {
        if (openCvLoaded) {
            return;
        }
        openCvLoaded = true;
        if (!OpenCVLoader.initLocal()) {
            LimeLog.severe("Internal OpenCV library not found. Using OpenCV Manager for initialization");
        } else {
            LimeLog.info("OpenCV library found inside package. Using it!");
        }
    }

    public Stereo3DRenderer(RenderHost host, OnSurfaceReadyListener listener, Context context, PreferenceConfiguration prefConfig) {
        ensureOpenCvLoaded();

        this.host = host;
        this.onSurfaceReadyListener = listener;
        this.context = context;
        this.prefConfig = prefConfig;
        this.depthModel = selectDepthModel(context, prefConfig);
        this.modelInputWidth = depthModel.inputSize;
        this.modelInputHeight = depthModel.inputSize;

        quadVertexBuffer = ByteBuffer.allocateDirect(QUAD_VERTICES.length * 4).order(ByteOrder.nativeOrder()).asFloatBuffer();
        quadVertexBuffer.put(QUAD_VERTICES).position(0);
        textureVertexBuffer = ByteBuffer.allocateDirect(TEXTURE_VERTICES.length * 4).order(ByteOrder.nativeOrder()).asFloatBuffer();
        textureVertexBuffer.put(TEXTURE_VERTICES).position(0);
        flippedTextureVertexBuffer = ByteBuffer.allocateDirect(TEXTURE_VERTICES_FLIPPED.length * 4).order(ByteOrder.nativeOrder()).asFloatBuffer();
        flippedTextureVertexBuffer.put(TEXTURE_VERTICES_FLIPPED).position(0);
    }

    public void setPrefConfig(PreferenceConfiguration prefConfig) {
        this.prefConfig = prefConfig;
        host.requestRender();
    }

    public synchronized void onSurfaceDestroyed() {
        if (stopped && depthSession == null) return;
        stopped = true;
        DepthSession closingSession = depthSession;
        depthSession = null;
        if (closingSession != null) closingSession.stop();
        LimeLog.info("Quit called. Shutting down 3dRenderer.");
        if (videoSurface != null) {
            videoSurface.release();
            videoSurface = null;
        }
        if (videoSurfaceTexture != null) {
            videoSurfaceTexture.release();
            videoSurfaceTexture = null;
        }

        final long closingGeneration = glGeneration;
        host.queueEvent(() -> {
            // A new context may reuse these numeric names before this event runs.
            if (glGeneration != closingGeneration) return;
            GLES20.glDeleteProgram(simple3dProgram);
            GLES20.glDeleteProgram(bilateralBlurProgram);
            GLES20.glDeleteProgram(dibr3dProgram);

            int[] textures = {
                    videoTextureId,
                    depthMapTextureId,
                    filteredDepthMapTextureId,
                    fboTextureId,
                    intermediateTextureId
            };
            GLES20.glDeleteTextures(textures.length, textures, 0);

            int[] fbos = {fboHandle, intermediateFboHandle, filterFboHandle};
            GLES20.glDeleteFramebuffers(fbos.length, fbos, 0);
            GLES30.glDeleteBuffers(pboHandles.length, pboHandles, 0);
        });

        currentlyRenderingMap = null;
        drawDelay = 0.0f;
        calcFps = 0;
        renderer = "CPU";
        isActive = false;
    }

    public Surface getVideoSurface() {
        return videoSurface;
    }

    @Override
    public void onFrameAvailable(SurfaceTexture surfaceTexture) {
        if (stopped || surfaceTexture != videoSurfaceTexture) return;
        synchronized (frameLock) {
            frameAvailable.set(true);
        }
        host.requestRender();
    }

    @Override
    public void onSurfaceCreated(GL10 gl, EGLConfig config) {
        final DepthSession depth;
        final Future<?> initialization;
        synchronized (this) {
            if (depthSession != null) depthSession.stop();
            if (videoSurface != null) videoSurface.release();
            if (videoSurfaceTexture != null) videoSurfaceTexture.release();
            ++glGeneration;
            stopped = false;
            frameAvailable.set(false);
            currentlyRenderingMap = null;
            block = false;
            flatMapUploaded = false;
            lastFpsTime = 0;
            totalDrawTime = 0;
            calcFps = 0;
            fps = 0;
            threeDFps = 0;
            drawDelay = 0;
            videoTextureId = createExternalOESTexture();
            videoSurfaceTexture = new SurfaceTexture(videoTextureId);
            // A SurfaceTexture's default buffer size is 1x1. MediaCodec sets its own buffer
            // dimensions, but a Vulkan swapchain (PyroWave) takes the surface's current extent,
            // which is this default size: every 3D PyroWave frame was decoded into one pixel and
            // the quad showed a flat grey (fork.17 on the Galaxy XR). Size it to the stream.
            if (prefConfig != null && prefConfig.width > 0 && prefConfig.height > 0) {
                videoSurfaceTexture.setDefaultBufferSize(prefConfig.width, prefConfig.height);
            }
            videoSurfaceTexture.setOnFrameAvailableListener(this);
            videoSurface = new Surface(videoSurfaceTexture);

            // Load the model first: every buffer and FBO below is sized from its input tensor.
            // The GPU delegate must be created, invoked and closed on one thread.
            depth = new DepthSession();
            depthSession = depth;
            initialization = depth.inferenceExecutor.submit(() -> {
                depth.initializeTfLite();
                depth.adoptTensorLayout();
            });
            // Cancelling this future wakes the GL waiter even if native construction
            // ignores interruption. Model close remains queued on its owner thread.
            depth.inferenceTask = initialization;
        }

        // Never hold the teardown lock while waiting for native initialization.
        try {
            initialization.get();
        } catch (InterruptedException e) {
            initialization.cancel(true);
            Thread.currentThread().interrupt();
            stopFailedInitialization(depth);
            return;
        } catch (CancellationException e) {
            return; // The owning session was already retired by stop/replacement.
        } catch (ExecutionException e) {
            LimeLog.severe("Depth model initialization failed: " + e.getCause());
            stopFailedInitialization(depth);
            return;
        }
        synchronized (this) {
            if (stopped || depthSession != depth || depth.stopped) return;
            modelInputWidth = depth.modelInputWidth;
            modelInputHeight = depth.modelInputHeight;
            renderer = depth.backend;

            depthMapTextureId = createEmptyTexture(modelInputWidth, modelInputHeight);

            simple3dProgram = createProgram(ShaderUtils.SIMPLE_VERTEX_SHADER, ShaderUtils.SIMPLE_FRAGMENT_SHADER);
            bilateralBlurProgram = createProgram(ShaderUtils.VERTEX_SHADER, ShaderUtils.OPTIMIZED_SINGLE_PASS_GAUSSIAN_BLUR_SHADER);
            dibr3dProgram = createProgram(ShaderUtils.VERTEX_SHADER, ShaderUtils.FRAGMENT_SHADER_3D);

            initializeFilterFbo();
            initializeIntermediateFbo();
            initializeFbo();
            depth.initBuffer();
            initializePBOs();

            if (onSurfaceReadyListener != null) {
                onSurfaceReadyListener.onStereo3DSurfaceReady(videoSurface);
            }
            if (stopped || depthSession != depth) return;
            depth.startWorkers();
            isActive = true;
        }
    }

    private synchronized void stopFailedInitialization(DepthSession depth) {
        if (depthSession == depth) onSurfaceDestroyed();
    }

    private void initializeIntermediateFbo() {
        intermediateTextureId = createRgbaTexture(modelInputWidth, modelInputHeight);
        int[] fbos = new int[1];
        GLES20.glGenFramebuffers(1, fbos, 0);
        intermediateFboHandle = fbos[0];
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, intermediateFboHandle);
        GLES20.glFramebufferTexture2D(GLES20.GL_FRAMEBUFFER, GLES20.GL_COLOR_ATTACHMENT0, GLES20.GL_TEXTURE_2D, intermediateTextureId, 0);
        if (GLES20.glCheckFramebufferStatus(GLES20.GL_FRAMEBUFFER) != GLES20.GL_FRAMEBUFFER_COMPLETE) {
            LimeLog.warning("Intermediate Framebuffer is not complete.");
        }
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, 0);
    }

    private float getParallax() {
        return prefConfig.parallax_depth * 0.7f;
    }

    private void applyTwoPassGaussianBlur() {
        int blurProgram = bilateralBlurProgram;

        GLES20.glUseProgram(blurProgram);

        int posHandle = GLES20.glGetAttribLocation(blurProgram, "a_Position");
        int texHandle = GLES20.glGetAttribLocation(blurProgram, "a_TexCoord");
        int inputTextureHandle = GLES20.glGetUniformLocation(blurProgram, "s_InputTexture");
        int texelSizeHandle = GLES20.glGetUniformLocation(blurProgram, "u_texelSize");
        int directionHandle = GLES20.glGetUniformLocation(blurProgram, "u_blurDirection");
        int parallaxHandle = GLES20.glGetUniformLocation(blurProgram, "u_parallax");
        GLES20.glVertexAttribPointer(posHandle, 2, GLES20.GL_FLOAT, false, 0, quadVertexBuffer);
        GLES20.glVertexAttribPointer(texHandle, 2, GLES20.GL_FLOAT, false, 0, textureVertexBuffer);
        GLES20.glEnableVertexAttribArray(posHandle);
        GLES20.glEnableVertexAttribArray(texHandle);
        GLES20.glUniform1f(parallaxHandle, getParallax());

        GLES20.glUniform2f(texelSizeHandle, 1.0f / modelInputWidth, 1.0f / modelInputHeight);

        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, intermediateFboHandle);
        GLES20.glViewport(0, 0, modelInputWidth, modelInputHeight);

        GLES20.glUniform2f(directionHandle, 1.0f, 0.0f);

        GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, depthMapTextureId);
        GLES20.glUniform1i(inputTextureHandle, 0);

        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4);

        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, filterFboHandle);
        GLES20.glViewport(0, 0, modelInputWidth, modelInputHeight);

        GLES20.glUniform2f(directionHandle, 0.0f, 1.0f);

        GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, intermediateTextureId);
        GLES20.glUniform1i(inputTextureHandle, 0);

        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4);

        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, 0);
    }

    private void drawBothEyes(int dualBubble3dProgram, float convergence, float shift) {
        int viewWidth = host.getWidth();
        int viewHeight = host.getHeight();

        float parallax = getParallax() * 0.06f;

        GLES20.glViewport(0, 0, viewWidth / 2, viewHeight);
        drawEye(dualBubble3dProgram, -parallax, convergence, shift);

        GLES20.glViewport(viewWidth / 2, 0, viewWidth / 2, viewHeight);
        drawEye(dualBubble3dProgram, parallax, convergence, shift);
    }

    private void drawEye(int program, float parallax, float convergence, float shift) {
        GLES20.glUseProgram(program);
        int posHandle = GLES20.glGetAttribLocation(program, "a_Position");
        int texHandle = GLES20.glGetAttribLocation(program, "a_TexCoord");
        int colorTexHandle = GLES20.glGetUniformLocation(program, "s_ColorTexture");
        int depthTexHandle = GLES20.glGetUniformLocation(program, "s_DepthTexture");
        int parallaxHandle = GLES20.glGetUniformLocation(program, "u_parallax");
        int convergenceHandle = GLES20.glGetUniformLocation(program, "u_convergence");
        int shiftHandle = GLES20.glGetUniformLocation(program, "u_shift");
        int debugModeHandle = GLES20.glGetUniformLocation(program, "u_debugMode");

        GLES20.glVertexAttribPointer(posHandle, 2, GLES20.GL_FLOAT, false, 0, quadVertexBuffer);
        GLES20.glVertexAttribPointer(texHandle, 2, GLES20.GL_FLOAT, false, 0,
                host.flipOutputVertically() ? flippedTextureVertexBuffer : textureVertexBuffer);
        GLES20.glEnableVertexAttribArray(posHandle);
        GLES20.glEnableVertexAttribArray(texHandle);

        GLES20.glUniform1i(debugModeHandle, isDebugMode ? 1 : 0);

        GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
        GLES20.glBindTexture(GL_TEXTURE_EXTERNAL_OES, videoTextureId);
        GLES20.glUniform1i(colorTexHandle, 0);

        GLES20.glActiveTexture(GLES20.GL_TEXTURE1);
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, filteredDepthMapTextureId);
        GLES20.glUniform1i(depthTexHandle, 1);

        // Guide for the edge-aware depth upsampling: the model-resolution copy of the video frame
        // that the depth map was computed from (see ShaderUtils.FRAGMENT_SHADER_3D).
        int guideTexHandle = GLES20.glGetUniformLocation(program, "s_GuideTexture");
        int depthTexelHandle = GLES20.glGetUniformLocation(program, "u_depthTexelSize");
        int guidedHandle = GLES20.glGetUniformLocation(program, "u_guidedUpsampling");
        GLES20.glActiveTexture(GLES20.GL_TEXTURE2);
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, fboTextureId);
        GLES20.glUniform1i(guideTexHandle, 2);
        GLES20.glUniform2f(depthTexelHandle, 1.0f / modelInputWidth, 1.0f / modelInputHeight);
        GLES20.glUniform1i(guidedHandle, (prefConfig != null && prefConfig.depthGuidedUpsampling) ? 1 : 0);

        GLES20.glUniform1f(parallaxHandle, parallax);
        GLES20.glUniform1f(convergenceHandle, convergence);
        GLES20.glUniform1f(shiftHandle, shift);
        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4);
    }

    private void drawWithShader() {
        if (prefConfig != null) {
            drawBothEyes(dibr3dProgram, prefConfig.convergence_ratio, prefConfig.balance_shift);
        }
    }


    private boolean block;
    // No model on any backend: a flat depth map was uploaded once so the stream still shows.
    private boolean flatMapUploaded;

    @Override
    public void onDrawFrame(GL10 gl) {
        DepthSession depth = depthSession;
        if (stopped || depth == null) return;
        renderer = depth.backend;
        long startTime = System.nanoTime();

        // A newly decoded image and a completed depth map each request a draw.
        // Redrawing for depth/settings must not submit the same image to AI again.
        host.setContinuousRendering(false);
        final boolean newVideoFrame;
        synchronized (frameLock) {
            newVideoFrame = frameAvailable.getAndSet(false);
            block = newVideoFrame && isMovieMode;
        }
        if (newVideoFrame) {
            try {
                videoSurfaceTexture.updateTexImage();
            } catch (Exception e) {
                Log.w("Stereo3DRenderer", "updateTexImage failed", e);
                return;
            }
        }

        long startTimeAi = System.nanoTime();
        long endTimeAi = System.nanoTime();
        if (depth.tflite != null) {
            ByteBuffer pixelBufferForAI = newVideoFrame ? depth.freeInputBuffers.poll() : null;
            if (pixelBufferForAI != null) {
                // Double-buffered PBO readback (one frame of latency, no GL stall); the
                // synced mode and API < 24 use current-frame synchronous readback.
                boolean success = !isMovieMode && Build.VERSION.SDK_INT >= Build.VERSION_CODES.N
                        ? readPixelsForAI_Async(pixelBufferForAI)
                        : readPixelsForAI(pixelBufferForAI);
                if (success) {
                    if (depth.inferenceInputQueue.offer(new RenderResult(pixelBufferForAI))) {
                        if (Boolean.TRUE.equals(isDebugMode)) Log.d("AiTask", "Success: The AI will now process this buffer.");
                    } else {
                        depth.freeInputBuffers.offer(pixelBufferForAI);
                    }
                } else {
                    depth.freeInputBuffers.offer(pixelBufferForAI);
                }
            }
            ByteBuffer newMap = null;
            if (block && isMovieMode) {
                // Synced mode waits for this frame's depth map, but a slow or failed model must
                // not freeze the GL thread: give up after MOVIE_MODE_MAX_DEPTH_WAIT_NS or as
                // soon as the inference thread is gone, and render with the previous map.
                // The worker signals depthReady when it publishes; while this thread is
                // parked here it must not also be asked to redraw for that same map.
                long deadline = System.nanoTime() + MOVIE_MODE_MAX_DEPTH_WAIT_NS;
                depth.depthWaiting.set(true);
                try {
                    synchronized (depth.depthReady) {
                        while ((newMap = depth.latestDepthMap.getAndSet(null)) == null
                                && depth.isAiRunning.get()) {
                            long remainingNs = deadline - System.nanoTime();
                            if (remainingNs <= 0) {
                                break;
                            }
                            try {
                                depth.depthReady.wait(Math.max(1L, remainingNs / 1_000_000L));
                            } catch (InterruptedException e) {
                                Thread.currentThread().interrupt();
                                break;
                            }
                        }
                    }
                } finally {
                    depth.depthWaiting.set(false);
                }
                if (newMap == null) {
                    block = false;
                }
            } else {
                newMap = depth.latestDepthMap.getAndSet(null);
            }
            if (newMap != null) {
                block = false;
                if (currentlyRenderingMap != null) depth.freeSmoothedBuffers.offer(currentlyRenderingMap);
                currentlyRenderingMap = newMap;
                uploadLatestDepthMapToGpu(newMap);
                endTimeAi = System.nanoTime();
                if (Boolean.TRUE.equals(isDebugMode)) Log.d("Stereo3DRenderer", "DepthMap OutputSpeed " + (endTimeAi - startTimeAi) / 1_000_000 + " ms");
            }

        } else if (!flatMapUploaded) {
            // No model on any backend: draw the stream flat (zero parallax) instead of
            // leaving the entity black while the decoder keeps producing frames.
            uploadLatestDepthMapToGpu(createFlatDepthMap());
            flatMapUploaded = true;
        }

        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT);
        applyTwoPassGaussianBlur();
        drawWithShader();
        long endTime = System.nanoTime();

        updatePerformanceStats(startTime, endTime, depth);
    }

    private void updatePerformanceStats(long startTime, long endTime, DepthSession depth) {
        if (lastFpsTime == 0) {
            lastFpsTime = startTime;
        }
        totalDrawTime += endTime - startTime;
        calcFps++;
        long elapsed = endTime - lastFpsTime;
        if (elapsed >= 1_000_000_000L) {
            // The overlay labels this value in milliseconds. Count each draw once,
            // including the reporting frame, and average this window's durations.
            drawDelay = (float) totalDrawTime / calcFps / 1_000_000f;
            fps = calcFps * 1_000_000_000f / elapsed;
            threeDFps = depth.completedDepthFrames.getAndSet(0) * 1_000_000_000f / elapsed;
            totalDrawTime = 0;
            calcFps = 0;
            lastFpsTime = endTime;
            if (Boolean.TRUE.equals(isDebugMode)) {
                int freeInputCap = depth.freeInputBuffers.size() + depth.freeInputBuffers.remainingCapacity();
                int aiInCap = depth.inferenceInputQueue.size() + depth.inferenceInputQueue.remainingCapacity();
                int aiOutCap = depth.filledOutputBuffers.size() + depth.filledOutputBuffers.remainingCapacity();
                int freeSmoothCap = depth.freeSmoothedBuffers.size() + depth.freeSmoothedBuffers.remainingCapacity();

                String queueStatus = String.format(
                        "Queues (Free/Cap) | FreeInput: %d/%d, To_AI: %d/%d, From_AI: %d/%d, Free_Smooth: %d/%d",
                        depth.freeInputBuffers.remainingCapacity(), freeInputCap,
                        depth.inferenceInputQueue.remainingCapacity(), aiInCap,
                        depth.filledOutputBuffers.remainingCapacity(), aiOutCap,
                        depth.freeSmoothedBuffers.remainingCapacity(), freeSmoothCap
                );
                Log.d("Stereo3DRenderer", queueStatus);
            }
        }
    }

    private ByteBuffer createFlatDepthMap() {
        int mapSize = modelInputWidth * modelInputHeight;
        byte[] flatData = new byte[mapSize];
        Arrays.fill(flatData, (byte) 128);

        ByteBuffer flatMap = ByteBuffer.allocateDirect(mapSize).order(ByteOrder.nativeOrder());

        flatMap.put(flatData);
        flatMap.rewind();
        return flatMap;
    }

    private void uploadLatestDepthMapToGpu(ByteBuffer depthMap) {
        if (depthMap != null) {
            GLES20.glActiveTexture(GLES20.GL_TEXTURE1);
            GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, depthMapTextureId);
            GLES20.glTexSubImage2D(GLES20.GL_TEXTURE_2D, 0, 0, 0, modelInputWidth, modelInputHeight, GLES20.GL_LUMINANCE, GLES20.GL_UNSIGNED_BYTE, depthMap);
        }
    }

    private void drawQuad(int program, float scale, float offset) {
        GLES20.glUseProgram(program);

        int posHandle = GLES20.glGetAttribLocation(program, "a_Position");
        int texHandle = GLES20.glGetAttribLocation(program, "a_TexCoord");
        int offsetHandle = GLES20.glGetUniformLocation(program, "u_xOffset");
        int scaleHandle = GLES20.glGetUniformLocation(program, "u_xScale");

        GLES20.glVertexAttribPointer(posHandle, 2, GLES20.GL_FLOAT, false, 0, quadVertexBuffer);
        GLES20.glVertexAttribPointer(texHandle, 2, GLES20.GL_FLOAT, false, 0, textureVertexBuffer);
        GLES20.glEnableVertexAttribArray(posHandle);
        GLES20.glEnableVertexAttribArray(texHandle);

        GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
        GLES20.glBindTexture(GL_TEXTURE_EXTERNAL_OES, videoTextureId);

        if (scaleHandle != -1) GLES20.glUniform1f(scaleHandle, scale);
        if (offsetHandle != -1) GLES20.glUniform1f(offsetHandle, offset);

        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4);
    }

    private static class InferenceResult {
        final ByteBuffer pixelBuffer;
        final ByteBuffer rawDepthBuffer;

        InferenceResult(ByteBuffer pixelBuffer, ByteBuffer rawDepthBuffer) {
            this.pixelBuffer = pixelBuffer;
            this.rawDepthBuffer = rawDepthBuffer;
        }
    }

    private static class RenderResult {
        final ByteBuffer pixelBuffer;

        RenderResult(ByteBuffer pixelBuffer) {
            this.pixelBuffer = pixelBuffer;
        }
    }

    public static void convertRgbaToRgb(ByteBuffer rgbaBuffer, ByteBuffer rgbBuffer, int width, int height) {
        Mat rgbaMat = null;
        Mat rgbMat = null;
        try {
            rgbaMat = new Mat(height, width, CvType.CV_8UC4, rgbaBuffer);
            rgbMat = new Mat(height, width, CvType.CV_8UC3, rgbBuffer);
            Imgproc.cvtColor(rgbaMat, rgbMat, Imgproc.COLOR_RGBA2RGB);
        } finally {
            if (rgbaMat != null) {
                rgbaMat.release();
            }
            if (rgbMat != null) {
                rgbMat.release();
            }
        }
    }

    /**
     * RGBA8 (glReadPixels order, bottom row first) -> float32 RGB in 0..1, top row first, which is
     * what the Depth Anything exports take (ImageNet normalisation is inside the graph).
     */
    public static void convertRgbaToFloatRgb(ByteBuffer rgbaBuffer, ByteBuffer floatRgbBuffer, int width, int height) {
        Mat rgbaMat = null;
        Mat rgbMat = null;
        Mat flippedMat = null;
        Mat floatMat = null;
        try {
            rgbaBuffer.rewind();
            rgbaMat = new Mat(height, width, CvType.CV_8UC4, rgbaBuffer);
            rgbMat = new Mat();
            Imgproc.cvtColor(rgbaMat, rgbMat, Imgproc.COLOR_RGBA2RGB);
            flippedMat = new Mat();
            Core.flip(rgbMat, flippedMat, 0);
            floatRgbBuffer.rewind();
            floatMat = new Mat(height, width, CvType.CV_32FC3, floatRgbBuffer);
            flippedMat.convertTo(floatMat, CvType.CV_32FC3, 1.0 / 255.0);
        } finally {
            if (rgbaMat != null) rgbaMat.release();
            if (rgbMat != null) rgbMat.release();
            if (flippedMat != null) flippedMat.release();
            if (floatMat != null) floatMat.release();
        }
    }

    public static double calculateAverageDifferenceOCV(ByteBuffer buffer1, ByteBuffer buffer2, int width, int height) {
        if (buffer1 == null || buffer2 == null) {
            return 1;
        }

        Mat mat1 = null;
        Mat mat2 = null;
        Mat diffMat = null;
        try {
            mat1 = new Mat(height, width, CvType.CV_8UC1, buffer1);
            mat2 = new Mat(height, width, CvType.CV_8UC1, buffer2);
            diffMat = new Mat();
            Core.absdiff(mat1, mat2, diffMat);
            Scalar meanDifference = Core.mean(diffMat);
            return meanDifference.val[0] / 255.0;
        } finally {
            if (mat1 != null) {
                mat1.release();
            }
            if (mat2 != null) {
                mat2.release();
            }
            if (diffMat != null) {
                diffMat.release();
            }
        }
    }

    private void initializePBOs() {
        pboIndex = 0;
        pboPrimed = false;
        PBO_SIZE = modelInputWidth * modelInputHeight * 4;

        GLES30.glGenBuffers(2, pboHandles, 0);

        GLES30.glBindBuffer(GLES30.GL_PIXEL_PACK_BUFFER, pboHandles[0]);
        GLES30.glBufferData(GLES30.GL_PIXEL_PACK_BUFFER, PBO_SIZE, null, GLES30.GL_DYNAMIC_READ);

        GLES30.glBindBuffer(GLES30.GL_PIXEL_PACK_BUFFER, pboHandles[1]);
        GLES30.glBufferData(GLES30.GL_PIXEL_PACK_BUFFER, PBO_SIZE, null, GLES30.GL_DYNAMIC_READ);

        GLES30.glBindBuffer(GLES30.GL_PIXEL_PACK_BUFFER, 0);
    }

    private Boolean readPixelsForAI(ByteBuffer destinationBuffer) {
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, fboHandle);
        GLES20.glViewport(0, 0, modelInputWidth, modelInputHeight);
        drawQuad(simple3dProgram, 1.0f, 0.0f);
        destinationBuffer.rewind();

        GLES20.glReadPixels(0, 0, modelInputWidth, modelInputHeight, GLES20.GL_RGBA, GLES20.GL_UNSIGNED_BYTE, destinationBuffer);

        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, 0);

        return true;
    }

    private boolean readPixelsForAI_Async(ByteBuffer destinationBuffer) {
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, fboHandle);
        GLES20.glViewport(0, 0, modelInputWidth, modelInputHeight);
        drawQuad(simple3dProgram, 1.0f, 0.0f);
        int writeIndex = pboIndex;
        int readIndex = (pboIndex + 1) % 2;
        GLES30.glBindBuffer(GLES30.GL_PIXEL_PACK_BUFFER, pboHandles[writeIndex]);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            GLES30.glReadPixels(0, 0, modelInputWidth, modelInputHeight, GLES30.GL_RGBA, GLES30.GL_UNSIGNED_BYTE, 0);
        }
        if (!pboPrimed) {
            // The other PBO has not received a frame yet.
            pboPrimed = true;
            pboIndex = readIndex;
            GLES30.glBindBuffer(GLES30.GL_PIXEL_PACK_BUFFER, 0);
            GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, 0);
            return false;
        }
        GLES30.glBindBuffer(GLES30.GL_PIXEL_PACK_BUFFER, pboHandles[readIndex]);
        ByteBuffer mappedBuffer = (ByteBuffer) GLES30.glMapBufferRange(
                GLES30.GL_PIXEL_PACK_BUFFER, 0, PBO_SIZE, GLES30.GL_MAP_READ_BIT);
        boolean success = false;
        if (mappedBuffer != null) {
            destinationBuffer.rewind();
            mappedBuffer.rewind();
            destinationBuffer.put(mappedBuffer);
            GLES30.glUnmapBuffer(GLES30.GL_PIXEL_PACK_BUFFER);
            success = true;
        }

        GLES30.glBindBuffer(GLES30.GL_PIXEL_PACK_BUFFER, 0);
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, 0);

        pboIndex = readIndex;
        return success;
    }


    private static void closeModel(Interpreter interpreter, GpuDelegate gpu, NnApiDelegate nnApi) {
        if (interpreter != null) interpreter.close();
        if (gpu != null) gpu.close();
        if (nnApi != null) nnApi.close();
    }


    private MappedByteBuffer loadModelFile() throws IOException {
        if (depthModel.isBundled()) {
            try (AssetFileDescriptor fileDescriptor = context.getAssets().openFd(depthModel.fileName);
                 FileInputStream inputStream = new FileInputStream(fileDescriptor.getFileDescriptor())) {
                return inputStream.getChannel().map(FileChannel.MapMode.READ_ONLY,
                        fileDescriptor.getStartOffset(), fileDescriptor.getDeclaredLength());
            }
        }
        File file = depthModel.localFile(context);
        try (FileInputStream inputStream = new FileInputStream(file)) {
            return inputStream.getChannel().map(FileChannel.MapMode.READ_ONLY, 0, file.length());
        }
    }

    /**
     * The model the preferences ask for, if it is on disk; otherwise the bundled default. The
     * settings screen downloads models before letting the preference change, so the fallback only
     * triggers when the file was removed afterwards (cleared app data, failed verification).
     */
    private static DepthModel selectDepthModel(Context context, PreferenceConfiguration prefConfig) {
        DepthModel wanted = DepthModel.fromPrefValue(prefConfig != null ? prefConfig.depthModel : null);
        if (!wanted.isAvailable(context)) {
            LimeLog.warning("Depth model " + wanted.fileName + " is not available; falling back to " + DepthModel.DEFAULT.fileName);
            return DepthModel.DEFAULT;
        }
        return wanted;
    }

    /**
     * Reads the input/output tensor layout from the loaded interpreter so the conversion code and
     * the buffer sizes match the model instead of assuming MiDaS's uint8 256x256 contract.
     */


    @Override
    public void onSurfaceChanged(GL10 gl, int width, int height) {
        GLES20.glViewport(0, 0, width, height);
    }

    private void initializeFbo() {
        fboTextureId = createRgbaTexture(modelInputWidth, modelInputHeight);
        int[] fbos = new int[1];
        GLES20.glGenFramebuffers(1, fbos, 0);
        fboHandle = fbos[0];
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, fboHandle);
        GLES20.glFramebufferTexture2D(GLES20.GL_FRAMEBUFFER, GLES20.GL_COLOR_ATTACHMENT0, GLES20.GL_TEXTURE_2D, fboTextureId, 0);
        if (GLES20.glCheckFramebufferStatus(GLES20.GL_FRAMEBUFFER) != GLES20.GL_FRAMEBUFFER_COMPLETE) {
            LimeLog.severe("Framebuffer is not complete.");
        }
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, 0);
    }

    private void initializeFilterFbo() {
        filteredDepthMapTextureId = createRgbaTexture(modelInputWidth, modelInputHeight);
        int[] fbos = new int[1];
        GLES20.glGenFramebuffers(1, fbos, 0);
        filterFboHandle = fbos[0];
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, filterFboHandle);
        GLES20.glFramebufferTexture2D(GLES20.GL_FRAMEBUFFER, GLES20.GL_COLOR_ATTACHMENT0, GLES20.GL_TEXTURE_2D, filteredDepthMapTextureId, 0);
        if (GLES20.glCheckFramebufferStatus(GLES20.GL_FRAMEBUFFER) != GLES20.GL_FRAMEBUFFER_COMPLETE) {
            LimeLog.severe("Filter Framebuffer is not complete.");
        }
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, 0);
    }

    private int createExternalOESTexture() {
        int[] textures = new int[1];
        GLES20.glGenTextures(1, textures, 0);
        int textureId = textures[0];
        GLES20.glBindTexture(GL_TEXTURE_EXTERNAL_OES, textureId);
        GLES20.glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE);
        GLES20.glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE);
        GLES20.glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
        return textureId;
    }

    private int createEmptyTexture(int width, int height) {
        int[] textures = new int[1];
        GLES20.glGenTextures(1, textures, 0);
        int textureId = textures[0];
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, textureId);
        GLES20.glTexImage2D(GLES20.GL_TEXTURE_2D, 0, GLES20.GL_LUMINANCE, width, height, 0, GLES20.GL_LUMINANCE, GLES20.GL_UNSIGNED_BYTE, null);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
        return textureId;
    }

    private int createRgbaTexture(int width, int height) {
        int[] textures = new int[1];
        GLES20.glGenTextures(1, textures, 0);
        int textureId = textures[0];
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, textureId);
        GLES20.glTexImage2D(GLES20.GL_TEXTURE_2D, 0, GLES20.GL_RGBA, width, height, 0, GLES20.GL_RGBA, GLES20.GL_UNSIGNED_BYTE, null);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
        return textureId;
    }

    private int loadShader(int type, String shaderCode) {
        int shader = GLES20.glCreateShader(type);
        GLES20.glShaderSource(shader, shaderCode);
        GLES20.glCompileShader(shader);
        int[] compiled = new int[1];
        GLES20.glGetShaderiv(shader, GLES20.GL_COMPILE_STATUS, compiled, 0);
        if (compiled[0] == 0) {
            LimeLog.severe("Could not compile shader " + type + ":");
            LimeLog.severe(GLES20.glGetShaderInfoLog(shader));
            GLES20.glDeleteShader(shader);
            shader = 0;
        }
        return shader;
    }

    private int createProgram(String vertex, String fragment) {
        int vertexShader = loadShader(GLES20.GL_VERTEX_SHADER, vertex);
        if (vertexShader == 0) return 0;
        int fragmentShader = 0;
        int program = 0;
        boolean linked = false;
        try {
            fragmentShader = loadShader(GLES20.GL_FRAGMENT_SHADER, fragment);
            if (fragmentShader == 0) return 0;
            program = GLES20.glCreateProgram();
            if (program == 0) return 0;
            GLES20.glAttachShader(program, vertexShader);
            GLES20.glAttachShader(program, fragmentShader);
            GLES20.glLinkProgram(program);
            int[] linkStatus = new int[1];
            GLES20.glGetProgramiv(program, GLES20.GL_LINK_STATUS, linkStatus, 0);
            if (linkStatus[0] != GLES20.GL_TRUE) {
                LimeLog.severe("Could not link program: ");
                LimeLog.severe(GLES20.glGetProgramInfoLog(program));
                return 0;
            }
            linked = true;
            return program;
        } finally {
            // Deletion of attached shaders is deferred by GL until the linked
            // program releases them. Program deletion alone does not delete shaders.
            GLES20.glDeleteShader(vertexShader);
            if (fragmentShader != 0) GLES20.glDeleteShader(fragmentShader);
            if (!linked && program != 0) GLES20.glDeleteProgram(program);
        }
    }

    private static double hasSceneChangedFast(ByteBuffer currentFrame, ByteBuffer previousFrame, int modelInputWidth) {
        if (currentFrame == null || previousFrame == null || currentFrame.capacity() != previousFrame.capacity()) {
            return 0.0;
        }

        currentFrame.rewind();
        previousFrame.rewind();

        long totalDifference = 0;
        int pixelsSampled = 0;

        final int PIXEL_STRIDE = 4;
        final int PIXEL_SAMPLE_RATE = 32;
        final int ROW_SAMPLE_RATE = 32;
        final int SAMPLE_STRIDE = PIXEL_STRIDE * PIXEL_SAMPLE_RATE;
        final int ROW_STRIDE = modelInputWidth * PIXEL_STRIDE * ROW_SAMPLE_RATE;

        for (int row = 0; row < currentFrame.capacity(); row += ROW_STRIDE) {
            for (int col = 0; col < modelInputWidth * PIXEL_STRIDE; col += SAMPLE_STRIDE) {
                int index = row + col;
                if (index + 2 >= currentFrame.capacity()) break;

                totalDifference += Math.abs((currentFrame.get(index) & 0xFF) - (previousFrame.get(index) & 0xFF));
                totalDifference += Math.abs((currentFrame.get(index + 1) & 0xFF) - (previousFrame.get(index + 1) & 0xFF));
                totalDifference += Math.abs((currentFrame.get(index + 2) & 0xFF) - (previousFrame.get(index + 2) & 0xFF));
                pixelsSampled++;
            }
        }

        if (pixelsSampled == 0) return 0.0;

        double averageDifference = (double) totalDifference / pixelsSampled;

        return averageDifference;
    }


    /** Native work may outlive cancellation; none of its mutable state is shared with a replacement. */
    private final class DepthSession {
        private final ExecutorService inferenceExecutor = Executors.newSingleThreadExecutor();
        private final ExecutorService executorService = Executors.newSingleThreadExecutor();
        private Future<?> inferenceTask;
        private volatile boolean stopped;
        private final AtomicBoolean isAiRunning = new AtomicBoolean();
        private final AtomicInteger completedDepthFrames = new AtomicInteger();
        private final AtomicBoolean isAiResultHandlingRunning = new AtomicBoolean();
        private final AtomicReference<ByteBuffer> latestDepthMap = new AtomicReference<>();
        // Synced mode parks the GL thread on depthReady until the worker publishes a map;
        // depthWaiting tells the worker that no redraw request is needed for it.
        private final Object depthReady = new Object();
        private final AtomicBoolean depthWaiting = new AtomicBoolean();
        private GpuDelegate gpuDelegate;
        private volatile Interpreter tflite;
        private NnApiDelegate nnApiDelegate;
        private ByteBuffer tfliteInputBuffer;
        private volatile String backend = "CPU " + depthModel.shortName();
        private int modelInputWidth = depthModel.inputSize;
        private int modelInputHeight = depthModel.inputSize;
        private boolean floatInput, floatOutput;
        private BlockingQueue<InferenceResult> filledOutputBuffers;
        private BlockingQueue<ByteBuffer> freeInputBuffers;
        private BlockingQueue<ByteBuffer> freeOutputBuffers;
        private BlockingQueue<ByteBuffer> freeSmoothedBuffers;
        private final BlockingQueue<RenderResult> inferenceInputQueue = new ArrayBlockingQueue<>(1);
        private ByteBuffer previousPixelBuffer;

        private void startWorkers() {
            if (stopped || tflite == null) return;
            isAiResultHandlingRunning.set(true);
            executorService.submit(new AiResultHandling());
            isAiRunning.set(true);
            inferenceTask = inferenceExecutor.submit(new AiTask());
        }

        private synchronized void stop() {
            if (stopped) return;
            stopped = true;
            if (inferenceTask != null) inferenceTask.cancel(true);
            // Capture the session, not the renderer's current fields. This also
            // closes a model whose native constructor finishes after cancellation.
            inferenceExecutor.execute(this::closeTfLite);
            inferenceExecutor.shutdown();
            executorService.shutdownNow();
        }

        private void requestDepthRender() {
            if (!stopped && depthSession == this) host.requestRender();
        }

        private void closeTfLite() {
            final Interpreter closingInterpreter = tflite;
            final GpuDelegate closingGpuDelegate = gpuDelegate;
            final NnApiDelegate closingNnApiDelegate = nnApiDelegate;
            tflite = null;
            gpuDelegate = null;
            nnApiDelegate = null;
            closeModel(closingInterpreter, closingGpuDelegate, closingNnApiDelegate);
        }

        private void initializeTfLite() {
            try {
                GpuDelegate.Options gpuOptions = new GpuDelegate.Options();
                gpuOptions.setQuantizedModelsAllowed(true);
                gpuOptions.setPrecisionLossAllowed(true);
                gpuOptions.setInferencePreference(GpuDelegateFactory.Options.INFERENCE_PREFERENCE_SUSTAINED_SPEED);
                gpuDelegate = new GpuDelegate(gpuOptions);
                tflite = new Interpreter(loadModelFile(), new Interpreter.Options().addDelegate(gpuDelegate));
                backend = "GPU " + depthModel.shortName();
                return;
            } catch (Exception e) {
                LimeLog.info("GPU delegate unavailable: " + e.getMessage());
                closeTfLite();
            }
            try {
                nnApiDelegate = new NnApiDelegate();
                tflite = new Interpreter(loadModelFile(), new Interpreter.Options().addDelegate(nnApiDelegate));
                backend = "NNAPI " + depthModel.shortName();
            } catch (Exception e) {
                LimeLog.info("NNAPI delegate unavailable: " + e.getMessage());
                reinitializeTfLiteOnCpu();
            }
        }

        private void reinitializeTfLiteOnCpu() {
            closeTfLite();
            try {
                tflite = new Interpreter(loadModelFile(), new Interpreter.Options().setNumThreads(4));
                backend = "CPU " + depthModel.shortName();
            } catch (Exception e) {
                LimeLog.severe("Failed to initialize the depth model on CPU: " + e.getMessage());
            }
        }

        private void adoptTensorLayout() {
            if (tflite == null) {
                return;
            }
            try {
                Tensor input = tflite.getInputTensor(0);
                Tensor output = tflite.getOutputTensor(0);
                int[] shape = input.shape();
                if (shape.length == 4 && shape[3] == 3) {          // NHWC, the layout all supported models use
                    modelInputHeight = shape[1];
                    modelInputWidth = shape[2];
                } else {
                    LimeLog.warning("Unexpected depth model input shape " + Arrays.toString(shape)
                            + "; keeping " + modelInputWidth + "x" + modelInputHeight);
                }
                floatInput = input.dataType() == DataType.FLOAT32;
                floatOutput = output.dataType() == DataType.FLOAT32;
                LimeLog.info("Depth model " + depthModel.fileName + ": input " + Arrays.toString(shape) + " "
                        + input.dataType() + ", output " + Arrays.toString(output.shape()) + " " + output.dataType());
            } catch (Exception e) {
                LimeLog.warning("Could not read the depth model's tensor layout: " + e.getMessage());
            }
        }

        private void initBuffer() {
            if (tflite != null) {
                int inputSize = modelInputHeight * modelInputWidth * 3 * (floatInput ? 4 : 1);
                tfliteInputBuffer = ByteBuffer.allocateDirect(inputSize).order(ByteOrder.nativeOrder());
                int outputSize = modelInputHeight * modelInputWidth * (floatOutput ? 4 : 1);
                freeOutputBuffers = new ArrayBlockingQueue<>(NUM_BUFFERS);
                filledOutputBuffers = new ArrayBlockingQueue<>(NUM_BUFFERS);
                for (int i = 0; i < NUM_BUFFERS; i++) {
                    freeOutputBuffers.offer(ByteBuffer.allocateDirect(outputSize).order(ByteOrder.nativeOrder()));
                }
            }
            int mapSize = modelInputWidth * modelInputHeight;
            freeSmoothedBuffers = new ArrayBlockingQueue<>(NUM_SMOOTHED_BUFFERS);
            for (int i = 0; i < NUM_SMOOTHED_BUFFERS; i++) {
                freeSmoothedBuffers.offer(ByteBuffer.allocateDirect(mapSize).order(ByteOrder.nativeOrder()));
            }
            previousPixelBuffer = ByteBuffer.allocateDirect(mapSize * 4).order(ByteOrder.nativeOrder());
            freeInputBuffers = new ArrayBlockingQueue<>(NUM_INPUT_BUFFERS);
            for (int i = 0; i < NUM_INPUT_BUFFERS; i++) {
                freeInputBuffers.offer(ByteBuffer.allocateDirect(mapSize * 4).order(ByteOrder.nativeOrder()));
            }
        }

        private class AiTask implements Runnable {

            private ByteBuffer previousRawMap = null;
            private ByteBuffer previousInferencePixels;

            @Override
            public void run() {
                while (!stopped && !Thread.currentThread().isInterrupted()) {
                    ByteBuffer pixelBuffer = null;
                    ByteBuffer outputBuffer = null;
                    double difference = 0.0f;
                    long startTime = System.nanoTime();
                    long waitTime = System.nanoTime();
                    long aiTime = System.nanoTime();
                    long aiTime_end = System.nanoTime();
                    try {
                        if (tflite == null) break;
                        RenderResult result = inferenceInputQueue.take();
                        pixelBuffer = result.pixelBuffer;
                        // Compare against the image that produced the cached map, not
                        // the preceding display frame. Small changes must accumulate,
                        // and frames skipped by a full queue must not advance the cache.
                        difference = hasSceneChangedFast(pixelBuffer, previousInferencePixels, modelInputWidth);
                        outputBuffer = freeOutputBuffers.take();
                        waitTime = System.nanoTime();
                        outputBuffer.rewind();

                        if (difference > ON_DRAW_CHANGE_TRESHOLD || previousRawMap == null) {
                            tfliteInputBuffer.rewind();
                            pixelBuffer.rewind();

                            if (floatInput) {
                                convertRgbaToFloatRgb(pixelBuffer, tfliteInputBuffer, modelInputWidth, modelInputHeight);
                            } else {
                                convertRgbaToRgb(pixelBuffer, tfliteInputBuffer, modelInputWidth, modelInputHeight);
                            }

                            aiTime = System.nanoTime();
                            if (depthModel == DepthModel.MIDAS_V2_256) {
                                // MiDaS-specific pre-processing (reflects the top/bottom bands so letterbox
                                // bars do not read as near planes); it also performs the vertical flip that
                                // the glReadPixels row order needs, which the float path does itself.
                                ReflectivePaddingInt8Minimal.applyReflectedPadding(tfliteInputBuffer);
                            }
                            tfliteInputBuffer.rewind();
                            outputBuffer.rewind();
                            tflite.run(tfliteInputBuffer, outputBuffer);
                            if (previousInferencePixels == null) {
                                previousInferencePixels = ByteBuffer.allocateDirect(pixelBuffer.capacity());
                            }
                            previousInferencePixels.clear();
                            pixelBuffer.rewind();
                            previousInferencePixels.put(pixelBuffer);
                            pixelBuffer.rewind();
                            if (previousRawMap == null) {
                                previousRawMap = ByteBuffer.allocateDirect(outputBuffer.capacity());
                            }
                            previousRawMap.clear();
                            outputBuffer.rewind();
                            previousRawMap.put(outputBuffer);
                            previousRawMap.rewind();
                        } else {
                            outputBuffer.clear();
                            previousRawMap.rewind();
                            outputBuffer.put(previousRawMap);
                            outputBuffer.rewind();
                        }
                        completedDepthFrames.incrementAndGet();
                        aiTime_end = System.nanoTime();
                        filledOutputBuffers.put(new InferenceResult(pixelBuffer, outputBuffer));
                        pixelBuffer = null;
                        outputBuffer = null;
                    } catch (InterruptedException e) {
                        LimeLog.severe("AI inference failed: " + e.getMessage());
                        Thread.currentThread().interrupt();
                    } catch (Exception e) {
                        LimeLog.severe("AI inference failed: " + e.getMessage());
                        if (gpuDelegate != null || nnApiDelegate != null) {
                            reinitializeTfLiteOnCpu();
                            previousRawMap = null;
                        } else {
                            break; // A broken CPU model must not spin and exhaust the pools.
                        }
                    } finally {
                        long duration = (System.nanoTime() - startTime) / 1_000_000;
                        long waitTimeText = (waitTime - startTime) / 1_000_000;
                        long aitimeText = (aiTime_end - aiTime) / 1_000_000;
                        if (pixelBuffer != null) freeInputBuffers.offer(pixelBuffer);
                        if (outputBuffer != null) freeOutputBuffers.offer(outputBuffer);
                        if (Boolean.TRUE.equals(isDebugMode)) Log.d("Stereo3DRenderer", "CalculateTime AiDepthMap: " + duration + " ms " + filledOutputBuffers.remainingCapacity() + " " + waitTimeText + " ms" + "aitime: " + aitimeText);
                    }
                }
                isAiRunning.set(false);
            }
        }

        private class AiResultHandling implements Runnable {

            private static final double IMAGE_DIFFERENCE_MULTIPLIER = 100.0;
            private static final double MAX_SMOOTHING_FACTOR = 1;

            private static final double MIN_SMOOTHING_FACTOR = 0.005;

            // --- Member Fields ---
            private final byte[] processedDataArray = new byte[modelInputWidth * modelInputHeight];
            private Mat previousSmoothedMat;
            private boolean isFirstFrame = true;

            @Override
            public void run() {
                try (DepthFrameDifference frameDifference = new DepthFrameDifference(modelInputWidth, modelInputHeight)) {
                    while (!stopped && !Thread.currentThread().isInterrupted()) {
                        ByteBuffer resultBuffer = null;
                        InferenceResult result = null;
                        long startTime = System.nanoTime();
                        long waitTime = System.nanoTime();
                        Mat rawMat = null;
                        Mat processedMat = null;
                        Mat diff = null, validMask = null, blended = null, inverseMask = null;
                        try {
                            result = filledOutputBuffers.take();
                            resultBuffer = freeSmoothedBuffers.take();
                            waitTime = System.nanoTime();

                            InferenceResult intermediate;
                            while ((intermediate = filledOutputBuffers.poll()) != null) {
                                freeInputBuffers.offer(result.pixelBuffer);
                                freeOutputBuffers.offer(result.rawDepthBuffer);
                                result = intermediate;
                            }
                            ByteBuffer rawDepthBuffer = result.rawDepthBuffer;
                            ByteBuffer currentPixelBuffer = result.pixelBuffer;

                            currentPixelBuffer.rewind();
                            double imageDifference = frameDifference.compare(currentPixelBuffer, previousPixelBuffer) * IMAGE_DIFFERENCE_MULTIPLIER;

                            rawDepthBuffer.rewind();
                            rawMat = new Mat(modelInputHeight, modelInputWidth, floatOutput ? CvType.CV_32FC1 : CvType.CV_8UC1, rawDepthBuffer);
                            processedMat = new Mat();
                            // Min-max normalise to 8 bit; the rest of the pipeline is 8-bit whatever the model emits
                            Core.normalize(rawMat, processedMat, 0, 255, Core.NORM_MINMAX, CvType.CV_8U);

                            if (isFirstFrame) {
                                previousSmoothedMat = processedMat.clone();
                                isFirstFrame = false;
                            }

                            double smoothing = (imageDifference * 10) / (Math.max(1.0f, threeDFps) * 3);
                            smoothing = Math.min(smoothing, MAX_SMOOTHING_FACTOR);
                            smoothing = Math.max(smoothing, MIN_SMOOTHING_FACTOR);
                            diff = new Mat();
                            Core.absdiff(processedMat, previousSmoothedMat, diff);
                            Core.MinMaxLocResult mmr = Core.minMaxLoc(diff);
                            double thresholdValue = Math.max(1, mmr.maxVal * ((1.0 - smoothing)) * 0.1);
                            validMask = new Mat();
                            Imgproc.threshold(diff, validMask, thresholdValue, 255, Imgproc.THRESH_BINARY_INV);
                            processedMat.copyTo(previousSmoothedMat, validMask);
                            blended = new Mat();
                            Core.addWeighted(processedMat, smoothing, previousSmoothedMat, 1.0 - smoothing, 0.0, blended);
                            inverseMask = new Mat();
                            Core.bitwise_not(validMask, inverseMask);
                            blended.copyTo(previousSmoothedMat, inverseMask);
                            previousSmoothedMat.get(0, 0, processedDataArray);
                            // Straight into the 8-bit map buffer: the raw buffer may be float32 and 4x larger
                            resultBuffer.clear();
                            resultBuffer.put(processedDataArray);
                            resultBuffer.rewind();
                            // Transfer ownership to the GL consumer. Only an unpublished
                            // superseded map may return to the producer's pool here.
                            ByteBuffer superseded = latestDepthMap.getAndSet(resultBuffer);
                            resultBuffer = null;
                            if (superseded != null) freeSmoothedBuffers.offer(superseded);
                            synchronized (depthReady) {
                                depthReady.notifyAll();
                            }
                            // A GL thread parked in the synced-mode wait consumes this map in
                            // its current draw; a second request would draw the same frame twice.
                            if (!depthWaiting.get()) requestDepthRender();

                            previousPixelBuffer.rewind();
                            previousPixelBuffer.put(currentPixelBuffer);
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                        } catch (Exception e) {
                            LimeLog.severe("AI exception " + e.getMessage());
                        } finally {
                            if (diff != null) diff.release();
                            if (validMask != null) validMask.release();
                            if (blended != null) blended.release();
                            if (inverseMask != null) inverseMask.release();
                            if (rawMat != null) {
                                rawMat.release();
                            }
                            if (processedMat != null) {
                                processedMat.release();
                            }
                            if (resultBuffer != null) {
                                freeSmoothedBuffers.offer(resultBuffer);
                            }
                            if (result != null) {
                                freeInputBuffers.offer(result.pixelBuffer);
                                freeOutputBuffers.offer(result.rawDepthBuffer);
                            }
                            long duration = (System.nanoTime() - startTime) / 1_000_000;
                            long waitTimeText = (waitTime - startTime) / 1_000_000;
                            if (Boolean.TRUE.equals(isDebugMode)) Log.d("Stereo3DRenderer", "CalculateTime AiResult:    " + duration + " ms" + " " + freeOutputBuffers.remainingCapacity() + " " + waitTimeText + " ms ");
                        }
                    }
                } finally {
                    if (previousSmoothedMat != null) previousSmoothedMat.release();
                    isAiResultHandlingRunning.set(false);
                }
            }
        }

    }
}
