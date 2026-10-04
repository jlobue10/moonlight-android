package com.limelight.xr;

import android.app.Activity;
import android.os.Bundle;

import androidx.xr.runtime.Session;
import androidx.xr.runtime.SessionCreateResult;
import androidx.xr.runtime.SessionCreateSuccess;
import androidx.xr.scenecore.LaunchUtils;

import com.limelight.LimeLog;
import com.limelight.preferences.PreferenceConfiguration;

/**
 * Starts the stream activity directly in Full Space on Android XR when it will show the stereo
 * screen. Requesting Full Space from inside the running stream made the system stop and restart
 * the Game activity during the transition, and Moonlight ends the stream (and finishes) whenever
 * its activity is stopped, so the stream never came up. Launching into Full Space avoids the
 * transition altogether; {@link XrStereoPresenter} then finds the 3D-content capability already
 * present and creates its surface immediately.
 */
public final class XrLaunch {
    private XrLaunch() {}

    /** Activity options for startActivity(), or null when a normal launch is right. */
    public static Bundle fullSpaceOptionsIfNeeded(Activity parent) {
        try {
            if (!XrStereoPresenter.isSupported(parent)) {
                return null;
            }
            PreferenceConfiguration prefs = PreferenceConfiguration.readPreferences(parent);
            if (prefs.renderMode == 0 || !prefs.xrStereo) {
                return null;
            }
            SessionCreateResult result = Session.create(parent);
            if (!(result instanceof SessionCreateSuccess)) {
                LimeLog.warning("XR: no session for a Full Space launch (" + result.getClass().getSimpleName() + ")");
                return null;
            }
            Bundle options = LaunchUtils.createBundleForFullSpaceLaunch(((SessionCreateSuccess) result).getSession(), new Bundle());
            LimeLog.info("XR: launching the stream in Full Space");
            return options;
        } catch (Throwable t) {
            // Missing XR runtime classes or a refused session: a normal launch still works.
            LimeLog.warning("XR: Full Space launch options unavailable: " + t);
            return null;
        }
    }
}
