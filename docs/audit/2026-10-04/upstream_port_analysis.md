# Porting analysis: moonlight-stream/moonlight-android (og/master, v12.2) → jlobue10/moonlight-android `moonlight-noir` (Artemis)

Repo: `/home/user/jlobue10/moonlight-android`, branch `moonlight-noir` @ `c5cf27f4` (identical to ClassicOldSong/moonlight-android `moonlight-noir`).
Analysis date: 2026-10-04. All cherry-pick tests were run in throw-away worktrees (removed afterwards); the main checkout was never modified. The `moonlight-common-c` submodule got an extra `og` remote (read-only fetch, no checkout change) and its nested `enet` submodule was fetched (no checkout change).

---

## 1. Executive summary

* **Where the fork sits.** `git merge-base --all` gives two bases: `f10085f5` (2024-07-27, "Update to libopus v1.5.2") is the last upstream *master* commit Artemis ever merged; `27ded2ad` (2024-11-14) is the last Weblate-branch commit it took. Upstream master shipped **nothing but translations between 2024-07-27 and 2026-08-31**; then Cameron Gutman landed the entire v12.2 release in a two-week burst (2026-08-31 → 2026-09-12). `origin/moonlight-noir..og/master` is 55 commits: 25 Weblate translation commits (ignored), 1 Weblate merge, and **29 substantive commits**, all analysed below.
* **Nothing has been ported yet.** No upstream commit has a patch-id twin in the fork (`git cherry`), none of the new API/identifier strings exist in the fork, and Artemis's `upstream/next` branch is a stale 2024 "MoonlightNext" experiment with none of them either. Four upstream fixes are *partially* pre-empted by independent Artemis work (8BitDo vendor ID, Series X init packets for 0x0b12/0x02fe, a `GameManager` null check, and jmDNS/BC bumps to intermediate versions).
* **Line endings dominate the mechanical picture.** The fork converted **122 of 171 Java files and 107 other text files to CRLF**; upstream is LF. A plain `git cherry-pick` therefore conflicts on every hunk of 7 commits that are *content-wise clean*. With `-Xignore-space-at-eol` (or after a simulated `* text=auto` renormalization — both were tested and give identical results) the true picture is: **15 of 29 apply cleanly, 14 conflict**, of which 9 are one-line version/adjacency conflicts in gradle/driver tables, 4 are trivial Java conflicts, and **only one (3df0103a, the <API 21 cleanup) is a substantive conflict** with Artemis's rewritten `MediaCodecDecoderRenderer`. Gotcha: an ignore-EOL pick leaves the *added* lines as LF inside a CRLF file (verified: 53 LF lines in a 3,503-line CRLF `ControllerHandler.java`), so touched files must be re-CRLF'd (`unix2dos`) before committing. See §7 for the normalization recommendation.
* **Highest-value ports (all small, all apply with ignore-EOL):** `abde6021` controller LED crash/ANR fix, `3c6a0d12` rumble via `VibratorManager.getDefaultVibrator()`, `68adf9ec` stop rewriting H.264 SPS constraint/level_idc on Oreo+, `ddb674a9` native keyboard capture on Android 16.1, `6d4c64a5` disable surface producer throttling (Android 17), `8974dcda` F13–F24/Print/Screenshot keys, `b9c5eddd` '+' key shift. The last three of the previous sentence plus keyboard capture need **compileSdk 37** (fork is on 36).
* **Native/security:** the fork still ships **OpenSSL 1.1.1q (July 2022; the 1.1.1 series has been out of support since 2023-09-11)** and **libopus 1.5.2**; upstream moved to **OpenSSL 4.0.2 + libopus 1.6.1** and dropped the unused `libssl.a` from the link. Exposure is bounded — common-c only uses `EVP_aes_128_{gcm,cbc}` and `RAND_bytes` — so this is hygiene rather than a known hole, but it applies cleanly (binary blobs + 3-line Android.mk edit).
* **moonlight-common-c:** the submodule (`ClassicOldSong/moonlight-common-c @ c999436`, 2025-09-01) is **44 upstream commits behind** `og/master` (`f900dd4`) and 42 behind what upstream 12.2 pins (`874ac95`). Missing: RTSP response-size/Session-header hardening (security), the nanors SIMD Reed-Solomon FEC decoder (performance), three RFI/IDR loss-handling fixes, LTR-ACK support, enqueue-side gamepad batching, thread-context/pthread_attr leak fixes, RTT-without-mutex, dual-touchpad + Steam controller type, `MODIFIER_EXTENDED`, and µs-resolution timestamps + RTP stats APIs. Artemis adds only two commits (Apollo server-command control messages and an empty keep-alive payload). **`git merge-tree` shows og/master merges into the Artemis submodule with zero conflicts.** The bump is coupled to upstream's Android-side commit `0dc4c4fe` (µs DECODE_UNIT fields, nanors sources in Android.mk, new constants) and — critically for Artemis — to its **`baseTimestampUs` normalization**, because the new `PltGetMicroseconds()` no longer shares `System.nanoTime()`'s epoch and the fork's frame-age/latency code assumes it does (§3.9).
* **Toolchain gap:** fork AGP 8.13 / Gradle 8.13 / Java 11 / compileSdk 36 / targetSdk 34 / NDK r27 vs upstream AGP 9.4 / Gradle 9.7.1 / Java 17 + core-library desugaring / compileSdk 37 / targetSdk 36 / NDK r29 (§4). One latent issue: the fork already runs **jmDNS 3.6.2 without desugaring**, the exact combination upstream found broken "on older Android versions" (`4eb24a8d`), and the fork uses jmDNS on every device below Android 14 — worth verifying on an API 21–25 emulator.

---

## 2. Upstream moonlight-android commits since the merge base

Legend — **Pick** column = result of `git cherry-pick --no-commit` onto `origin/moonlight-noir`: `plain` / `-Xignore-space-at-eol` (the post-renormalization run reproduced the second column exactly). ✓ = applied, ✗ = conflict (files listed). **In fork** = equivalent change already present.

| # | SHA | Date | Subject | Class | Value | Difficulty | In fork? | Pick plain / ignore-EOL | Notes |
|---|-----|------|---------|-------|-------|------------|----------|--------------------------|-------|
| 1 | `9d8b073c` | 08-31 | Update SDK and AGP (compileSdk 34→37, AGP 8.5.1→9.3.2, Gradle 8.7→9.7.1, `resValues=true`, release proguard `-optimize`, debug no longer minified) | build-dep | medium (prereq for #18/#22) | medium–high | partial (fork already at AGP 8.13 / Gradle 8.13 / compileSdk 36 via its own bumps) | ✗ `app/build.gradle`, `build.gradle`, wrapper / ✗ same | Conflicts are version lines + the fork's `debug` buildType (Diana labels, minify). Redo by hand. |
| 2 | `1fa0e2a0` | 08-31 | NDK r29 (`29.0.14206865`) | build-dep | low–med | trivial | no (r27) | ✓ / ✓ | Needs r29 installed in CI. |
| 3 | `b3a7e32a` | 08-31 | jMDNS 3.5.9→3.6.3, BouncyCastle 1.77→1.85.2/1.85, `packaging.excludes += '/META-INF/*.md'`, drop `android.useAndroidX=false` | build-dep (security-adjacent) | medium | trivial by hand | partial (fork: jmDNS 3.6.2, BC 1.81) | ✗ `app/build.gradle` / ✗ | Dependency block adjacency only. Keep fork's `pickFirsts`; add the `excludes`. |
| 4 | `9221a0ca` | 08-31 | Java source/target 11→17 | build-dep | low (prereq for #26's hunk) | trivial | no | ✓ / ✓ | |
| 5 | `0dc4c4fe` | 09-01 | Update moonlight-common-c (8af4562→874ac95): `receiveTimeUs/enqueueTimeUs`, `baseTimestampUs` normalization, nanors in Android.mk, `LI_CTYPE_STEAM`, `LI_CCAP_DUAL_TOUCHPAD` | perf / security / input | **high** | medium | no | ✗ MediaCodec, VideoDecoderRenderer, MoonBridge, gitlink / ✗ **gitlink only** | Java/C hunks apply with ignore-EOL; the gitlink must point at an Artemis submodule commit that has merged upstream (§5). See §3.9 for the epoch hazard. |
| 6 | `c2e224eb` | 09-01 | Sync SDL joystick code (controller_list/usb_ids/controller_type refresh, `GuessControllerType`-based Xbox/PS detection, Steam/Switch2/Elite/PS5-Edge types) | input | medium | low | no (fork's SDL files are byte-identical to the merge base) | ✓ / ✓ | **Won't compile until `LI_CTYPE_STEAM` exists** (common-c ≥ `874ac95`) — or add a local `#define`. |
| 7 | `4b2221d3` | 09-01 | targetSdk 34→36 | build/other | medium | low to apply, medium to validate | no | ✓ / ✓ | Edge-to-edge is enforced from targetSdk 35; Artemis's custom overlays/menus need a visual pass. Predictive back: both sides already set `enableOnBackInvokedCallback="false"` on `Game`, so no back-key regression. |
| 8 | `3f114ac7` | 09-01 | AppVeyor VS2026/JDK25 | CI | none | — | n/a | ✓ / ✓ | Fork's `appveyor.yml` is vestigial; skip. |
| 9 | `583f662a` | 09-01 | Add 8BitDo (0x2dc8) to XboxOneController vendors | input | **already present** | — | **yes** | ✗ / ✗ (trivial: fork's list also has GameSir 0x3537) | Skip. |
| 10 | `3c6a0d12` | 09-01 | Rumble via `VibratorManager.getDefaultVibrator()` on S+ | input | **high** | trivial | no | ✗ / ✓ | §3.6 |
| 11 | `b9c5eddd` | 09-01 | Shift modifier for `KEYCODE_PLUS` | input | medium | trivial | no | ✓ / ✓ | §3.8 |
| 12 | `0711e236` | 09-01 | UiHelper: tolerate missing/partial `GameManager` (Meta Quest) | other/stability | low | trivial | **partial** (fork null-checks and logs; lacks `try/catch(Throwable)`) | ✗ / ✗ (trivial) | Wrap fork's `UiHelper.setGameModeStatus()` (lines 39–55) body in upstream's try/catch. |
| 13 | `280454fd` | 09-01 | Xbox Series S/X init packets (0x02fe, 0x0b05, 0x0b12, 0x0b13) | input | low | trivial | **partial** (fork has 0x0b12 and 0x02fe with `ONE_S_INIT`, same bytes as `SERIES_S_INIT`) | ✗ / ✗ (trivial) | Only 0x0b05 (Elite 2 *Bluetooth* PID) and 0x0b13 (Series X *BLE* PID) are missing — both are wireless PIDs that essentially never reach the USB driver. Add for parity; expect no behavioural change. |
| 14 | `578f38f6` | 09-01 | Fix AppVeyor CI | CI | none | — | n/a | ✗ / ✗ | Skip. |
| 15 | `8d720748` | 09-01 | Lint fix: restructure the Android 12 sensor-manager guard | other | low | trivial | no | ✗ / ✓ | Pure restructuring of `ControllerHandler` 777–785; harmless to take with #21. |
| 16 | `3df0103a` | 09-01 | Remove <API 21 code paths in `MediaCodecDecoderRenderer` (−91/+37) | other (cleanup) | low | **high** | no (`legacyInputBuffers` etc. still present) | ✗ / ✗ **substantive** (3 blocks against Artemis's `releaseWithPolicy`/`preferLowerDelays`/frame-age rewrite) | Do not port as a patch. Optionally hand-delete the fork's own dead `< LOLLIPOP` branches (18 sites). |
| 17 | `68adf9ec` | 09-01 | Don't modify H.264 constraint_set4/5 or level_idc on Oreo+ | picture-quality / stability | **high** | trivial | no | ✗ / ✓ | §3.2 |
| 18 | `6d4c64a5` | 09-01 | `Surface.setProducerThrottlingEnabled(false)` (Android 17) | performance (latency) | **high** (on 17+ devices) | trivial, **needs compileSdk 37** | no | ✓ / ✓ | §3.1 |
| 19 | `8974dcda` | 09-01 | F13–F24, `KEYCODE_PRINT`, `KEYCODE_SCREENSHOT` | input | medium | trivial | no (fork's `KeyMapper` has VK_F13+ only for its scancode table) | ✗ / ✗ (trivial: `VK_F13` constant placement) | §3.5 |
| 20 | `ad861490` | 09-01 | `@SuppressLint("InlinedApi")` on button map / battery / translate | other (lint) | low | trivial | no | ✗ / ✗ (trivial: fork's `translate()` has an extra `scancode` param) | Apply annotations by hand with #19. |
| 21 | `abde6021` | 09-01 | Fix crashes and ANRs in controller LED handling | input / stability | **high** | low | no | ✗ / ✓ | §3.4 |
| 22 | `ddb674a9` | 09-01 | Android 16.1 keyboard capture (`CAPTURE_KEYBOARD` + `setKeyboardCaptureEnabled`) | input | **high** | low, **needs compileSdk ≥ 36.1 (upstream: 37)** | no | ✗ manifest+Game / ✗ Game only (trivial: fork uses a multi-catch) | §3.3 |
| 23 | `31b70030` | 09-02 | libopus 1.5.2→1.6.1, OpenSSL 1.1.1q→4.0.2, drop libssl, `libopus/`→`opus/` | security / build-dep | **high** (hygiene) | low (binary blobs; +~20 MB of `.a` churn) | no | ✓ / ✓ | §3.10 |
| 24 | `b55b4b6d` | 09-02 | Language list: eo, lv, ta; "Bahasa Indonesia" | other (i18n) | low | trivial | no (fork keeps its own list) | ✗ / ✓ | Only meaningful together with the Weblate translation commits Artemis does not take. |
| 25 | `5c0c2390` | 09-02 | `local_network_rationale` string ("currently unused") | other | none now | trivial | no | ✓ / ✓ | Groundwork for Android's Local Network Permission; take when upstream wires it. |
| 26 | `4eb24a8d` | 09-02 | Core library desugaring (`desugar_jdk_libs 2.1.5`) "to fix jmDNS on older Android versions" | build-dep / stability | **high if the fork is affected** | trivial | no — and fork is on jmDNS 3.6.2 **without** it | ✗ / ✗ (trivial, depends on #4) | §3.11 |
| 27 | `801dba1b` | 09-02 | AGP 9.3.2→9.4.0 | build-dep | bundled with #1 | trivial | no | ✗ / ✗ (version line) | |
| 28 | `98c12beb` | 09-02 | OkHttp 4.12→5.5 + `.fastFallback(false)` | build-dep / stability | medium | low | no | ✗ / ✗ (version line; `NvHTTP.java` hunk applies) | §3.11. `fastFallback()` exists since OkHttp 4.11, so the NvHTTP line can be taken before the upgrade. |
| 29 | `b48494cb` | 09-12 | Version 12.2 + changelog | release | none | — | n/a | ✗ / ✗ | Skip. |
| — | `4dc95b56` | 09-02 | Merge 'origin/weblate' | i18n | none | — | n/a | not tested | Skip (translations). |

Counts with `-Xignore-space-at-eol`: 15 clean (#2, 4, 6, 7, 8, 10, 11, 15, 17, 18, 21, 23, 24, 25 + #5's source files), 14 conflicting; after excluding CI/release/skip rows, the only non-trivial conflict is #16.

---

## 3. Detailed notes on the high-value commits

File:line references are to the fork (`app/src/main/java/com/limelight/...`), measured on `c5cf27f4`.

### 3.1 `6d4c64a5` — Disable producer throttling on the video surface (performance)

```java
// Game.java, surfaceCreated(), right after the setFrameRate() block
// Disable producer throttling on the underlying surface for reduced latency
if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.CINNAMON_BUN) {
    holder.getSurface().setProducerThrottlingEnabled(false);
}
```
Rationale (upstream's commit message says only "for reduced latency"; the mechanism): Android 17 (`CINNAMON_BUN`, API 37) exposes the compositor's per-surface *producer throttling* — the BufferQueue back-pressure that paces a producer to the display's refresh cadence. A streaming client that releases decoded frames with explicit timestamps (`releaseOutputBuffer(index, ts)`) wants the newest frame in front of the compositor as soon as it is decoded; throttling can hold the decoder's output behind an older queued buffer for up to one refresh period. Turning it off trades a possible dropped frame for lower and more consistent latency, which is Moonlight's preference. No-op below API 37.

Where it lands: `Game.java` `surfaceCreated()` (declared at 3811), immediately after the `setFrameRate` block at 3832–3843. Applies cleanly. **Requires compileSdk 37** (fork: 36). Artemis interplay: complementary to Artemis's own `preferLowerDelays`/`releaseWithPolicy()` logic in `MediaCodecDecoderRenderer` (lines 49, 67) and its reflection-based `SurfaceView.setFrameRate` call at `Game.java:915–931`; no conflict.

### 3.2 `68adf9ec` — Disable H.264 constraint and level_idc modifications on Oreo and later (picture quality / stability)

Two one-condition changes in `MediaCodecDecoderRenderer`:
```java
// fork 1736: 'else {' becomes
else if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
    // Force the constraints unset otherwise (some may be set by default)
    sps.constraintSet4Flag = false;
    sps.constraintSet5Flag = false;
}
// fork 1917:
if (!refFrameInvalidationActive && Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
    ... "Patching level_idc to 31/32/..."
```
Rationale (upstream gives none; inferred from the code's history): Moonlight has rewritten the SPS since the KitKat/Lollipop era — clearing `constraint_set4/5` and lowering `level_idc` so old decoders would allocate a minimal DPB (one buffered frame, lower latency). Modern (Oreo+, Codec2-era) decoders size their buffers from the stream and can treat an SPS whose level is *lower than the stream actually needs* as invalid, or behave differently when constraint flags the encoder legitimately set are cleared. Upstream now leaves the SPS alone on O+ except for the explicit `constrainedHighProfile` quirk (which still *sets* the flags for decoders known to benefit). Expect fewer decoder-init failures / fallback-to-software cases on newer SoCs; no change below O.

Lands at fork lines 1736–1740 and 1917 (the fork's SPS code is textually identical to upstream's here). Applies cleanly with ignore-EOL.

### 3.3 `ddb674a9` — Android 16.1 keyboard capture (input)

* `AndroidManifest.xml`: add `<uses-permission android:name="android.permission.CAPTURE_KEYBOARD" />` (normal permission; fork's permission block is lines 4–12).
* `Game.java:1348–1370 setMetaKeyCaptureState(boolean)`: new first branch
```java
if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.BAKLAVA && Build.VERSION.SDK_INT_FULL >= Build.VERSION_CODES_FULL.BAKLAVA_1) {
    WindowManager.LayoutParams windowLayoutParams = getWindow().getAttributes();
    windowLayoutParams.setKeyboardCaptureEnabled(enabled);
    getWindow().setAttributes(windowLayoutParams);
} else { /* existing Samsung SemWindowManager reflection hack, with ClassNotFoundException now ignored silently */ }
```
Rationale: Android 16.1 (API 36.1, `VERSION_CODES_FULL.BAKLAVA_1`) adds a platform API that lets a focused window receive system-reserved key combinations (Meta/Win, Alt+Tab, …) instead of the launcher/system — exactly what the Samsung-only reflection hack did. Fixes upstream #975, #1281, #1505. The fallback is kept for Samsung devices on ≤16.0.

Conflict: only because the fork's catch block is a Java 7 multi-catch (`catch (ClassNotFoundException | NoSuchMethodException | … e)`); re-apply the body by hand (upstream also demotes `ClassNotFoundException` to `ignored`). **Requires `SDK_INT_FULL`/`VERSION_CODES_FULL` → compileSdk ≥ 36.1; upstream uses 37.** Benefits Artemis's `rightAltAsMeta`/`backAsMeta` preferences directly (Meta combos reach `handleKeyDown`).

### 3.4 `abde6021` — Fix crashes and ANRs in controller LED handling (input / stability)

What was wrong, and the fix (fork line numbers):
1. **ANR**: `setControllerLED()` (`ControllerHandler` 2431–2459) opened a `LightsSession` and called `requestLights()` synchronously on whatever thread the host's LED packet arrived on; these are binder round-trips into InputManagerService. Upstream stores the colour in `InputDeviceContext.ledArgbValue` and posts a coalesced `setLedStateRunnable` to the existing `backgroundThreadHandler` (`removeCallbacks` + `post`, so rapid host updates don't queue up).
2. **Wrong capability on < Android 14**: `Light.hasRgbControl()` always returned true before UDC (`LIGHT_CAPABILITY_RGB` was 0), so non-RGB pads got a session and an empty request. The guess ("assume RGB only for Sony vendor 0x054c on < 14") moves from `getControllerCapabilities()` (3308–3313) into detection (after 787–795), so `hasRgbLed` and the advertised `LI_CCAP_RGB_LED` agree; the runnable also only opens a session if at least one RGB light exists.
3. **Crash on destroy**: `lightsSession.close()` (3234–3238) can throw `RuntimeException` when the device vanished; now caught and logged.
4. **Context migration** (`migrateContext`, 3374–3378): `ledArgbValue` is copied and the LED state re-posted, since a pending runnable may have been lost.

Applies cleanly with ignore-EOL — Artemis's `ControllerHandler` differs from upstream by ~363 content lines but none in these regions. Take `8d720748` and `ad861490`'s `ControllerHandler` annotations in the same step.

### 3.5 `8974dcda` — Newer keycodes from Android 15/16 (input)

`KeyboardTranslator.java`: `VK_F13 = 0x7C` constant (place next to the fork's `VK_F12 = 123` at ~line 36 — the only conflict); a new branch after the F1–F12 range at 215–218:
```java
else if (keycode >= KeyEvent.KEYCODE_F13 && keycode <= KeyEvent.KEYCODE_F24) {
    translated = (keycode - KeyEvent.KEYCODE_F13) + VK_F13;
}
```
and two cases before `default:` (fork 407): `KEYCODE_PRINT → 0x2A` (VK_PRINT), `KEYCODE_SCREENSHOT → 0x2C` (VK_SNAPSHOT). The constants were added in Android 15/16, hence `ad861490`'s `@SuppressLint("InlinedApi")` on `translate()`. compileSdk 36 (fork) is sufficient. The fork already has `utils/KeyMapper.java` with `VK_F13..VK_F24` for its Linux-scancode table, so you can reference `KeyMapper.VK_F13` instead of adding a second constant.

### 3.6 `3c6a0d12` — Rumble not working due to deprecated API (input)

Constructor change (fork `ControllerHandler` 152–157):
```java
if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
    this.deviceVibratorManager = (VibratorManager) activityContext.getSystemService(Context.VIBRATOR_MANAGER_SERVICE);
    this.deviceVibrator = this.deviceVibratorManager.getDefaultVibrator();
} else {
    this.deviceVibratorManager = null;
    this.deviceVibrator = (Vibrator) activityContext.getSystemService(Context.VIBRATOR_SERVICE);
}
```
and delete the fork's unconditional `deviceVibrator = getSystemService(VIBRATOR_SERVICE)` line earlier in the constructor. Rationale: `VIBRATOR_SERVICE` is deprecated since S and on some devices/ROMs hands back a vibrator that no longer drives the device motor (contributor report #1454); `VibratorManager.getDefaultVibrator()` is the supported path. Applies cleanly with ignore-EOL. Optional Artemis follow-up: the fork has five more `VIBRATOR_SERVICE` users (`VirtualController`, `KeyBoardController`, `Game`, `StreamSettings`, `DebugInfoActivity`).

### 3.7 `280454fd` / `583f662a` — Xbox Series / 8BitDo (input)

Already mostly in the fork (`XboxOneController.java`: vendors 19–30 include `0x2dc8 // 8BitDo` and `0x3537 // GameSir`; `INIT_PKTS` 46–64 include `0x0b12` and `0x02fe` with `ONE_S_INIT`, whose bytes equal upstream's `SERIES_S_INIT`). Missing only `0x0b05` (`USB_PRODUCT_XBOX_ONE_ELITE_SERIES_2_BLUETOOTH`) and `0x0b13` (`USB_PRODUCT_XBOX_SERIES_X_BLE`) — wireless PIDs that won't normally be seen by a USB driver. Add two `InitPacket` lines for parity; skip `583f662a`.

### 3.8 `b9c5eddd` — Shift modifier for the plus key (input)

The host protocol has one US `=`/`+` virtual key (0xBB); Android's semantic `KEYCODE_PLUS` (on-screen keyboards, some layouts) therefore needs Shift. Upstream adds `applyKeySpecificModifiers(keyCode, modifier)` and routes both `getModifierState(KeyEvent)` (fork 2013–2033) and a new `getModifierState(int keyCode)` through it, used by `keyboardEvent()` (fork 3911–3928, textually identical to upstream). Applies cleanly. Note: Artemis's own soft keyboard (`KeyBoardController`, sends via `conn.sendKeyboardInput(key, …, modifier[0], …)` at `Game.java:2229/2243`) bypasses this path and would need the same treatment if it emits `KEYCODE_PLUS`.

### 3.9 `0dc4c4fe` + `c2e224eb` — moonlight-common-c bump and SDL sync (performance / security / input)

Android-side changes that accompany the submodule bump (`8af4562 → 874ac95`):
* `DECODE_UNIT.receiveTimeMs/enqueueTimeMs` → **`receiveTimeUs/enqueueTimeUs`** (common-c `82ee2d6`): `callbacks.c:171,192`, `MoonBridge.java:219–221`, `VideoDecoderRenderer.java:12–14`, `MediaCodecDecoderRenderer.java:1745–1747`; stats math `totalTimeMs += (enqueueTimeUs - receiveTimeUs) / 1000` at 2123.
* **Epoch change — the Artemis-specific hazard.** Today `enqueueTimeMs` comes from `PltGetMillis()` = `CLOCK_MONOTONIC` ms, which shares `System.nanoTime()`'s epoch on Android. The fork relies on that coincidence: it queues `timestampUs = enqueueTimeMs * 1000` (2149) and later computes `frameAgeNs = System.nanoTime() - (info.presentationTimeUs * 1000L)` (1298–1300, again at ~1364) to decide drops in MAX_SMOOTHNESS/CAP_FPS modes. The new `PltGetMicroseconds()` returns **microseconds since library init (`start_ts`, `CLOCK_MONOTONIC_RAW`)**, so after the bump every frame would look seconds-to-days old and the drop logic would fire constantly. Upstream's fix in the same commit is mandatory for the fork:
```java
// MediaCodecDecoderRenderer, submitDecodeUnit(): the frame timestamps use an undefined epoch, so normalize them to uptime microseconds.
if (baseTimestampUs == 0) {
    baseTimestampUs = (SystemClock.uptimeMillis() * 1000) - enqueueTimeUs;
}
long timestampUs = baseTimestampUs + enqueueTimeUs;
```
(`uptimeMillis()` ≈ `nanoTime()/1e6`, so the fork's nanoTime comparisons keep working with ms-granularity error.) Audit every `presentationTimeUs`-vs-`nanoTime` site: 1298–1300, ~1364, `enqueueNsByPtsUs.put(timestampUs, …)` at 1688 and `updateDecodeLatencyStats()` at 86–97 (keyed by PTS; unaffected as long as the same `timestampUs` is used on both ends).
* `Android.mk`: replace `moonlight-common-c/reedsolomon/rs.c` with `nanors/deps/obl/oblas_common.c`, `nanors/deps/obl/oblas_lite.c`, `nanors/rs.c` and add the `nanors` + `nanors/deps/obl` include dirs; `nanors` is a **new nested submodule** → `git submodule update --init --recursive` in docs/CI.
* `MoonBridge.java`: `LI_CTYPE_STEAM = 0x04`, `LI_CCAP_DUAL_TOUCHPAD = 0x100`.
* `c2e224eb` (applies cleanly; the fork's `minisdl.c/h`, `controller_list.h`, `usb_ids.h`, `controller_type.h` are byte-identical to the merge base, and Artemis's `simplejni.c` additions are in another region) rewrites the three `SDL_IsJoystick*` helpers on top of `GuessControllerType()` + a known-old-Xbox-One table, refreshes ~190 controller IDs, and maps Steam/Hori-Steam/Switch 2/PS5 Edge/Xbox Elite types in `guessControllerType`. `simplejni.c` returns `LI_CTYPE_STEAM`, which **does not compile until common-c ≥ `874ac95`** (or a local define).

### 3.10 `31b70030` — libopus 1.6.1 and OpenSSL 4.0.2 (security / build)

* Fork: `openssl/include/openssl/opensslv.h` says `OpenSSL 1.1.1q  5 Jul 2022` (binary string confirms); `libopus 1.5.2` (binary string). Both are exactly what og/master had at the merge base.
* Upstream: `OpenSSL 4.0.2 25 Aug 2026`, libcrypto only (`libssl.a` deleted; `LOCAL_STATIC_LIBRARIES := libopus libcrypto cpufeatures`), libopus 1.6.1 (binary reports `libopus unknown` — built without git metadata via cgutman/moonlight-mobile-deps `419349aa`), directory renamed `libopus/` → `opus/`.
* Exposure: common-c's `PlatformCrypto.c` uses only `EVP_aes_128_gcm/cbc`, `EVP_CIPHER_CTX_*` and `RAND_bytes` — no TLS, no X.509/ASN.1 parsing (pairing is done in Java with BouncyCastle). So none of the post-1.1.1q X.509/ASN.1-class CVEs reach this code path; the value is (a) being on a supported line (1.1.1 EOL 2023-09-11), (b) dropping a dead ~1 MB `libssl.a` per ABI, (c) newer AES-GCM/NEON code. Applies cleanly (pure binary + Android.mk; the fork's Android.mk already has the same `LOCAL_BRANCH_PROTECTION := standard` line as upstream).

### 3.11 Dependencies with runtime effect: `98c12beb`, `b3a7e32a`, `4eb24a8d`

* **OkHttp 5.5** (`98c12beb`): OkHttp 5 enables *fast fallback* (RFC 8305 happy-eyeballs) by default, which "is intolerant of thread interruptions" — NvHTTP's polling threads are interrupted routinely, so upstream sets `.fastFallback(false)` on the shared builder (`NvHTTP.java:177–183` in the fork). The method exists in 4.11+; add the line now, bump later. OkHttp 5 is Kotlin-first; the fork's `NvHTTP` and any other OkHttp callers (Artemis added ~243 lines there) need a compile check for removed Java-facing API.
* **jMDNS 3.6.3 / BouncyCastle 1.85.2 + 1.85** (`b3a7e32a`): fork is on 3.6.2 / 1.81 — straightforward bumps; the `packaging.excludes '/META-INF/*.md'` is needed for the BC duplicate-LICENSE error.
* **Core library desugaring** (`4eb24a8d`): upstream enabled it specifically "to fix jmDNS on older Android versions" after moving to 3.6.3. The fork already ships jmDNS **3.6.2 without desugaring** and selects `JmDNSDiscoveryAgent` for every device **below Android 14** (`DiscoveryService.java:66`). If 3.6.x pulls in `java.time`/`java.util.function` APIs, discovery on API 21–25 devices is broken today. Verify on an API 21–25 emulator (look for `NoClassDefFoundError`/`NoSuchMethodError` from `javax.jmdns`); if so this is the single most impactful dependency change for the fork's older-device users. Needs #4 (Java 17 lines) first; AGP 8.13 supports it.

---

## 4. Toolchain and dependency comparison

| Item | Fork `moonlight-noir` | og/master (12.2) | Note |
|------|-----------------------|------------------|------|
| Android Gradle Plugin | 8.13.0 | **9.4.0** | AGP 9 defaults `buildFeatures.resValues` to off → fork's `resValue` lines need `resValues = true`; needs Gradle 9 + JDK 17 to run Gradle. |
| Gradle wrapper | 8.13 | **9.7.1** | |
| Java source/target | 11 | **17** | |
| Core library desugaring | no | **yes** (`desugar_jdk_libs 2.1.5`) | see §3.11 |
| `android.useAndroidX` | true (fork uses AndroidX) | property removed (upstream has no AndroidX deps) | no action |
| compileSdk | 36 | **37** | needed for #18 (`CINNAMON_BUN`) and #22 (`VERSION_CODES_FULL`) |
| targetSdk | 34 | **36** | |
| minSdk | 21 | 21 | |
| NDK | 27.0.12077973 | **29.0.14206865** | |
| `Application.mk` | android-21, `APP_SUPPORT_FLEXIBLE_PAGE_SIZES` | same | 16 KB pages already handled |
| ProGuard | `proguard-android.txt`, debug **and** release minified | `proguard-android-optimize.txt`, release only | fork choice; AGP 9 fine either way |
| `packaging.resources` | `pickFirsts META-INF/versions/9/OSGI-INF/MANIFEST.MF` | `excludes /META-INF/*.md` | take both |
| bouncycastle bcprov / bcpkix (jdk18on) | 1.81 / 1.81 | **1.85.2 / 1.85** | |
| jcodec | 0.2.5 | 0.2.5 | |
| okhttp | 4.12.0 | **5.5.0** (+ `fastFallback(false)`) | |
| jmdns | 3.6.2 | **3.6.3** | |
| ShieldControllerExtensions | 1.0.1 | 1.0.1 | |
| Fork-only runtime deps | gson 2.13.1, androidx annotation 1.9.1 / cardview 1.0.0 / appcompat 1.7.1 / preference 1.2.1 / recyclerview 1.4.0, material 1.13.0, SearchPreference v2.5.1, MPAndroidChart v3.1.0, litert 1.4.0 + litert-gpu 1.4.0, opencv 4.12.0 | — | all must survive the AGP 9 / Gradle 9 move |
| Fork-only test deps | junit 4.13.2, androidx.test core 1.7.0, robolectric 4.16, mockito 5.19.0 | — | fork has `testOptions` + root `test` aggregation in `build.gradle`; AGP 9 keeps these |
| OpenSSL (prebuilt) | **1.1.1q** (2022-07-05), `libssl.a` + `libcrypto.a` × 4 ABIs | **4.0.2** (2026-08-25), `libcrypto.a` only | `LOCAL_STATIC_LIBRARIES` drops `libssl` |
| libopus (prebuilt) | **1.5.2**, dir `libopus/` | **1.6.1**, dir `opus/` | headers for 1.6 differ (±~400 lines) |
| Reed-Solomon FEC | `moonlight-common-c/reedsolomon/rs.c` (scalar) | `nanors` submodule (`nanors/rs.c` + `deps/obl/oblas_{common,lite}.c`, NEON/SSSE3/GFNI dispatch) | Android.mk change in #5 |
| moonlight-common-c pin | ClassicOldSong `c999436` (2025-09-01) | moonlight-stream `874ac95` (2026-08-18); og/master head `f900dd4` (2026-09-26) | §5 |
| enet pin (nested) | cgutman/enet `115a10baa` (v1.3.17-75) | `aca87840` (5 commits ahead) | §5 |
| CI | `appveyor.yml` (vestigial copy of upstream's), no `.github/workflows` | AppVeyor VS2026 / JDK 25 | n/a |

For reference, the merge-base-era `app/build.gradle` (`f10085f5`, 2024-07) had NDK r27, compileSdk 34, AGP 8.5.1, Gradle 8.7, BC 1.77, OkHttp 4.12, jmDNS 3.5.9 — Artemis has been bumping AGP/Gradle/compileSdk/BC/jmDNS on its own since.

---

## 5. moonlight-common-c comparison

Submodule path: `app/src/main/jni/moonlight-core/moonlight-common-c` (origin `ClassicOldSong/moonlight-common-c`, HEAD `c999436` "Add send empty payload method", 2025-09-01). Upstream `og/master` = `f900dd4` (2026-09-26). Merge base = `5f22801` "Fix CGN subnet mask". Upstream moonlight-android 12.2 pins `874ac95`.

**Trial merges (`git merge-tree --write-tree`, objects only): `c999436` + `og/master` → clean; `c999436` + `874ac95` → clean.** Artemis's two feature commits only append (control-stream packet-type indices 12–14, `CTRL_CHANNEL_SERVERCTL 0x08`, `LiSendExecServerCmd()`, `LiSendEmptyPayload()`), and upstream's LTR-ACK work uses a separate `SS_LTR_FRAME_ACK_PTYPE` rather than a new index, so there is no table clash.

### 5.1 Upstream commits missing from the submodule (44; oldest first)

| SHA | Date | Subject | Class | Value for Artemis | Notes |
|-----|------|---------|-------|-------------------|-------|
| `82ee2d6` | 2024-09-12 (merged late 2025) | Improve support for high-resolution stats | perf / API | **high** | `receiveTimeUs/enqueueTimeUs`, `PltGetMicroseconds()` (µs since init, `CLOCK_MONOTONIC_RAW`), `LiGetMicroseconds()`, **`LiGetRTPVideoStats()`/`LiGetRTPAudioStats()`** (packet/FEC/OOS counters — ideal for Artemis's performance overlay). Drives the Android-side `0dc4c4fe` changes (§3.9). |
| `a3ebaaf` | 2024-10-20 | `uint64_t` presentationTimeMs | API | low | |
| `fdd0265` | 2024-10-20 | macOS/iOS clock | other | none | |
| `e356b2c` | 2025-10-27 | `presentationTimeMs` → **`presentationTimeUs`** + raw `rtpTimestamp` in `DECODE_UNIT` | perf / API | medium | Not consumed by `callbacks.c`; available for better pacing. |
| `1c86405` | 2025-11-06 | Vita header | other | none | |
| `e59a5f5` | 2025-11-09 | **Rewrite gamepad input batching to batch on the enqueue side** | input / perf | medium–high | Batches interleaved input from multiple controllers correctly, removes per-event allocation/dequeue overhead. |
| `2d984f4`, `6250fa2`, `6268780`, `703a069` | 2025-11 → 2026-07 | ENet submodule bumps (→ `aca8784`) | network | medium | see §5.3 |
| `20c05ed` | 2025-11-25 | Don't lock the ENet mutex in `LiGetEstimatedRttInfo()` | perf | medium | Artemis's overlay polls RTT; removes contention with the control-stream thread. |
| `b126e48` | 2025-11-25 | Better locking for batched mouse/sensor events | input / perf | medium | Unlocks before enqueue so the input thread doesn't immediately contend. |
| `0586f3d` | 2026-01-05 | **Fix thread-context leak on non-Vita platforms** | leak | medium | Every `PltCreateThread()` leaked its context struct on Android. |
| `3a377e7` | 2026-01-05 | Explicit pthread stack size only on Vita | other | low | Android threads get the default stack again. |
| `435bc6a` | 2026-01-11 | **Fix `pthread_attr` leak** | leak | medium | `pthread_attr_destroy()` after `pthread_create()`. |
| `2a5a1f3` | 2026-01-21 | **LTR ACK control messages** (Sunshine) | video / perf | medium–high | `connectionReceivedCompleteFrame(idx, isLTR)`; RFI queue generalised to `referenceFrameControlQueue`; RFI request now a packed `SS_RFI_REQUEST`. Enables Sunshine long-term-reference recovery (fewer IDR storms). |
| `305993b`, `07c32c8` | 2026-01/02 | Probe local address with a UDP `connect()` (valid port) | network | medium | Replaces `getsockname()` on the RTSP TCP socket; failure is now fatal at `STAGE_NAME_RESOLUTION` — watch for new "failed to resolve local addr" reports on exotic networks. |
| `b6b95b6`, `1d0e91d` | 2026-02-15 | CI | none | — | |
| `de364b6`, `5551d29`, `a063522`, `3872285`, `b187204` | 2026-02-19 | **nanors Reed-Solomon FEC** (SIMDe/NEON, scalar fallback) | perf | **high** | FEC recovery cost drops sharply (NEON on arm64/armv7); fewer dropped frames at high bitrate/loss. |
| `3fa9191` | 2026-02-20 | lowercase windows headers | other | none | |
| `7022b33` | 2026-03-28 | Win32 socket-error refactor + `EMSGSIZE` | other | none (needed by next row) | |
| `7b026e7` | 2026-03-24 | **Harden RTSP handling: malformed Session headers and oversized responses** | **security** | **high** | `MAX_RTSP_RESPONSE_SIZE` (1 MiB) enforced on both the ENet and TCP RTSP paths (previously a malicious/buggy host could make the client `malloc`/`extendBuffer` without bound); `strtok_r` result on the `Session:` header is NULL/empty-checked before `strdup` (previously `strdup(NULL)` → crash). |
| `40d8731`, `47b4d33` | 2026-04/06 | NXDK (Xbox) support | other | none | |
| `2600bea` | 2026-05-14 | **Dual-touchpad controllers** | input | medium | `LI_CCAP_DUAL_TOUCHPAD 0x100`, `LiSendControllerTouchEvent2(…, touchpadIndex, …)`; wire layout: `zero[2]` → `zero + touchpadIndex` (compatible). Needs a JNI binding to be useful (Steam Controller). |
| `1fddbcb`, `99c45d3` | 2026-07-03 | rswrapper header hygiene; `__has_builtin` fallback | build | low | |
| `1f76427`, `2ea4775`, `e41355e` | 2026-07 | **Switch to upstream nanors submodule** (native SIMD + GFNI runtime dispatch), bumps → `b1e3c22` | perf / build | high (with the row above) | Adds the `nanors` nested submodule; Android.mk must list `nanors/rs.c` + `deps/obl/oblas_common.c` + `oblas_lite.c`. |
| `82e2514` | 2026-07-11 | 3DS compile fix | other | none | |
| `518b244` | 2026-08-18 | MbedTLS → PSA APIs | other | none (Android uses the OpenSSL path) | |
| `874ac95` | 2026-08-18 | **`LI_CTYPE_STEAM 0x04`** | input | medium | Required by the SDL sync (`c2e224eb`). |
| `d85371c` | 2026-09-08 | **Fix RFI after a multi-block frame loss** | video | high | `reportedLostFrame` was left set after a loss, suppressing the report for the *next* lost frame → host never invalidated it. |
| `be43885` | 2026-09-08 | **Fix handling of a partially dropped IDR frame** | video | high | Dropping a half-processed IDR now forces an IDR wait instead of decoding garbage. |
| `62e0663` | 2026-09-08 | **Don't speculatively report losses if RFI is disabled** (closes #147) | video | high | Prevented "120 consecutive frames dropped" when a speculative loss report triggered an IDR wait that the depacketizer never requested. |
| `5a26329` | 2026-09-26 | LICENSE section-7 exception | legal | low | |
| `f900dd4` | 2026-09-26 | **`MODIFIER_EXTENDED 0x10`** (Sunshine extension; stripped for GFE) | input | medium | Distinguishes Enter vs Numpad Enter etc. Artemis's scancode-aware `translate()` could set it. Not in 12.2's pin (`874ac95`); needs `og/master`. |

### 5.2 Artemis-only commits (2 + 4 upstream merges)

| SHA | Date | Subject | Content |
|-----|------|---------|---------|
| `84af637` | 2024-09-11 | Add server cmd support over Control Stream | Apollo protocol extension: packet types `0x3000/0x3001/0x3002` (exec server cmd / set clipboard / file-transfer nonce) in the Gen7-encrypted table, `IDX_EXEC_SERVER_CMD..IDX_FILE_TRANSFER_NONCE_REQUEST` (12–14), `CTRL_CHANNEL_SERVERCTL 0x08`, `AP_SERVER_CMD_PACKET`, `LiSendExecServerCmd(uint8_t)`; async callbacks for clipboard/nonce. |
| `c999436` | 2025-09-01 | Add send empty payload method | `LiSendEmptyPayload()` — 4-byte `AA 55 AA 55` on the server-control channel as a Wi-Fi-sleep keep-alive. |
| `40bec19`, `40a4e68`, `ad329b2`, `b4d2af7` | 2024-10 → 2025-09 | Merge upstream/master | plain merges |

Content three-dot diff: Artemis-only = 4 files, +66/−1; upstream-only = 32 files, +1146/−1197 (nanors tables moved into the nested submodule).

### 5.3 enet (nested submodule of moonlight-common-c)

Fork pin `115a10baa` (cgutman/enet, v1.3.17-75) vs upstream pin `aca87840` — 5 commits ahead, 6 files, +136/−21:
* `dea6fb5` FreeBSD `IP_RECVDSTADDR`/`IP_SENDSRCADDR` (n/a on Android).
* `78cc9b4` **Only pass the peer's local address if the host was wildcard-bound** — `enet_host_create` records `wildcardBind`; `enet_socket_send` gets the explicit source address only then. Affects Linux/Android send paths on multi-homed devices (Wi-Fi + VPN/USB tethering): avoids forcing a stale/incorrect source address. Worth having.
* `c7353c0` lowercase Windows headers, `0eb84dc` NXDK, `aca8784` 3DS — n/a.
Taking common-c `874ac95`/`f900dd4` brings the enet pin along automatically.

---

## 6. Recommended porting order

**Phase 0 — mechanics (decide once).** Pick either the ignore-EOL workflow or a normalization (§7). Create a topic branch; for every pick use `git cherry-pick -Xignore-space-at-eol <sha>` and run `unix2dos` (or `sed -i 's/\r*$/\r/'`) on touched CRLF files before committing.

**Phase 1 — pure Java fixes, no toolchain change (all compile on compileSdk 36):**
1. `abde6021` LED crash/ANR fix (+ `8d720748`, and the `ControllerHandler` half of `ad861490`).
2. `3c6a0d12` rumble via `getDefaultVibrator()` (remove the fork's older `VIBRATOR_SERVICE` assignment).
3. `68adf9ec` SPS constraint/level_idc only below Oreo.
4. `b9c5eddd` plus-key shift (and mirror in Artemis's `KeyBoardController` if it emits `KEYCODE_PLUS`).
5. `8974dcda` F13–F24/Print/Screenshot (+ `KeyboardTranslator` half of `ad861490`; hand-resolve the `VK_F13` placement).
6. `0711e236` by hand (wrap `UiHelper.setGameModeStatus` in try/catch); `280454fd` by hand (two `InitPacket` lines); skip `583f662a`.
7. `98c12beb`'s `NvHTTP` line `.fastFallback(false)` (safe on OkHttp 4.12).

**Phase 2 — build chain, in this order so each step stays buildable:**
8. `9221a0ca` Java 17 → `4eb24a8d` desugaring (test discovery on an API 21–25 emulator before and after) → `b3a7e32a` BC 1.85.2/1.85 + jmDNS 3.6.3 + `excludes` → `98c12beb` OkHttp 5.5 (compile-check Artemis's OkHttp usages) → `1fa0e2a0` NDK r29.
9. compileSdk 37: either keep AGP 8.13 (expect a "compileSdk newer than tested" warning) or take `9d8b073c` + `801dba1b` (AGP 9.4 / Gradle 9.7.1; add `buildFeatures.resValues = true`; re-check the fork's `splits`, `testOptions`, jitpack deps and the root `build.gradle` test aggregation). Then `6d4c64a5` (producer throttling) and `ddb674a9` (keyboard capture + `CAPTURE_KEYBOARD` permission).
10. `4b2221d3` targetSdk 36 last, with an edge-to-edge/insets pass over Artemis's overlays, PiP and virtual controller.

**Phase 3 — native:**
11. `31b70030` OpenSSL 4.0.2 + libopus 1.6.1 (drop `libssl` from `LOCAL_STATIC_LIBRARIES`; `libopus/` → `opus/`).
12. Submodule: in ClassicOldSong/moonlight-common-c (or a fork of it), `git merge og/master` (clean) — or pin exactly `874ac95` to match 12.2 (also clean, but loses `MODIFIER_EXTENDED` and the three September RFI fixes, which are the most valuable video changes; prefer `f900dd4`). Init `nanors` and the new `enet`. Port `0dc4c4fe` (Android.mk nanors sources/includes, µs fields in `callbacks.c`/`MoonBridge`/`VideoDecoderRenderer`/`MediaCodecDecoderRenderer`, **`baseTimestampUs` normalization**, audit §3.9's `nanoTime` comparisons), then `c2e224eb` (SDL sync). Smoke-test: FEC recovery under induced loss, RFI with Sunshine, multi-controller input, Artemis's stats overlay (consider switching it to `LiGetRTPVideoStats()`).

**Phase 4 — optional / skip:** `3df0103a` (hand-prune the fork's dead `< LOLLIPOP` branches instead), `b55b4b6d`/`5c0c2390` (only with Weblate), `3f114ac7`/`578f38f6`/`b48494cb` (skip). Follow-ups unlocked by Phase 3: `MODIFIER_EXTENDED` from the fork's scancode path (Numpad Enter), `LiSendControllerTouchEvent2` JNI for dual-touchpad controllers, `LI_CTYPE_STEAM` in `ControllerHandler`'s type guessing.

---

## 7. Line-ending normalization: recommendation

Facts measured:
* Fork: 122/171 Java files and 107 other tracked text files are CRLF (e.g. `ControllerHandler.java` 3,472/3,472 lines CRLF; `Game.java` is LF). Upstream and the merge base are LF everywhere. Whitespace-insensitive, the fork's real divergence in the touched Java files is ~3,000 added / ~575 removed lines; the raw diff is 10,684/7,538 because of EOL.
* A simulated normalization (`.gitattributes`: `* text=auto` + `*.a *.png *.jar *.tflite *.onnx binary`, then `git add --renormalize .`) is a 230-file, +38,915/−38,909 commit. After it, plain cherry-picks reproduce the `-Xignore-space-at-eol` results **exactly** (same 15 clean / 14 conflicting, same files).
* `-Xignore-space-at-eol` alone leaves mixed endings in the picked files (added lines LF, rest CRLF) — fixable with `unix2dos` on the touched files before committing; after a renormalization the problem disappears.

Trade-off: the fork's actual upstream is ClassicOldSong/moonlight-android (CRLF). If the fork normalizes and Artemis does not, every future `git merge upstream/moonlight-noir` hits the same whole-file noise in the other direction (mitigable with `git merge -Xrenormalize`/`merge.renormalize=true`, but conflicts in CRLF files become harder to read and every merge touches hundreds of files). A half-measure — adding `* text=auto` without renormalizing — is worse: the next edit to any CRLF file silently rewrites the whole file to LF in that commit.

Recommendation:
* If the fork intends to keep pulling Artemis regularly: **do not normalize unilaterally.** Port with `git cherry-pick -Xignore-space-at-eol` (proven equivalent) plus `unix2dos` on touched files; keep `.gitattributes` absent (or add it only with binary markers). Consider proposing the normalization upstream to Artemis — it is a one-commit change there and would benefit everyone pulling from moonlight-stream.
* If the fork is diverging from Artemis for good (or Artemis accepts the normalization): do the one-time `* text=auto` + `git add --renormalize .` commit **first**, as a standalone commit with no other changes, then port with plain cherry-picks; set `merge.renormalize=true` locally for any later Artemis merges.
