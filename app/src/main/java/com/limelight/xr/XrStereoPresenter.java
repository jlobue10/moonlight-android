package com.limelight.xr;

import android.app.Activity;
import android.content.Context;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.view.Surface;

import androidx.xr.runtime.Session;
import androidx.xr.runtime.SessionCreateResult;
import androidx.xr.runtime.SessionCreateSuccess;
import androidx.xr.runtime.math.FloatSize2d;
import androidx.xr.runtime.math.IntSize2d;
import androidx.xr.runtime.math.Pose;
import androidx.xr.runtime.math.Vector3;
import androidx.xr.scenecore.Entity;
import androidx.xr.scenecore.Scene;
import androidx.xr.scenecore.SessionExt;
import androidx.xr.scenecore.Space;
import androidx.xr.scenecore.SpatialCapability;
import androidx.xr.scenecore.SpatialModeChangeEvent;
import androidx.xr.scenecore.SurfaceEntity;

import com.limelight.LimeLog;

import java.util.Set;
import java.util.function.Consumer;

/**
 * Presents a side-by-side stereo frame as a true stereoscopic floating screen on Android XR.
 *
 * A flat Android window on a headset is a 2D panel, so a side-by-side frame drawn into it is just
 * two pictures. Jetpack XR SceneCore can instead show a {@link SurfaceEntity} whose
 * {@link SurfaceEntity.StereoMode#SIDE_BY_SIDE} mode sends the left half of each frame to the left
 * eye and the right half to the right eye. The entity is only rendered in Full Space, so this class
 * requests it, waits for the {@link SpatialCapability#SPATIAL_3D_CONTENT} capability, creates the
 * entity with a quad whose aspect matches one eye's picture, pins the surface's pixel size, and
 * hands the Surface to the caller (which renders into it with {@link SurfaceGlThread}).
 *
 * Everything that touches SceneCore lives here, behind {@link #isSupported(Context)}, so the rest
 * of the app never loads these classes on phones, TVs or other non-XR devices. All methods are
 * main-thread only.
 */
public final class XrStereoPresenter {

    public interface Listener {
        /** The stereo surface exists and is pinned to widthPx x heightPx (the full side-by-side frame). */
        void onStereoSurfaceReady(Surface surface, int widthPx, int heightPx);

        /** Stereo is not possible on this device/session; the caller should fall back to the flat view. */
        void onStereoUnavailable(String reason);
    }

    /** Android XR declares this feature; XR packages exist from API 34. */
    private static final String FEATURE_XR_SPATIAL = "android.software.xr.api.spatial";
    /** Distance of the floating screen from the activity space origin, in metres. */
    private static final float SCREEN_DISTANCE_METERS = 2.0f;
    /** How long to wait for Full Space (the SPATIAL_3D_CONTENT capability) before giving up. */
    private static final long FULL_SPACE_TIMEOUT_MS = 6000;

    private final Activity activity;
    private Session session;
    private Scene scene;
    private SurfaceEntity entity;
    private Listener listener;
    private Consumer<Set<SpatialCapability>> capabilitiesListener;
    private Consumer<SpatialModeChangeEvent> modeListener;
    /** How the frame is presented: SIDE_BY_SIDE stereo (normal), or MONO for A/B testing. */
    public static final int LAYOUT_SBS = 0;
    public static final int LAYOUT_MONO = 1;
    private int layout = LAYOUT_SBS;
    private int frameWidthPx;
    private int frameHeightPx;
    private float screenWidthMeters;
    private boolean hideMainPanel;
    private boolean mainPanelHidden;
    private volatile boolean waitingForFullSpace;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Runnable fullSpaceTimeout = () -> {
        if (entity == null && listener != null) {
            fail("Full Space was not granted within " + FULL_SPACE_TIMEOUT_MS + " ms");
        }
    };

    public XrStereoPresenter(Activity activity) {
        this.activity = activity;
    }

    public static boolean isSupported(Context context) {
        return Build.VERSION.SDK_INT >= 34
                && context.getPackageManager().hasSystemFeature(FEATURE_XR_SPATIAL);
    }

    /**
     * @param frameWidthPx  width of the full side-by-side frame (both eyes)
     * @param frameHeightPx height of the frame
     * @param screenWidthMeters physical width of the floating screen
     * @param hideMainPanel hide the activity's flat window while the stereo screen shows
     */
    public void setLayout(int layout) {
        this.layout = layout;
    }

    public void start(int frameWidthPx, int frameHeightPx, float screenWidthMeters, boolean hideMainPanel, Listener listener) {
        this.frameWidthPx = frameWidthPx;
        this.frameHeightPx = frameHeightPx;
        this.screenWidthMeters = screenWidthMeters;
        this.hideMainPanel = hideMainPanel;
        this.listener = listener;
        try {
            SessionCreateResult result = Session.create(activity);
            if (!(result instanceof SessionCreateSuccess)) {
                fail("XR session not available: " + result.getClass().getSimpleName());
                return;
            }
            session = ((SessionCreateSuccess) result).getSession();
            scene = SessionExt.getScene(session);

            if (scene.getSpatialCapabilities().contains(SpatialCapability.SPATIAL_3D_CONTENT)) {
                createEntity();
            } else {
                // Spatial content needs Full Space; the capability arrives once the transition is done.
                capabilitiesListener = caps -> {
                    if (entity == null && caps.contains(SpatialCapability.SPATIAL_3D_CONTENT)) {
                        try {
                            createEntity();
                        } catch (Throwable t) {
                            fail("creating the stereo surface failed: " + t);
                        }
                    }
                };
                scene.addSpatialCapabilitiesChangedListener(capabilitiesListener);
                LimeLog.info("XR stereo: requesting Full Space");
                scene.requestFullSpace();
                waitingForFullSpace = true;
                // Never leave the stream waiting for a surface that may not come.
                mainHandler.postDelayed(fullSpaceTimeout, FULL_SPACE_TIMEOUT_MS);
            }
        } catch (Throwable t) {
            // Includes NoClassDefFoundError/LinkageError when the XR runtime is missing.
            fail(t.toString());
        }
    }

    /** True while the system is moving this activity to Full Space on our request. */
    public boolean isWaitingForFullSpace() {
        return waitingForFullSpace;
    }

    private void createEntity() {
        waitingForFullSpace = false;
        mainHandler.removeCallbacks(fullSpaceTimeout);
        // SBS: the quad has one eye's aspect (each half is stretched over it). MONO (A/B test):
        // the whole side-by-side frame is shown flat, so the quad takes the full frame's aspect.
        boolean mono = layout == LAYOUT_MONO;
        float aspect = mono ? (float) frameWidthPx / frameHeightPx : (frameWidthPx / 2f) / frameHeightPx;
        FloatSize2d extents = new FloatSize2d(screenWidthMeters, screenWidthMeters / aspect);
        Pose pose = new Pose(new Vector3(0f, 0f, -SCREEN_DISTANCE_METERS));
        // THE PARENT MATTERS. SceneCore's SurfaceEntity.create() documents: "parent ... Defaults to
        // null. If null, the entity is created but not attached to the scene graph, meaning it will be
        // invisible." The short overloads used in fork.4–fork.11 did exactly that: the entity received
        // every frame and was never part of the scene (and setPose in the activity space threw
        // "non-AndroidXrEntity parent"). Attach it to the activity space explicitly.
        Entity parent = scene.getActivitySpace();
        entity = SurfaceEntity.create(session, pose, new SurfaceEntity.Shape.Quad(extents),
                mono ? SurfaceEntity.StereoMode.MONO : SurfaceEntity.StereoMode.SIDE_BY_SIDE,
                SurfaceEntity.SuperSampling.NONE, SurfaceEntity.SurfaceProtection.NONE, parent);
        LimeLog.info("XR stereo: entity parented to " + parent + " (parent now " + entity.getParent() + ")");
        entity.setSurfacePixelDimensions(new IntSize2d(frameWidthPx, frameHeightPx));
        Surface probe = entity.getSurface();
        LimeLog.info("XR stereo: entity surface " + probe + " valid=" + (probe != null && probe.isValid())
                + " layout=" + (mono ? "MONO" : "SIDE_BY_SIDE"));

        // A video decoder tags its buffers with a colour space and the entity reads it from there;
        // OpenGL output carries no such tag (dataspace UNKNOWN) and an RGBA alpha channel. Tell the
        // entity explicitly what it is looking at: opaque SDR BT.709 sRGB, full range. (fork.8 showed
        // the quad black while frames were being swapped into it.)
        try {
            entity.setMediaBlendingMode(SurfaceEntity.MediaBlendingMode.OPAQUE);
        } catch (RuntimeException e) {
            LimeLog.warning("XR stereo: blending mode not applied: " + e.getMessage());
        }
        try {
            entity.setContentColorMetadata(new SurfaceEntity.ContentColorMetadata(
                    SurfaceEntity.ContentColorMetadata.ColorSpace.BT709,
                    SurfaceEntity.ContentColorMetadata.ColorTransfer.SRGB,
                    SurfaceEntity.ContentColorMetadata.ColorRange.FULL,
                    0));
        } catch (RuntimeException e) {
            LimeLog.warning("XR stereo: colour metadata not applied: " + e.getMessage());
        }

        // The system's "recommended pose" for Full Space content is logged but NOT applied any more.
        // fork.8–fork.10 on the Galaxy XR: the quad was moved there twice right after creation and was
        // never visible with any producer (GL, Canvas), while our frames were provably fine. A pose at
        // or near the viewer's origin puts a plane through the head, which shows nothing. The screen
        // stays where we put it: SCREEN_DISTANCE_METERS straight ahead of the activity space origin.
        modeListener = event -> {
            Pose p = event.getRecommendedPose();
            if (p != null) {
                LimeLog.info("XR stereo: system recommended pose t=(" + p.getTranslation().getX() + ","
                        + p.getTranslation().getY() + "," + p.getTranslation().getZ() + ") scale="
                        + event.getRecommendedScale() + " (ignored; screen stays at z=-" + SCREEN_DISTANCE_METERS + ")");
            }
        };
        scene.setSpatialModeChangedListener(modeListener);
        Pose placed = entity.getPose(Space.PARENT);
        LimeLog.info("XR stereo: screen placed at t=(" + placed.getTranslation().getX() + ","
                + placed.getTranslation().getY() + "," + placed.getTranslation().getZ() + ") in the parent space");

        if (hideMainPanel && scene.getMainPanelEntity() != null) {
            // setEnabled(false) alone showed no effect on the Galaxy XR (fork.11); also fade it out.
            scene.getMainPanelEntity().setEnabled(false);
            try {
                scene.getMainPanelEntity().setAlpha(0f);
            } catch (RuntimeException e) {
                LimeLog.warning("XR stereo: main panel alpha not applied: " + e.getMessage());
            }
            mainPanelHidden = true;
            LimeLog.info("XR stereo: main panel hidden (enabled=" + scene.getMainPanelEntity().isEnabled() + ")");
        }
        LimeLog.info("XR stereo: " + (mono ? "MONO" : "SIDE_BY_SIDE") + " SurfaceEntity " + frameWidthPx + "x" + frameHeightPx
                + " px on a " + extents.getWidth() + "x" + extents.getHeight() + " m quad");
        listener.onStereoSurfaceReady(entity.getSurface(), frameWidthPx, frameHeightPx);
    }

    private void fail(String reason) {
        LimeLog.warning("XR stereo unavailable: " + reason);
        stop();
        listener.onStereoUnavailable(reason);
    }

    /** Disposes the entity, restores the main panel and returns to Home Space. Safe to call twice. */
    public void stop() {
        waitingForFullSpace = false;
        mainHandler.removeCallbacks(fullSpaceTimeout);
        try {
            if (scene != null) {
                if (capabilitiesListener != null) {
                    scene.removeSpatialCapabilitiesChangedListener(capabilitiesListener);
                    capabilitiesListener = null;
                }
                if (modeListener != null) {
                    scene.clearSpatialModeChangedListener();
                    modeListener = null;
                }
                if (mainPanelHidden && scene.getMainPanelEntity() != null) {
                    scene.getMainPanelEntity().setEnabled(true);
                    try {
                        scene.getMainPanelEntity().setAlpha(1f);
                    } catch (RuntimeException ignored) {
                    }
                    mainPanelHidden = false;
                }
            }
            if (entity != null) {
                entity.dispose();
                entity = null;
            }
            if (scene != null) {
                scene.requestHomeSpace();
            }
        } catch (Throwable t) {
            LimeLog.warning("XR stereo teardown: " + t);
        } finally {
            scene = null;
            session = null;   // lifecycle-bound to the activity; no explicit destroy API
        }
    }
}
