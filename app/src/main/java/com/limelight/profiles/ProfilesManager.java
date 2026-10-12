package com.limelight.profiles;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.SharedPreferences.OnSharedPreferenceChangeListener;
import android.widget.Toast;

import androidx.annotation.NonNull;

import com.google.gson.Gson;
import com.google.gson.reflect.TypeToken;
import com.limelight.LimeLog;
import com.limelight.preferences.PreferenceConfiguration;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Reader;
import java.io.Writer;
import java.lang.reflect.Type;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

public class ProfilesManager {
    private static final String PROFILES_DIR = "profiles";
    private static final String PROFILES_FILE = "profiles.json";

    static ProfilesManager instance;

    private final Map<UUID, SettingsProfile> profiles = new LinkedHashMap<>();
    private UUID activeProfileId;
    private final List<ProfileChangeListener> listeners = new ArrayList<>();
    private Context appContext; // Application context for auto-save

    private ProfilesManager() {}

    public static synchronized ProfilesManager getInstance() {
        if (instance == null) {
            instance = new ProfilesManager();
        }
        return instance;
    }

    public boolean load(Context context) {
        LimeLog.info("ArtemisProfile: Loading profile...");
        if (context == null) {
            return false;
        }

        try {
            this.appContext = context.getApplicationContext();
        } catch (Exception e) {
            // If getApplicationContext() fails (e.g., during app startup), use the context directly
            this.appContext = context;
        }

        // Additional safety check
        if (this.appContext == null) {
            return false;
        }

        try {
            File dir = new File(this.appContext.getFilesDir(), PROFILES_DIR);
            if (!dir.exists() && !dir.mkdirs()) {
                return false;
            }
            File file = new File(dir, PROFILES_FILE);
            if (!file.exists()) {
                // We don't want to warn user about profile not exist
                return true;
            }
            ProfilesData data;
            try (Reader reader = new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8)) {
                Gson gson = new Gson();
                Type type = new TypeToken<ProfilesData>(){}.getType();
                data = gson.fromJson(reader, type);
            } catch (IOException e) {
                LimeLog.warning("ArtemisProfile: Failed to load profiles from file:" + e);
                e.printStackTrace();
                quarantine(file);
                return false;
            }
            // Validate a complete snapshot before replacing the live collection. A
            // malformed later entry must not leave only the successfully read prefix.
            if (data == null || data.profiles == null) {
                quarantine(file);
                throw new IOException("Missing profile collection");
            }
            Map<UUID, SettingsProfile> loaded = new LinkedHashMap<>();
            for (SettingsProfile profile : data.profiles) {
                // A name is part of the identity: the launcher activities call
                // getActiveName().isEmpty() on every start.
                if (profile == null || profile.getUuid() == null || profile.getName() == null) {
                    quarantine(file);
                    throw new IOException("Profile entry has no identity");
                }
                loaded.put(profile.getUuid(), profile);
            }
            profiles.clear();
            profiles.putAll(loaded);
            activeProfileId = data.activeProfileId;
        } catch (Exception e) {
            // JsonSyntaxException and the identity failures above land here.
            LimeLog.warning("ArtemisProfile: Failed to load profiles:" + e);
            e.printStackTrace();
            try {
                File file = new File(new File(this.appContext.getFilesDir(), PROFILES_DIR), PROFILES_FILE);
                if (file.exists()) {
                    quarantine(file);
                }
            } catch (Exception ignored) {
            }
            return false;
        }

        return true;
    }

    // A collection this build rejects is set aside instead of being left for the next
    // save() of the (empty) live map to replace: the atomic rename that makes save()
    // safe would otherwise reliably destroy the only copy. The newest damaged copy wins.
    private static void quarantine(File file) {
        File bad = new File(file.getParentFile(), PROFILES_FILE + ".bad");
        if (bad.exists() && !bad.delete()) {
            LimeLog.warning("ArtemisProfile: Failed to replace the previous quarantined profiles file");
        }
        if (!file.renameTo(bad)) {
            LimeLog.warning("ArtemisProfile: Failed to quarantine the rejected profiles file");
        }
    }

    public boolean save(Context context) {
        if (context == null) {
            return false;
        }

        File temporary = null;
        try {
            File dir = new File(context.getFilesDir(), PROFILES_DIR);
            if (!dir.exists() && !dir.mkdirs()) {
                return false;
            }
            File file = new File(dir, PROFILES_FILE);
            // Keep the previous file until serialization, flush, sync and close
            // have all succeeded. A same-directory rename replaces it atomically
            // on Android, including when saving over an existing profile file.
            temporary = File.createTempFile(PROFILES_FILE, ".tmp", dir);
            try (FileOutputStream output = new FileOutputStream(temporary);
                 Writer writer = new OutputStreamWriter(output, StandardCharsets.UTF_8)) {
                Gson gson = new Gson();
                ProfilesData data = new ProfilesData();
                data.profiles = new ArrayList<>(profiles.values());
                data.activeProfileId = activeProfileId;
                gson.toJson(data, writer);
                writer.flush();
                output.getFD().sync();
            } catch (IOException e) {
                LimeLog.warning("ArtemisProfile: Failed to save profiles to file:" + e);
                e.printStackTrace();
                return false;
            }
            if (!temporary.renameTo(file)) {
                throw new IOException("Failed to replace profiles file");
            }
        } catch (Exception e) {
            LimeLog.warning("ArtemisProfile: Failed to save profiles:" + e);
            e.printStackTrace();
            return false;
        } finally {
            if (temporary != null && temporary.exists() && !temporary.delete()) {
                LimeLog.warning("ArtemisProfile: Failed to remove temporary profiles file");
            }
        }

        return true;
    }

    public List<SettingsProfile> getProfiles() {
        return new ArrayList<>(profiles.values());
    }

    public void add(SettingsProfile profile) {
        profiles.put(profile.getUuid(), profile);
        notifyListeners();
        saveIfPossible();
    }

    public void update(SettingsProfile profile) {
        profiles.put(profile.getUuid(), profile);
        notifyListeners();
        saveIfPossible();
    }

    public void delete(UUID uuid) {
        profiles.remove(uuid);
        if (uuid.equals(activeProfileId)) {
            activeProfileId = null;
        }
        notifyListeners();
        saveIfPossible();
    }

    public void setActive(UUID uuid) {
        activeProfileId = uuid;
        notifyListeners();
        saveIfPossible();
    }

    public SettingsProfile getActive() {
        return activeProfileId == null ? null : profiles.get(activeProfileId);
    }

    @NonNull
    public String getActiveName() {
        SettingsProfile active = getActive();
        return active == null || active.getName() == null ? "" : active.getName();
    }

    public void addListener(ProfileChangeListener listener) {
        listeners.add(listener);
    }

    public void removeListener(ProfileChangeListener listener) {
        listeners.remove(listener);
    }

    private void notifyListeners() {
        for (ProfileChangeListener listener : listeners) {
            listener.onProfilesChanged();
        }
    }

    private static class ProfilesData {
        List<SettingsProfile> profiles;
        UUID activeProfileId;
    }

    public interface ProfileChangeListener {
        void onProfilesChanged();
    }

    /**
     * Returns a SharedPreferences that overlays the active profile's options on top of the real prefs.
     */
    public SharedPreferences getOverlayingSharedPreferences(Context context) {
        SharedPreferences base = androidx.preference.PreferenceManager.getDefaultSharedPreferences(context);
        SettingsProfile active = getActive();
        if (active == null || active.getOptions() == null) {
            return base;
        }
        return new OverlaySharedPreferences(base, active.getOptions());
    }

    /**
     * Wraps a SharedPreferences to override and shadow values from a profile's options map.
     */
    private static class OverlaySharedPreferences implements SharedPreferences {
        private final SharedPreferences base;
        private final Map<String, Object> patch;
        OverlaySharedPreferences(SharedPreferences base, Map<String, Object> patch) {
            this.base = base;
            this.patch = patch;
        }
        @Override public Map<String, ?> getAll() {
            Map<String, Object> combined = new LinkedHashMap<>(base.getAll());
            combined.putAll(patch);
            return combined;
        }
        @Override public String getString(String key, String defValue) {
            if (patch.containsKey(key)) return (String) patch.get(key);
            return base.getString(key, defValue);
        }
        @Override public int getInt(String key, int defValue) {
            if (patch.containsKey(key)) return ((Number) patch.get(key)).intValue();
            return base.getInt(key, defValue);
        }
        @Override public long getLong(String key, long defValue) {
            if (patch.containsKey(key)) return ((Number) patch.get(key)).longValue();
            return base.getLong(key, defValue);
        }
        @Override public float getFloat(String key, float defValue) {
            if (patch.containsKey(key)) return ((Number) patch.get(key)).floatValue();
            return base.getFloat(key, defValue);
        }
        @Override public boolean getBoolean(String key, boolean defValue) {
            if (patch.containsKey(key)) return (Boolean) patch.get(key);
            return base.getBoolean(key, defValue);
        }
        @Override public Set<String> getStringSet(String key, Set<String> defValues) {
            if (patch.containsKey(key)) return (Set<String>) patch.get(key);
            return base.getStringSet(key, defValues);
        }
        @Override public boolean contains(String key) {
            return patch.containsKey(key) || base.contains(key);
        }
        @Override public Editor edit() { return base.edit(); }
        @Override public void registerOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener listener) {
            base.registerOnSharedPreferenceChangeListener(listener);
        }
        @Override public void unregisterOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener listener) {
            base.unregisterOnSharedPreferenceChangeListener(listener);
        }
    }

    private boolean saveIfPossible() {
        if (appContext != null) {
            return save(appContext);
        }
        return false;
    }
}
