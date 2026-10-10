package com.limelight.xr;

import android.app.Activity;
import android.content.Context;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.view.Surface;

import androidx.xr.runtime.Config;
import androidx.xr.runtime.DeviceTrackingMode;
import androidx.xr.runtime.Session;
import androidx.xr.runtime.SessionConfigureResult;
import androidx.xr.runtime.SessionConfigureSuccess;
import androidx.xr.runtime.SessionCreateResult;
import androidx.xr.runtime.SessionCreateSuccess;
import androidx.xr.arcore.RenderViewpoint;
import androidx.xr.runtime.math.FloatSize2d;
import androidx.xr.runtime.math.FloatSize3d;
import androidx.xr.runtime.math.IntSize2d;
import androidx.xr.runtime.math.Pose;
import androidx.xr.runtime.math.Quaternion;
import androidx.xr.runtime.math.Vector3;
import androidx.xr.scenecore.Entity;
import androidx.xr.scenecore.MovableComponent;
import androidx.xr.scenecore.Scene;
import androidx.xr.scenecore.ScenePose;
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
    private MovableComponent movable;
    private Runnable originListener;
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
    private float screenHeightMeters;
    private boolean hideMainPanel;
    private boolean movableScreen;
    private boolean mainPanelHidden;
    private boolean active;
    private int generation;
    private volatile boolean waitingForFullSpace;
    // Set when we asked for Full Space; stop() only returns to Home Space in that case.
    private boolean requestedFullSpace;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private Runnable fullSpaceTimeout;

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
     * @param movableScreen give the screen the system's move affordance (grab bar) so the user
     *                      can drag it around, like any other panel in Full Space
     */
    public void setLayout(int layout) {
        this.layout = layout;
    }

    public void start(int frameWidthPx, int frameHeightPx, float screenWidthMeters, boolean hideMainPanel,
                      boolean movableScreen, Listener listener) {
        stop();
        active = true;
        final int startedGeneration = ++generation;
        this.frameWidthPx = frameWidthPx;
        this.frameHeightPx = frameHeightPx;
        this.screenWidthMeters = screenWidthMeters;
        this.hideMainPanel = hideMainPanel;
        this.movableScreen = movableScreen;
        this.listener = listener;
        fullSpaceTimeout = () -> {
            if (isCurrentGeneration(startedGeneration) && waitingForFullSpace && entity == null) {
                fail("Full Space was not granted within " + FULL_SPACE_TIMEOUT_MS + " ms");
            }
        };
        try {
            SessionCreateResult result = Session.create(activity);
            if (!(result instanceof SessionCreateSuccess)) {
                fail("XR session not available: " + result.getClass().getSimpleName());
                return;
            }
            session = ((SessionCreateSuccess) result).getSession();
            scene = SessionExt.getScene(session);
            enableDeviceTracking();

            if (scene.getSpatialCapabilities().contains(SpatialCapability.SPATIAL_3D_CONTENT)) {
                createEntity();
            } else {
                // Spatial content needs Full Space; the capability arrives once the transition is done.
                waitingForFullSpace = true;
                capabilitiesListener = caps -> {
                    if (isCurrentGeneration(startedGeneration) && entity == null
                            && caps.contains(SpatialCapability.SPATIAL_3D_CONTENT)) {
                        try {
                            createEntity();
                        } catch (Throwable t) {
                            fail("creating the stereo surface failed: " + t);
                        }
                    }
                };
                scene.addSpatialCapabilitiesChangedListener(capabilitiesListener);
                if (isCurrentGeneration(startedGeneration) && waitingForFullSpace) {
                    LimeLog.info("XR stereo: requesting Full Space");
                    // Register first: requestFullSpace may synchronously grant the capability.
                    mainHandler.postDelayed(fullSpaceTimeout, FULL_SPACE_TIMEOUT_MS);
                    requestedFullSpace = true;
                    scene.requestFullSpace();
                }
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

    private boolean isCurrentGeneration(int expected) {
        return active && generation == expected;
    }

    private void createEntity() {
        if (!active || scene == null || entity != null) return;
        final int createdGeneration = generation;
        waitingForFullSpace = false;
        if (fullSpaceTimeout != null) mainHandler.removeCallbacks(fullSpaceTimeout);
        // SBS: the quad has one eye's aspect (each half is stretched over it). MONO (A/B test):
        // the whole side-by-side frame is shown flat, so the quad takes the full frame's aspect.
        boolean mono = layout == LAYOUT_MONO;
        float aspect = mono ? (float) frameWidthPx / frameHeightPx : (frameWidthPx / 2f) / frameHeightPx;
        screenHeightMeters = screenWidthMeters / aspect;
        FloatSize2d extents = new FloatSize2d(screenWidthMeters, screenHeightMeters);
        Pose pose = defaultPose();
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
            if (!isCurrentGeneration(createdGeneration)) return;
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

        // The screen is a child of the activity space, so it already follows the system recenter
        // gesture (which moves the activity space origin). What it lacked: a way to move it by hand
        // and a way to bring it back in front of the viewer. MovableComponent gives it the system's
        // own grab affordance; recenter() does the rest (menu item, and after a system recenter).
        if (movableScreen) {
            try {
                // scaleInZ=false: the system otherwise grows the screen as it is pushed away
                // so it keeps its angular size, which reads as "it will not move further away".
                movable = MovableComponent.createSystemMovable(session, false);
                movable.setSize(new FloatSize3d(screenWidthMeters, screenHeightMeters, 0.01f));
                if (entity.addComponent(movable)) {
                    LimeLog.info("XR stereo: screen is movable (system move affordance)");
                } else {
                    LimeLog.warning("XR stereo: MovableComponent was not accepted by the entity");
                    movable = null;
                }
            } catch (Throwable t) {
                LimeLog.warning("XR stereo: movable screen not available: " + t);
                movable = null;
            }
        }
        try {
            originListener = () -> {
                if (!isCurrentGeneration(createdGeneration)) return;
                LimeLog.info("XR stereo: activity space origin changed (system recenter); re-placing the screen");
                recenter();
            };
            scene.getActivitySpace().addOriginChangedListener(originListener);
        } catch (Throwable t) {
            LimeLog.warning("XR stereo: origin-changed listener not registered: " + t);
            originListener = null;
        }

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

    /**
     * The session is created with device (head) tracking off, and then every render viewpoint
     * is "not available" (fork.17 on the Galaxy XR). LAST_KNOWN needs no permission and is
     * enough for a recenter.
     */
    private void enableDeviceTracking() {
        try {
            Config config = session.getConfig();
            if (config.getDeviceTracking() != DeviceTrackingMode.DISABLED) {
                LimeLog.info("XR stereo: device tracking already " + config.getDeviceTracking());
                return;
            }
            SessionConfigureResult result = session.configure(
                    config.copy(config.getPlaneTracking(), config.getHandTracking(), DeviceTrackingMode.LAST_KNOWN));
            LimeLog.info("XR stereo: device tracking LAST_KNOWN -> " + result.getClass().getSimpleName());
            if (!(result instanceof SessionConfigureSuccess)) {
                LimeLog.warning("XR stereo: head pose will be unavailable; recenter uses the default pose");
            }
        } catch (Throwable t) {
            LimeLog.warning("XR stereo: device tracking not configured: " + t);
        }
    }

    /** Where the screen goes without any head information: straight ahead of the activity space origin. */
    private static Pose defaultPose() {
        return new Pose(new Vector3(0f, 0f, -SCREEN_DISTANCE_METERS), Quaternion.Identity);
    }

    /** True once the stereo screen exists (so a recenter request has something to move). */
    public boolean isShowing() {
        return entity != null;
    }

    /**
     * Puts the screen {@value #SCREEN_DISTANCE_METERS} m in front of the viewer's current head
     * pose, upright and facing them (yaw only, so a tilted head never tilts the screen). The head
     * pose comes from ARCore's mono render viewpoint, converted from the perception space into
     * the activity space the entity lives in; when that is unavailable (tracking off, old
     * runtime) the screen returns to its default place ahead of the activity space origin.
     * Main thread only.
     */
    public void recenter() {
        if (entity == null || scene == null) {
            return;
        }
        Pose target = defaultPose();
        String how = "default pose";
        try {
            Pose head = headPoseInActivitySpace();
            if (head != null) {
                Vector3 fwd = head.getForward();
                float fx = fwd.getX(), fz = fwd.getZ();
                float len = (float) Math.sqrt(fx * fx + fz * fz);
                if (len > 0.05f) {
                    // Horizontal look direction: ignore pitch so the screen stays level.
                    fx /= len;
                    fz /= len;
                    Vector3 t = head.getTranslation();
                    Vector3 position = new Vector3(t.getX() + fx * SCREEN_DISTANCE_METERS, t.getY(),
                            t.getZ() + fz * SCREEN_DISTANCE_METERS);
                    // The quad faces +Z in its own frame; fromLookTowards points its -Z along the
                    // look direction, i.e. the quad turns to face the viewer.
                    Quaternion rotation = Quaternion.fromLookTowards(new Vector3(fx, 0f, fz), new Vector3(0f, 1f, 0f));
                    target = new Pose(position, rotation);
                    how = "head pose t=(" + t.getX() + "," + t.getY() + "," + t.getZ() + ")";
                }
            }
        } catch (Throwable e) {
            LimeLog.warning("XR stereo: head pose unavailable (" + e + "); using the default pose");
        }
        try {
            entity.setPose(target, Space.PARENT);
            Vector3 p = target.getTranslation();
            LimeLog.info("XR stereo: screen recentered at t=(" + p.getX() + "," + p.getY() + "," + p.getZ() + ") from " + how);
        } catch (RuntimeException e) {
            LimeLog.warning("XR stereo: recenter failed: " + e);
        }
    }

    /** The viewer's head pose in the activity space, or null when the runtime cannot provide it. */
    private Pose headPoseInActivitySpace() {
        // RenderViewpoint reports in the perception (ARCore) space; the entity is placed in the
        // activity space, so go through a ScenePose at that perception pose. A headset may offer
        // only the left/right viewpoints ("Mono render viewpoint is not available" on the Galaxy
        // XR), so try mono first and fall back to the eyes.
        Pose perception = viewpointPose("mono");
        if (perception == null) {
            Pose left = viewpointPose("left");
            Pose right = viewpointPose("right");
            if (left != null && right != null) {
                Vector3 l = left.getTranslation(), r = right.getTranslation();
                perception = new Pose(new Vector3((l.getX() + r.getX()) / 2f, (l.getY() + r.getY()) / 2f,
                        (l.getZ() + r.getZ()) / 2f), left.getRotation());
            } else {
                perception = left != null ? left : right;
            }
        }
        if (perception == null) {
            return null;
        }
        ScenePose scenePose = scene.getPerceptionSpace().getScenePoseFromPerceptionPose(perception);
        return scenePose.getPoseInActivitySpace();
    }

    private Pose viewpointPose(String which) {
        try {
            RenderViewpoint viewpoint = "left".equals(which) ? RenderViewpoint.left(session)
                    : "right".equals(which) ? RenderViewpoint.right(session) : RenderViewpoint.mono(session);
            RenderViewpoint.State state = viewpoint.getState().getValue();
            return state != null ? state.getPose() : null;
        } catch (RuntimeException e) {
            LimeLog.info("XR stereo: " + which + " render viewpoint unavailable: " + e.getMessage());
            return null;
        }
    }

    private void fail(String reason) {
        if (!active) return;
        LimeLog.warning("XR stereo unavailable: " + reason);
        Listener failedListener = listener;
        stop();
        if (failedListener != null) failedListener.onStereoUnavailable(reason);
    }

    private void cleanup(Runnable action) {
        try {
            action.run();
        } catch (Throwable t) {
            LimeLog.warning("XR stereo teardown: " + t);
        }
    }

    /** Disposes the entity, restores the main panel and returns to Home Space. Safe to call twice. */
    public void stop() {
        active = false;
        ++generation;
        waitingForFullSpace = false;
        if (fullSpaceTimeout != null) mainHandler.removeCallbacks(fullSpaceTimeout);
        fullSpaceTimeout = null;
        listener = null;
        // Invalidate ownership before invoking runtime APIs: removing a listener cannot
        // retract a callback already queued by SceneCore, and cleanup APIs can throw.
        Scene oldScene = scene;
        SurfaceEntity oldEntity = entity;
        MovableComponent oldMovable = movable;
        Runnable oldOriginListener = originListener;
        Consumer<Set<SpatialCapability>> oldCapabilitiesListener = capabilitiesListener;
        Consumer<SpatialModeChangeEvent> oldModeListener = modeListener;
        boolean restorePanel = mainPanelHidden;
        // Only undo a space change we made: a user who launched the app in Full Space
        // keeps it.
        boolean leaveFullSpace = requestedFullSpace;
        requestedFullSpace = false;
        scene = null;
        session = null; // lifecycle-bound to the activity; no explicit destroy API
        entity = null;
        movable = null;
        originListener = null;
        capabilitiesListener = null;
        modeListener = null;
        mainPanelHidden = false;
        if (oldScene != null) {
            if (oldOriginListener != null) cleanup(() -> oldScene.getActivitySpace().removeOriginChangedListener(oldOriginListener));
            if (oldCapabilitiesListener != null) cleanup(() -> oldScene.removeSpatialCapabilitiesChangedListener(oldCapabilitiesListener));
            if (oldModeListener != null) cleanup(oldScene::clearSpatialModeChangedListener);
            if (restorePanel) cleanup(() -> {
                Entity panel = oldScene.getMainPanelEntity();
                if (panel != null) {
                    cleanup(() -> panel.setEnabled(true));
                    cleanup(() -> panel.setAlpha(1f));
                }
            });
        }
        if (oldEntity != null) {
            if (oldMovable != null) cleanup(() -> oldEntity.removeComponent(oldMovable));
            cleanup(oldEntity::dispose);
        }
        if (oldScene != null && leaveFullSpace) cleanup(oldScene::requestHomeSpace);
    }
}
