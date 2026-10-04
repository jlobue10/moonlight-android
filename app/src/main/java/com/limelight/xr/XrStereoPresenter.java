package com.limelight.xr;

import android.app.Activity;
import android.content.Context;
import android.os.Build;
import android.view.Surface;

import androidx.xr.runtime.Session;
import androidx.xr.runtime.SessionCreateResult;
import androidx.xr.runtime.SessionCreateSuccess;
import androidx.xr.runtime.math.FloatSize2d;
import androidx.xr.runtime.math.IntSize2d;
import androidx.xr.runtime.math.Pose;
import androidx.xr.runtime.math.Vector3;
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

    private final Activity activity;
    private Session session;
    private Scene scene;
    private SurfaceEntity entity;
    private Listener listener;
    private Consumer<Set<SpatialCapability>> capabilitiesListener;
    private Consumer<SpatialModeChangeEvent> modeListener;
    private int frameWidthPx;
    private int frameHeightPx;
    private float screenWidthMeters;
    private boolean hideMainPanel;
    private boolean mainPanelHidden;

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
            }
        } catch (Throwable t) {
            // Includes NoClassDefFoundError/LinkageError when the XR runtime is missing.
            fail(t.toString());
        }
    }

    private void createEntity() {
        float eyeAspect = (frameWidthPx / 2f) / frameHeightPx;   // one eye's picture
        FloatSize2d extents = new FloatSize2d(screenWidthMeters, screenWidthMeters / eyeAspect);
        Pose pose = new Pose(new Vector3(0f, 0f, -SCREEN_DISTANCE_METERS));
        entity = SurfaceEntity.create(session, pose, new SurfaceEntity.Shape.Quad(extents),
                SurfaceEntity.StereoMode.SIDE_BY_SIDE);
        entity.setSurfacePixelDimensions(new IntSize2d(frameWidthPx, frameHeightPx));

        // When the system (re)enters Full Space it recommends where content should sit.
        modeListener = event -> {
            if (entity != null && event.getRecommendedPose() != null) {
                entity.setPose(event.getRecommendedPose(), Space.ACTIVITY);
            }
        };
        scene.setSpatialModeChangedListener(modeListener);

        if (hideMainPanel && scene.getMainPanelEntity() != null) {
            scene.getMainPanelEntity().setEnabled(false);
            mainPanelHidden = true;
        }
        LimeLog.info("XR stereo: SIDE_BY_SIDE SurfaceEntity " + frameWidthPx + "x" + frameHeightPx
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
