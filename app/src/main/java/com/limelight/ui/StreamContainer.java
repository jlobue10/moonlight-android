package com.limelight.ui;

import android.content.Context;
import android.graphics.PixelFormat;
import android.opengl.GLSurfaceView;
import android.util.AttributeSet;
import android.view.KeyEvent;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.inputmethod.BaseInputConnection;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.widget.FrameLayout;
import android.widget.Toast;

import com.limelight.Game;
import com.limelight.LimeLog;
import com.limelight.R;
import com.limelight.preferences.PreferenceConfiguration;
import com.limelight.utils.Stereo3DRenderer;
import com.limelight.xr.CanvasTestPattern;
import com.limelight.xr.SurfaceGlThread;
import com.limelight.xr.XrStereoPresenter;

/**
 * A container that manages different stream display modes and now correctly
 * handles all input callbacks, aspect ratio scaling, and a robust surface lifecycle.
 * It uses SurfaceView for 2D and GLSurfaceView for both 3D modes.
 */
public class StreamContainer extends FrameLayout implements SurfaceHolder.Callback, Stereo3DRenderer.OnSurfaceReadyListener {

    public interface InputCallbacks {
        boolean handleKeyUp(KeyEvent event);
        boolean handleKeyDown(KeyEvent event);
        boolean handleCommitText(CharSequence text);
        boolean handleDeleteSurroundingText(int beforeLength, int afterLength);
        boolean handleFocusChange(boolean hasWindowFocus);
    }

    public enum StreamMode {
        MODE_2D,
        MODE_AI_3D,
        MODE_AI_3D_MOVIE
    }

    private Game game;
    private PreferenceConfiguration prefConfig;
    private Stereo3DRenderer mStereoRenderer;

    // Android XR: the SBS frame goes to a stereo SurfaceEntity instead of a GLSurfaceView
    private XrStereoPresenter xrPresenter;
    private SurfaceGlThread xrGlThread;
    private CanvasTestPattern xrTestPattern;
    private boolean xrStereo = false;
    private volatile boolean destroyed;

    private SurfaceView mSurfaceView;
    private Surface mCurrentSurface;
    private Runnable onSurfaceAvailable;
    private StreamMode renderMode = null;
    private InputCallbacks mInputCallbacks;
    private boolean commitTextEnabled = false;

    private double desiredAspectRatio;
    private boolean fillDisplay = false;

    private boolean isSurfaceReady = false;

    public StreamContainer(Context context, AttributeSet attrs) {
        super(context, attrs);

        setFocusable(true);
        setFocusableInTouchMode(true);
    }

    public void init(Game game, PreferenceConfiguration prefConfig) {
        if (this.game != null) {
            return;
        }

        this.game = game;
        this.prefConfig = prefConfig;
        this.renderMode = mapIntToStreamMode(prefConfig.renderMode);

        isSurfaceReady = false;
        mCurrentSurface = null;

        Context context = getContext();
        LayoutParams childParams = new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT);

        // Always craete a surface view as a Workaround for the sizing issue of GLSurfaceView
        mSurfaceView = new SurfaceView(context);
        addView(mSurfaceView, childParams);

        if (renderMode != StreamMode.MODE_2D) {
            // Only touch Stereo3DRenderer when 3D is actually in use: its static initializer loads
            // OpenCV (a ~23 MB native library), which 2D streams must not pay for.
            Stereo3DRenderer.isMovieMode = renderMode == StreamMode.MODE_AI_3D_MOVIE;

            if (prefConfig.xrStereo && XrStereoPresenter.isSupported(context)) {
                // Headset: true stereo via a SceneCore SurfaceEntity in Full Space. The plain
                // SurfaceView above stays as the (black) app window that keeps input focus.
                startXrStereo();
            }
            if (!xrStereo) {
                createFlatStereoView();
            }
        }

        mSurfaceView.getHolder().addCallback(this);
        if (mSurfaceView.getHolder().getSurface() != null && mSurfaceView.getHolder().getSurface().isValid()) {
            surfaceChanged(mSurfaceView.getHolder(), PixelFormat.RGBA_8888, mSurfaceView.getWidth(), mSurfaceView.getHeight());
        }
    }

    /**
     * True while Android XR is moving the activity to Full Space at our request. The activity is
     * stopped and restarted during that transition; Game must not treat that stop as "stream over".
     */
    public boolean isXrSpaceTransitionInProgress() {
        return xrStereo && xrPresenter != null && xrPresenter.isWaitingForFullSpace();
    }

    /** Flat displays: a GLSurfaceView shows the side-by-side frame as-is. */
    private void createFlatStereoView() {
        Context context = getContext();
        GLSurfaceView glSurfaceView = new GLSurfaceView(context);
        glSurfaceView.setEGLContextClientVersion(3);
        mStereoRenderer = new Stereo3DRenderer(Stereo3DRenderer.hostFor(glSurfaceView), this, context, prefConfig);
        glSurfaceView.setRenderer(mStereoRenderer);
        glSurfaceView.setRenderMode(GLSurfaceView.RENDERMODE_WHEN_DIRTY);
        mSurfaceView = glSurfaceView;
        addView(mSurfaceView, new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT));
    }

    /**
     * Android XR: ask for a side-by-side stereo SurfaceEntity sized for one eye's aspect and
     * render the SBS frame into it from an EGL thread. Failure (no XR runtime, Full Space refused)
     * falls back to the flat view; a failure that happens synchronously inside start() leaves
     * xrStereo false so init() creates the flat view, a later one creates it from the callback.
     */
    private void startXrStereo() {
        xrStereo = true;
        final int frameWidth = Math.max(2, prefConfig.width) * 2;   // full resolution per eye
        final int frameHeight = Math.max(2, prefConfig.height);
        xrPresenter = new XrStereoPresenter(game);
        final boolean canvasTest = "canvas".equals(prefConfig.xrStereoLayout);
        xrPresenter.setLayout("mono".equals(prefConfig.xrStereoLayout) || canvasTest
                ? XrStereoPresenter.LAYOUT_MONO : XrStereoPresenter.LAYOUT_SBS);
        xrPresenter.start(frameWidth, frameHeight, prefConfig.xrScreenWidthMeters, prefConfig.xrHideMainPanel,
                prefConfig.xrMovableScreen, new XrStereoPresenter.Listener() {
            @Override
            public void onStereoSurfaceReady(Surface surface, int widthPx, int heightPx) {
                if (destroyed) return;
                if (xrGlThread != null || xrTestPattern != null) {
                    return;
                }
                if (canvasTest) {
                    // Diagnostic: paint the entity with Canvas instead of OpenGL and run the stream in
                    // the flat view, so the user can compare the two producers on the same entity.
                    xrTestPattern = new CanvasTestPattern(surface, widthPx, heightPx);
                    xrTestPattern.start();
                    LimeLog.info("XR stereo: canvas test pattern on the SurfaceEntity; stream renders flat");
                    xrStereo = false;
                    fallBackToFlatStereo("canvas test pattern mode");
                    return;
                }
                xrGlThread = new SurfaceGlThread(surface, widthPx, heightPx);
                mStereoRenderer = new Stereo3DRenderer(xrGlThread, StreamContainer.this, getContext(), prefConfig);
                xrGlThread.setRenderer(mStereoRenderer);
                xrGlThread.setInitListener(reason -> post(() -> onXrGlFailed(reason)));
                xrGlThread.start();
                LimeLog.info("XR stereo: rendering " + widthPx + "x" + heightPx + " side-by-side into the SurfaceEntity");
            }

            @Override
            public void onStereoUnavailable(String reason) {
                if (destroyed) return;
                LimeLog.warning("XR stereo unavailable (" + reason + "); showing the side-by-side frame flat");
                xrStereo = false;
                xrPresenter = null;
                if (xrGlThread != null) {
                    return;   // already rendering into the entity; nothing to fall back from
                }
                // A failure inside start() is synchronous: init() sees xrStereo == false right after
                // and builds the flat view itself. A later failure (Full Space never granted, entity
                // creation failed) has to build it here; fallBackToFlatStereo() is idempotent.
                post(() -> fallBackToFlatStereo(reason));
            }
        });
    }

    /** True while the stream is shown on the Android XR stereo screen. */
    public boolean isXrStereoActive() {
        return xrStereo && xrPresenter != null && xrPresenter.isShowing();
    }

    /** Brings the Android XR stereo screen back in front of the viewer (no-op elsewhere). */
    public void recenterXrScreen() {
        if (isXrStereoActive()) {
            xrPresenter.recenter();
        }
    }

    /** The EGL thread could not start on the entity's surface: tear the XR path down and go flat. */
    private void onXrGlFailed(String reason) {
        if (destroyed) return;
        LimeLog.warning("XR stereo GL thread failed (" + reason + "); showing the side-by-side frame flat");
        // onSurfaceCreated may fail after allocating the model or starting workers.
        if (mStereoRenderer != null) {
            mStereoRenderer.onSurfaceDestroyed();
            mStereoRenderer = null;
        }
        if (xrGlThread != null) {
            xrGlThread.shutdown();
            xrGlThread = null;
        }
        if (xrPresenter != null) {
            xrPresenter.stop();
            xrPresenter = null;
        }
        xrStereo = false;
        fallBackToFlatStereo(reason);
    }

    /** Builds the flat GLSurfaceView path if it does not exist yet and tells the user why. */
    private void fallBackToFlatStereo(String reason) {
        if (destroyed) return;
        try {
            Toast.makeText(getContext(), getContext().getString(R.string.xr_stereo_fallback_toast, reason), Toast.LENGTH_LONG).show();
        } catch (RuntimeException ignored) {
            // no window yet; the log line is enough
        }
        if (!(mSurfaceView instanceof GLSurfaceView) && mStereoRenderer == null) {
            createFlatStereoView();
            mSurfaceView.getHolder().addCallback(this);
        }
    }

    // --- Aspect Ratio and Scaling Logic ---
    public void setDesiredAspectRatio(double aspectRatio) {
        this.desiredAspectRatio = aspectRatio;
        requestLayout();
    }

    public void setFillDisplay(boolean fillDisplay) {
        this.fillDisplay = fillDisplay;
        requestLayout();
    }

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        if (renderMode != StreamMode.MODE_2D) {
            super.onMeasure(widthMeasureSpec, heightMeasureSpec);
            return;
        }
        if (desiredAspectRatio == 0) {
            super.onMeasure(widthMeasureSpec, heightMeasureSpec);
            return;
        }

        int widthSize = MeasureSpec.getSize(widthMeasureSpec);
        int heightSize = MeasureSpec.getSize(heightMeasureSpec);
        int measuredHeight, measuredWidth;

        if (fillDisplay) {
            if (widthSize < heightSize * desiredAspectRatio) {
                measuredHeight = heightSize;
                measuredWidth = (int)(heightSize * desiredAspectRatio);
            } else {
                measuredWidth = widthSize;
                measuredHeight = (int)(widthSize / desiredAspectRatio);
            }
        } else {
            if (widthSize > heightSize * desiredAspectRatio) {
                measuredHeight = heightSize;
                measuredWidth = (int)(measuredHeight * desiredAspectRatio);
            } else {
                measuredWidth = widthSize;
                measuredHeight = (int)(measuredWidth / desiredAspectRatio);
            }
        }

        setMeasuredDimension(measuredWidth, measuredHeight);
        int childWidthMeasureSpec = MeasureSpec.makeMeasureSpec(measuredWidth, MeasureSpec.EXACTLY);
        int childHeightMeasureSpec = MeasureSpec.makeMeasureSpec(measuredHeight, MeasureSpec.EXACTLY);
        measureChildren(childWidthMeasureSpec, childHeightMeasureSpec);
    }

    public void setInputCallbacks(InputCallbacks callbacks) {
        this.mInputCallbacks = callbacks;
    }

    public void setCommitTextEnabled(boolean enabled) {
        this.commitTextEnabled = enabled;
    }

    @Override
    public boolean onKeyPreIme(int keyCode, KeyEvent event) {
        if (mInputCallbacks != null) {
            if (event.getAction() == KeyEvent.ACTION_DOWN) {
                if (mInputCallbacks.handleKeyDown(event)) return true;
            } else if (event.getAction() == KeyEvent.ACTION_UP) {
                if (mInputCallbacks.handleKeyUp(event)) return true;
            }
        }
        return super.onKeyPreIme(keyCode, event);
    }

    @Override
    public void onWindowFocusChanged(boolean hasWindowFocus) {
        super.onWindowFocusChanged(hasWindowFocus);
        if (mInputCallbacks != null) {
            mInputCallbacks.handleFocusChange(hasWindowFocus);
        }
    }

    @Override
    public boolean onCheckIsTextEditor() {
        return commitTextEnabled || super.onCheckIsTextEditor();
    }

    @Override
    public InputConnection onCreateInputConnection(EditorInfo outAttrs) {
        if (!commitTextEnabled) {
            return super.onCreateInputConnection(outAttrs);
        }
        outAttrs.inputType = android.text.InputType.TYPE_CLASS_TEXT;
        outAttrs.imeOptions = EditorInfo.IME_FLAG_NO_EXTRACT_UI;
        return new BaseInputConnection(this, false) {
            @Override
            public boolean commitText(CharSequence text, int newCursorPosition) {
                return mInputCallbacks != null && mInputCallbacks.handleCommitText(text) || super.commitText(text, newCursorPosition);
            }
            @Override
            public boolean deleteSurroundingText(int beforeLength, int afterLength) {
                return mInputCallbacks != null && mInputCallbacks.handleDeleteSurroundingText(beforeLength, afterLength) || super.deleteSurroundingText(beforeLength, afterLength);
            }
        };
    }

    // The stereo surface becomes ready on the GL thread while onCreate installs the
    // callback on the main thread; both sides must observe the other's write or the
    // stream never starts (or starts twice).
    private final Object surfaceReadyLock = new Object();

    public void setOnSurfaceAvailable(Runnable callback) {
        Runnable ready = null;
        synchronized (surfaceReadyLock) {
            this.onSurfaceAvailable = callback;
            if (isSurfaceReady) {
                ready = callback;
            }
        }
        if (ready != null) {
            ready.run();
        }
    }

    public Surface getSurface() {
        return mCurrentSurface;
    }

    public SurfaceView getSurfaceView() {
        return mSurfaceView;
    }

    public StreamMode mapIntToStreamMode(int modeIndex) {
        StreamContainer.StreamMode[] modes = StreamContainer.StreamMode.values();
        if (modeIndex >= 0 && modeIndex < modes.length) {
            return modes[modeIndex];
        } else {
            return StreamContainer.StreamMode.MODE_2D;
        }
    }

    private void notifySurfaceReady() {
        Runnable callback;
        synchronized (surfaceReadyLock) {
            isSurfaceReady = true;
            callback = onSurfaceAvailable;
        }
        if (callback != null) {
            callback.run();
        }
    }

    @Override
    public void surfaceCreated(SurfaceHolder holder) {
        game.surfaceCreated(holder);
    }
    @Override
    public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {
        if (renderMode == StreamMode.MODE_2D && width > 0 && height > 0) {
            mCurrentSurface = holder.getSurface();
            notifySurfaceReady();
        }

        game.surfaceChanged(holder, format, width, height);
    }
    @Override
    public void surfaceDestroyed(SurfaceHolder holder) {
        if (renderMode == StreamMode.MODE_2D) {
            isSurfaceReady = false;
            mCurrentSurface = null;
        } else if (mStereoRenderer != null && !xrStereo) {
            // The XR stereo renderer does not live in this View's surface; onDestroy() stops it.
            mStereoRenderer.onSurfaceDestroyed();
        }

        if (xrStereo) {
            // In stereo mode this View is only the flat app window; the stream renders into the
            // SurfaceEntity. Android XR may recreate the window's surface (panel resize, Full Space
            // layout), and Game.surfaceDestroyed() would end the stream for that. Ignore it.
            LimeLog.info("XR stereo: app window surface destroyed; stream continues on the SurfaceEntity");
            return;
        }

        game.surfaceDestroyed(holder);
    }

    @Override
    public void onStereo3DSurfaceReady(Surface surface) {
        if (!destroyed && renderMode != StreamMode.MODE_2D) {
            mCurrentSurface = surface;
            notifySurfaceReady();
        }
    }

    public void onDestroy() {
        if (destroyed) return;
        destroyed = true;
        isSurfaceReady = false;
        mCurrentSurface = null;
        onSurfaceAvailable = null;
        if (mStereoRenderer != null) {
            mStereoRenderer.onSurfaceDestroyed();   // queues its GL cleanup on the host thread
            mStereoRenderer = null;
        }
        if (xrGlThread != null) {
            xrGlThread.shutdown();                  // drains that cleanup, then releases EGL
            xrGlThread = null;
        }
        if (xrTestPattern != null) {
            xrTestPattern.shutdown();
            xrTestPattern = null;
        }
        if (xrPresenter != null) {
            xrPresenter.stop();                     // disposes the entity, back to Home Space
            xrPresenter = null;
        }
        xrStereo = false;
    }
}
