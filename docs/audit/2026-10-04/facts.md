# Established facts (for the final report)

## Fork identity
- jlobue10/moonlight-android, single branch `moonlight-noir`, HEAD c5cf27f (2026-09-09) = ClassicOldSong/moonlight-android (Artemis Android) moonlight-noir, 0 ahead / 0 behind. No fork-specific commits. versionName 20.2.6, versionCode 57. No CI workflows (only issue templates).
- Artemis last merged moonlight-stream/moonlight-android at 27ded2ad (2024-11-14). moonlight-stream master is v12.2 (2026-09-12); 55 commits since the merge-base (many Weblate).
- ClassicOldSong `next` branch = abandoned 2024 "MoonlightNext" Kotlin/Compose experiment (474 behind) — irrelevant.
- Divergence from moonlight-stream (app/src/main/java + jni): 333 files, +55331/-51823. Biggest: ControllerHandler (+297/-127 real), MediaCodecDecoderRenderer (+565/-53 real), Game.java (+2123/-459), MediaCodecHelper, Stereo3DRenderer (new, 1149 lines), KeyMapper (new), StreamSettings, PcView/AppView, NvConnection, KeyBoard* virtual keyboard, ExternalDisplayControlActivity, TrackpadContext, ProConController, EditProfileActivity, DeviceUtils, AddComputerManually.
- 118 Java files use CRLF (upstream LF) → whole-file diffs that are mostly noise (ComputerManagerService.java: 0 real changes).

## Toolchain / deps (fork)
- AGP 8.13.0, Gradle 8.13, NDK 27.0.12077973, compileSdk 36, targetSdk 34, minSdk 21, Java 11, minify on (debug+release), ABI splits (x86, x86_64, armeabi-v7a, arm64-v8a), ndk.debugSymbolLevel FULL, APP_SUPPORT_FLEXIBLE_PAGE_SIZES true, LOCAL_BRANCH_PROTECTION standard, gwpAsanMode=always.
- Deps: bcprov/bcpkix-jdk18on 1.81, jcodec 0.2.5, okhttp 4.12.0, jmdns 3.6.2, ShieldControllerExtensions 1.0.1, gson 2.13.1, androidx annotation 1.9.1 / cardview 1.0.0 / appcompat 1.7.1 / preference 1.2.1 / recyclerview 1.4.0, material 1.13.0, SearchPreference v2.5.1, MPAndroidChart v3.1.0, litert 1.4.0 + litert-gpu 1.4.0, opencv 4.12.0. Tests: junit 4.13.2, androidx.test core 1.7.0, robolectric 4.16, mockito 5.19.0.
- Prebuilt native: OpenSSL 1.1.1q (5 Jul 2022, EOL branch, from cgutman/moonlight-mobile-deps; built no-shared no-ssl3 no-stdio no-engine no-hw); libopus (version TBD by agent). moonlight-common-c submodule = ClassicOldSong fork @ c999436, merge-base with moonlight-stream common-c 5f22801 (2025-07-15): 44 upstream commits missing, 6 Artemis-only; src diff +574/-1084. enet pin 115a10baa (cgutman/enet).
- Assets: midas-midas-v2-w8a8.tflite 17 MB (SBS-3D depth model), assets/config 16K.

## Manifest facts
- Permissions: INTERNET, ACCESS_NETWORK_STATE, POST_NOTIFICATIONS, REORDER_TASKS, VIBRATE, WAKE_LOCK, ACCESS_WIFI_STATE, TV EPG read/write, CHANGE_WIFI_MULTICAST_STATE (<=33).
- allowBackup=true; backup rules exclude only sharedpref (client.crt/client.key in filesDir are NOT excluded → pairing identity travels in cloud backup / D2D transfer; same as upstream).
- network_security_config: cleartext permitted, system trust anchors only.
- Exported: PcView (MAIN), ShortcutTrampoline (VIEW content/file *.art, BROWSABLE, mimeType */*), AddComputerManually (VIEW art:// BROWSABLE), PosterContentProvider (exported=true, no permission; openFile parses appId with Integer.parseInt → NumberFormatException from a hostile caller), StartExternalDisplayControlReceiver (no exported attr; no intent-filter → not exported), KeyboardAccessibilityService (exported=false, BIND_ACCESSIBILITY_SERVICE), FileProvider (exported=false; paths: external-path ".", external-files-path, cache-path, external-cache-path, files-path "." — very broad).
- Game activity: preferMinimalPostProcessing=true (ALLM), supportsPictureInPicture, singleTask, NVIDIA immediateInput/rawCursorInput meta. game_mode_config: no battery/performance game modes, no downscaling, no FPS override.
- NvHTTP: pins serverCert after pairing (checkServerTrusted/HostnameVerifier), default trust manager fallback; PairingManager uses SecureRandom for PIN & salt, SHA-256 (Gen7+), verifies server signature; AndroidCryptoProvider: RSA-2048 SHA256withRSA, client.crt/client.key in filesDir.
