# Audit: jlobue10/moonlight-android (Artemis Android fork)

Date: 2026-10-04. Audited commit: `c5cf27f4` on branch `moonlight-noir` (versionName 20.2.6, versionCode 57).
Method: five parallel deep-dives (security, performance, picture quality, upstream-porting analysis, ecosystem research), with the headline findings re-verified by hand against the source. Each finding is tagged **VERIFIED** (re-read in the source by the coordinating reviewer), **CONFIRMED** (established by an auditor reading the code end to end), or **SUSPECTED** (needs a device test). Detailed per-area reports with every file:line reference are listed in the appendix.

## TL;DR — what to do, in order

1. **Bump `moonlight-common-c` to upstream and port the Android side of `0dc4c4fe`** (clean merge; brings the RTSP crash/unbounded-allocation hardening a malicious host can trigger today, the Sept-2026 frame-loss recovery fixes, SIMD FEC, input batching, lock-free RTT, `MODIFIER_EXTENDED`). Watch the one real hazard: the fork's frame-age drop logic assumes the old timestamp epoch (§6, Phase 3).
2. **Close the two Artemis-only security holes**: the accessibility service forwards every hardware key on the device to the host whenever a stream is connected, even from other apps (S2); `art://` links can start a stream with no confirmation and auto-pair the device to an attacker-chosen host with a URL-supplied PIN (S3).
3. **Harden host-response parsing** (shared with upstream): any LAN host can kill the app via a non-numeric `status_code`/port (S4); a cert mismatch silently falls back to plain-HTTP `serverinfo` that overwrites the stored host (S5).
4. **Restore the Frame Pacing setting and upstream's render loop** (P1/Q1): both branches of Artemis's "latency profile" block force Balanced, so three of the five pacing modes are dead; the render thread busy-polls the decoder ~1,000×/s, and two presentation paths run at once, which presents frames out of order during jitter. Delete `applySurfaceFrameRate()` (P2/Q3), which overrides the panel refresh request.
5. **Cherry-pick the upstream v12.2 batch** (all tested, apply with `-Xignore-space-at-eol`): LED ANR fix, rumble fix, H.264 SPS no-rewrite on Oreo+, F13–F24 and '+' keys, OpenSSL 1.1.1q → 4.0.2 (EOL crypto on the packet path), BouncyCastle 1.85.2, OkHttp `fastFallback(false)`; then compileSdk 37 for producer-throttling-off (Android 17) and native keyboard capture (Android 16.1), and targetSdk 36.
6. **Picture quality**: let Auto choose AV1 on capable hardware (today AV1 is only ever used when forced, and the "Allow AV1 by default" commit was a no-op, Q2); fix external-display HDR gating and the millihertz refresh (Q4/Q5); gate MediaTek RFI (Q6); lazy-load the 23 MB OpenCV library instead of on every stream start (P4).
7. **Take three open Artemis PRs**: #603 (Amlogic HEVC low-latency), #601 (4K startup disconnect), #590 (capability-gated low latency with tests).
8. **Host side**: Apollo v0.4.8 is unpatched for CVE-2026-32253 (critical auth bypass; the fix is only on master and the maintainer declined a release) and likely inherits the five Sept-2026 Sunshine advisories — keep it LAN/VPN-only or move to Sunshine ≥ 2026.914 / a patched Vibepollo build.

---

## 1. What this fork actually is

- **It is a pure mirror of Artemis Android.** The fork has one branch and is byte-identical to ClassicOldSong/moonlight-android `moonlight-noir` at `c5cf27f4` (2026-09-09, "Merge pull request #573 … right alt command c/v mapping"). There are no fork-specific commits, so everything below describes Artemis as you ship it. (VERIFIED: `git rev-list --left-right --count` = 0/0.)
- **Artemis itself is stalled.** Last stable release v20.2.6 (2025-08-14); last pre-release v20.3.0-experimental.9 (2025-09-16); after that only zh-rTW translations and one input PR in a year. No sign that development moved to another repo. 26 open PRs, several of them performance/quality work (see §5.4).
- **Artemis last merged upstream Moonlight in 2024** (merge-base with moonlight-stream master: `f10085f5` 2024-07-27 for code, `27ded2ad` 2024-11-14 for translations). moonlight-stream released v12.2 on 2026-09-12 with 29 substantive commits the fork lacks (§6).
- **Divergence from upstream Moonlight is large**: 333 files, +55k/−52k lines in `app/src/main/java` + `jni`. Much of it is line-ending noise: 118–122 of the fork's Java files are CRLF while upstream is LF (e.g. `ComputerManagerService.java` is functionally identical to upstream but diffs as a whole-file rewrite). Real divergence is concentrated in `Game.java`, `ControllerHandler.java`, `MediaCodecDecoderRenderer.java`, `MediaCodecHelper.java`, `StreamSettings.java`, the virtual keyboard/controller packages, and the new `Stereo3DRenderer`/`KeyMapper`/`ExternalDisplayControlActivity`. (VERIFIED.)
- **Toolchain**: AGP 8.13 / Gradle 8.13 / Java 11 / NDK r27 / compileSdk 36 / **targetSdk 34** / minSdk 21; upstream v12.2 is AGP 9.4 / Gradle 9.7.1 / Java 17 + desugaring / NDK r29 / compileSdk 37 / targetSdk 36. Google Play's 2026 target-SDK floor will reject targetSdk 34 for updates (relevant only if you publish).
- **Native pins**: OpenSSL **1.1.1q** (July 2022; the 1.1.1 branch has been end-of-life since 2023-09-11; `libssl.a` is linked but never referenced), libopus 1.5.2, moonlight-common-c = ClassicOldSong fork `c999436` (2025-09-01) which is **44 commits behind** moonlight-stream common-c, enet `115a10b`. (VERIFIED.)
- No CI: `.github/` holds only issue templates. Builds are manual.

---

## 2. Security findings (ranked)

No Critical or High findings. The core trust model is sound and unchanged from upstream: the paired server certificate is pinned by DER equality on every HTTPS request with no accept-all path, pairing follows the protocol correctly with `SecureRandom` for PIN/salt/challenge and server-signature verification, the identity is an RSA-2048 key in app-private storage, SQLite is parameterized, and nothing the host sends is executed or used as a path/URL/intent/shell command. (VERIFIED for TLS/pairing/key storage.)

### S1 — Medium — Native stream parser lacks upstream's RTSP hardening (malicious host → crash / unbounded allocation) — VERIFIED
- `moonlight-common-c/src/RtspConnection.c:1212`: `sessionIdString = strdup(strtok_r(sessionId, ";", &strtokCtx));` — a `Session: ;` reply to `SETUP streamid=audio` makes `strtok_r` return NULL → `strdup(NULL)` → crash. RTSP responses are accumulated with no size cap at `:334` (ENet) and `:440` (TCP).
- Upstream fixed both in `7b026e7` (2026-03-24, "Harden RTSP handling for malformed Session headers and oversized responses": `MAX_RTSP_RESPONSE_SIZE` = 1 MiB enforced at three points, token NULL/empty check). moonlight-qt v6.2.0 (2026-10-04) lists CVE-2026-33546/33547 as "malicious host can crash Moonlight when connecting"; the mapping to this commit is plausible but not confirmed by any public record reachable from here.
- Impact: crash/DoS at handshake from a compromised or spoofed host. Not code execution.
- Fix: merge moonlight-stream common-c into the submodule (`git merge-tree` shows a **clean** merge against both `874ac95` and `f900dd4`; Artemis adds only the server-command packet types and `LiSendEmptyPayload`). This one move also brings the September 2026 RFI/IDR loss fixes, SIMD FEC, enqueue-side gamepad batching, lock-free RTT, dual touchpad, `LI_CTYPE_STEAM`, `MODIFIER_EXTENDED`, and two thread-context/`pthread_attr` leak fixes. See §6 for the one real porting hazard (`baseTimestampUs`).

### S2 — Medium — Accessibility service forwards every hardware key on the device to the host while a stream is connected (Artemis-only) — VERIFIED
- `KeyboardAccessibilityService.java:22-49`: `onKeyEvent` checks only `Game.instance.connected` and a small blacklist, then calls `Game.instance.handleKeyDown/Up` and returns `true` (consumes the key). `FLAG_REQUEST_FILTER_KEY_EVENTS` delivers all key events system-wide; `info.packageNames` only filters accessibility *events*, not key filtering. The XML also declares `canRetrieveWindowContent="true"` and `flagRetrieveInteractiveWindows`, which nothing uses.
- `Game` stays connected while paused (PiP is entered automatically on leave; split-screen is allowed), so with the service enabled, keys typed into another app — a password manager, a banking app — are swallowed and sent to the host.
- Fix: gate on `Game.instance.hasWindowFocus()` and `!isInPictureInPictureMode()` (accept `ExternalDisplayControlActivity` focus for dual-display mode), and drop the window-content capabilities from `keyboard_accessibility_service.xml`.

### S3 — Medium — `art://` deep links and exported activities can start a stream without confirmation and can auto-pair to an attacker-chosen host (Artemis-only) — VERIFIED
- `AddComputerManually` is exported + BROWSABLE for scheme `art`; `ShortcutTrampoline` is exported + BROWSABLE for `content`/`file` `*.art`.
- `art://launch?host_name=…&app_id=…` (`AddComputerManually.java:293-310`) forwards to `ShortcutTrampoline`, which — if the host is ONLINE and PAIRED — calls `startActivities(createStartIntent(...))` with **no user confirmation** (`ShortcutTrampoline.java:144-155`). A web page or any installed app can thus open a stream to your PC launching an attacker-chosen app id and route the device's input to it. Host name/UUID are advertised in cleartext `serverinfo` to anyone on the LAN.
- `art://host:port?name=…&pin=…&passphrase=…` (`:205-216` → `PcView.java:257-267, 308-318`) adds the host after one "Proceed" dialog and then **auto-completes OTP pairing with the URL-supplied PIN and passphrase** once the host polls online. This removes the only out-of-band step of the pairing protocol: the device ends up paired to a host the user never typed a PIN into.
- Also a crash: without an explicit port, `uri.getPort()` = −1 is stored and `AddressTuple` throws `IllegalArgumentException("Invalid port")` in `PcView.onCreate`.
- Fix: always confirm host + app for `.art`/`art://` launches; never accept `pin`/`passphrase` from a URI (at most prefill the OTP dialog); default the port when −1; escape the manifest `pathPattern` (`.*\\.art`).

### S4 — Medium — Any LAN host can kill the app via unchecked integer parsing of `serverinfo` (shared with upstream) — VERIFIED
- `NvHTTP.java:329` `(int)Long.parseLong(xpp.getAttributeValue(…, "status_code"))` throws `NumberFormatException` on a missing/non-numeric attribute; same for `getHttpsPort`/`getCurrentGame`/`getServerAppVersionQuad`. `ComputerManagerService.tryPollIp` catches only `XmlPullParserException | IOException` and runs on a bare `Thread`, so the unchecked exception kills the process. Reachable from an mDNS advertisement before any pairing, and it repeats every launch while the attacker is present.
- Upstream v12.2 has identical code (not fixed).
- Fix: catch `RuntimeException` in `tryPollIp`; treat missing/invalid `status_code` as malformed (`XmlPullParserException`); wrap the `parseInt` sites; validate the mDNS port range before building `AddressTuple`.

### S5 — Low — Cert-mismatch HTTPS failure falls back to plain-HTTP `serverinfo` that overwrites the stored host (shared with upstream) — VERIFIED
- `NvHTTP.java:355-383`: on `SSLHandshakeException` with a `CertificateException` cause the code returns the **HTTP** `serverinfo` as authoritative; `ComputerDetails.update()` then copies name/addresses/MAC/pairState/server-command labels from it. A LAN attacker advertising the victim PC's UUID can make the entry show ONLINE + PAIRED with their own address and MAC (WoL goes to them). Launch/stream still fail (pinned HTTPS), so impact is confusion/DoS.
- Fix: for a host with a pinned cert, do not merge HTTP-sourced details on cert mismatch; surface a "certificate changed, re-pair" state instead; never take `PairStatus` from HTTP for such a host.

### S6 — Low — Dependency / toolchain hygiene — VERIFIED versions
| Component | Fork | Upstream v12.2 | Note |
|---|---|---|---|
| OpenSSL (static) | 1.1.1q + unused libssl | 4.0.2, libcrypto only | EOL branch on the packet path (every control/input/audio packet is decrypted by it). Only `EVP_aes_128_gcm/cbc` + `RAND_bytes` are used, so none of the 1.1.1r–w CVEs reach this code; this is "unmaintained crypto", not a known hole. Upstream `31b70030` applies cleanly. |
| bcprov/bcpkix | 1.81 | 1.85.2 / 1.85 | CVE-2026-13506 (ASN.1 nesting-depth guard reset → DoS, < 1.85, published 2026-08-03) is **not reachable from a host**: the host's cert is parsed by the platform `CertificateFactory` (`PairingManager.java:78`), BC only touches the app's own key/cert files. Upgrade anyway. |
| OkHttp | 4.12.0 | 5.5.0 + `.fastFallback(false)` | No client CVE; take the `fastFallback(false)` line now (exists since 4.11). |
| jmDNS | 3.6.2 (no desugaring) | 3.6.3 + core-library desugaring | Upstream found jmDNS 3.6.x broken on older Android without desugaring; the fork uses jmDNS below Android 14 → **test discovery on an API 21–25 device/emulator**. |
| MPAndroidChart | present | — | Unused (no references outside proguard rules); remove. |
| targetSdk | 34 | 36 | Play 2026 floor; also edge-to-edge/predictive-back validation needed for Artemis overlays. |

### S7 — Low — Artemis feature-specific
- **Clipboard sync** (opt-in, off by default): on every focus gain the whole device clipboard is POSTed to the host; on every focus loss the host's clipboard replaces the device's, read with an unbounded `ResponseBody.string()` (`Game.java:2248-2257, 2354-2404`). Only the pinned host can trigger it. Fix: cap the fetch (e.g. 64 KiB), don't auto-overwrite on focus loss.
- **OTP pairing** (`PairingManager.java:212-227`): `sha256(pin + salt + passphrase)` goes over cleartext HTTP (port 47989, by protocol design); a passive LAN sniffer can brute-force 10⁴ PINs × short passphrases offline. The dialog enforces ≥ 4 chars, the deep-link path enforces nothing. Fix: require a long passphrase; SUSPECTED impact (depends on Apollo's OTP window/single-use semantics).
- **Root flavor** (API ≤ 25 only): `EvdevCaptureProvider.java:46` binds the evdev socket on all interfaces, first connector wins → bind to loopback.
- **Backups** (same as upstream): `client.key`/`client.crt`/`computers4.db` are included in Auto Backup and device-to-device transfer (only `sharedpref` is excluded); a restored backup streams without re-pairing. Deliberate trade-off; exclude `files/client.key` etc. if you want one-device-one-identity.
- **`PosterContentProvider`** (exported, same as upstream): the `uuid` path segment is not validated, but the final component is forced to `<int>.png` under the box-art cache, so traversal yields nothing useful; a non-numeric id raises `IllegalArgumentException` to the caller (no crash of the app). Validate with `UUID.fromString`.
- **Debug builds only**: `NvHTTP` verbose logging prints pairing parameters and the stream AES key (`rikey`) in URLs.
- Artemis-only native hunks reviewed: packet types `0x3001/0x3002` are marked `needsAsyncCallback` but have no handler → in debug builds a host sending them trips `LC_ASSERT` (abort); release builds drop them silently. Remove the dead entries.

---

## 3. Performance findings (ranked)

### P1 — High — The Frame Pacing setting is dead and the render loop busy-polls the decoder — VERIFIED
- `Game.java:691-705`: both branches of the "latency profile selection" block end with `prefConfig.framePacing = FRAME_PACING_BALANCED`. The Cap-FPS block right after (`:757`) and the renderer's non-balanced path (`MediaCodecDecoderRenderer.java:1284-1429`, the EWMA/drop-threshold code) are unreachable; "Minimum latency", "Cap FPS" and "Max smoothness" in Settings do nothing, and the perf log still reports the user's choice.
- `MediaCodecDecoderRenderer.java:1211-1260`: every loop iteration allocates a new `BufferInfo`, drains the codec non-blocking and releases the newest frame immediately, then blocks for 2 ms (0.5 ms with "prefer lower delays"), then again for 250–500 µs. That is on the order of 1,000+ `dequeueOutputBuffer` round-trips per second on a `THREAD_PRIORITY_URGENT_DISPLAY` thread for the whole session (upstream blocks 50 ms; the codec wakes the waiter when output is ready, so polling buys no latency). The immediate-release path also races the Choreographer path (`doFrame`), so a frame completing between polls can be presented *before* its predecessor, and frames rendered that way are never counted in the overlay's rendering FPS.
- Fix: delete the two `framePacing = BALANCED` assignments and the `LATEST_ONLY_LOW_LATENCY` block / `releaseWithPolicy` / backoff; restore upstream's loop (one `BufferInfo`, `dequeueOutputBuffer(info, 50000)`, honour `prefs.framePacing`). Map the "prefer lower delays" toggle onto `FRAME_PACING_MIN_LATENCY`. Magnitude of the CPU/battery saving is SUSPECTED until profiled; the ordering race and dead options are VERIFIED.

### P2 — High — `applySurfaceFrameRate()` undoes the display frame-rate request — VERIFIED
- `Game.surfaceCreated()` (`Game.java:3836`) asks for the chosen panel refresh with `FRAME_RATE_COMPATIBILITY_FIXED_SOURCE` + `CHANGE_FRAME_RATE_ALWAYS` (upstream logic, "lowest possible display latency"). Then the decoder's `configureAndStartDecoder` (`MediaCodecDecoderRenderer.java:611 → 2412-2424`) calls `surface.setFrameRate(streamFps, FRAME_RATE_COMPATIBILITY_DEFAULT)` — the two-arg overload implies `CHANGE_FRAME_RATE_ONLY_IF_SEAMLESS` — which runs later and therefore wins, inviting a 120 Hz panel to drop to 60 Hz for a 60 fps stream (an extra scan-out period of latency, device-dependent). It is re-applied on every codec recovery.
- Fix: delete `applySurfaceFrameRate()` and its call. Also delete the reflective `SurfaceView.setFrameRate(float,int)` lookup at `Game.java:928-931` (no such method; a swallowed `NoSuchMethodException`).

### P3 — High on Android 17+ — Missing upstream `6d4c64a5` (disable surface producer throttling) — VERIFIED
- One version-gated line after the `setFrameRate` block in `surfaceCreated`: `if (SDK_INT >= VERSION_CODES.CINNAMON_BUN) holder.getSurface().setProducerThrottlingEnabled(false);`. Needs compileSdk 37 (or reflection). Lets the decoder queue the newest frame without the compositor's producer back-pressure.

### P4 — High (startup) — OpenCV's 23 MB library is loaded on the main thread at every stream start, even in 2D — VERIFIED
- `StreamContainer.init()` line 74 writes `Stereo3DRenderer.isMovieMode`, a non-final static; the write triggers the class initializer, whose static block calls `OpenCVLoader.initLocal()` → `System.loadLibrary("opencv_java4")` (23.5 MB arm64, 15.6 MB armv7) inside `Game.onCreate`. The 3D renderer and the 17 MB `.tflite` are otherwise correctly bypassed in 2D.
- Fix: move `isMovieMode` into `StreamContainer` / the renderer constructor; load OpenCV lazily in `Stereo3DRenderer`.

### P5 — Medium — Scalar Reed-Solomon FEC, per-packet-queue input batching, RTT query under the ENet mutex — CONFIRMED divergence
- The fork's common-c still uses `reedsolomon/rs.c` (table lookup + XOR per byte); upstream replaced it with SIMD `nanors` (NEON on arm64) in Feb–Jul 2026. FEC decode runs on the video receive thread exactly when the link is already losing packets. Same bump brings enqueue-side gamepad batching (`e59a5f5`), lock-free `LiGetEstimatedRttInfo` (`20c05ed`; the overlay polls it once a second from the receive thread), and the RFI/IDR fixes. `Android.mk` must switch to the `nanors/*.c` sources and the new nested submodule.

### P6 — Medium — Missing upstream `abde6021` (controller LED ANR/crash) and `3c6a0d12` (rumble via `VibratorManager`) — CONFIRMED, both apply with `-Xignore-space-at-eol`
- `ControllerHandler.java:2426-2459`: `openSession()`/`getLights()`/`requestLights()` are synchronous binder calls executed on the control-stream callback thread; before Android 14 `hasRgbControl()` is true for every light, so any pad with a player LED triggers them. `lightsSession.close()` in `destroy()` is unguarded.
- Rumble uses the deprecated `VIBRATOR_SERVICE`, which returns a non-working vibrator on some devices (upstream #1454).

### P7 — Medium — Missing upstream `68adf9ec` (stop rewriting H.264 SPS on Oreo+) — VERIFIED absent
- `MediaCodecDecoderRenderer.java:1736-1740` clears `constraint_set4/5`, `:1917-1931` lowers `level_idc` on every H.264 IDR regardless of OS version; upstream gates both to `< O`. Modern Codec2 decoders size buffers from the stream and can reject a mis-declared level; clearing the constraint flags discards the encoder's "no B-frames" declaration. Two one-line condition changes.

### P8 — Medium — Decode-latency bookkeeping is a cross-thread `LongSparseArray<Long>` with unbounded growth — CONFIRMED
- `MediaCodecDecoderRenderer.java:58, 86-98, 1688`: `put` on the receive thread, `get/delete` on the render thread, not thread-safe (torn reads / `ArrayIndexOutOfBounds` that would escape the `catch (IllegalStateException)` and kill the render thread); entries for frames dropped without rendering are never deleted; one boxed `Long` per frame. Upstream computes `uptimeMillis() − presentationTimeUs/1000` with no map.

### P9 — Medium — "Prevent packet loss" option sends 50 reliable ENet packets/s from the UI thread — VERIFIED
- `Game.java:333-338`: a `Handler` tick every 20 ms calls `LiSendEmptyPayload()` (`ENET_PACKET_FLAG_RELIABLE`, so each needs an ACK). Opt-in. If the goal is to keep the Wi-Fi radio awake, the `WIFI_MODE_FULL_LOW_LATENCY` lock the app already holds is the sanctioned mechanism; otherwise make it unreliable, ≥ 100 ms, and drive it from the native control-stream timer.

### P10 — Medium (3D mode only) — Stereo 3D path: synchronous `glReadPixels` every frame, 1 ms sleep-spin for inference in movie mode, continuous rendering, per-frame `Log.d` — CONFIRMED
- `Stereo3DRenderer.java:414-524, 647-658, 464-470`; a PBO async read-back exists (`:660-687`) but is never called. Also the unused dummy `SurfaceView` left under the `GLSurfaceView` (`StreamContainer.java:83-93`).

### P11 — Low
- Release build uses `proguard-android.txt` (carries `-dontoptimize`) instead of upstream's `proguard-android-optimize.txt`, and keeps `org.opencv.**`, `org.tensorflow.lite.gpu.**`, MPAndroidChart wholesale.
- Perf overlay: three `TrafficStats` binder calls per second plus string formatting on the video receive thread (`MediaCodecDecoderRenderer.java:1795-1796, 1855-1856`).
- Dead code: the "C2 sleep watchdog" sits outside the render loop and runs once at shutdown (`:1479-1494`); `applyExtraVendorOptions()`, `isMTKDecoderName()`, the `forceTightThresholds` reflection, several `if (SDK_INT >= 21) … else if (SDK_INT >= 21)` ladders.
- `Game.java:890-934` `setFixedSize(prefConfig.width, prefConfig.height)` uses the un-inverted resolution in portrait and contradicts the retained upstream comment about PiP.
- Leaked `DisplayManager.DisplayListener` capturing the activity in external-display mode (`Game.java:1011-1030`).
- Per-event allocations in the touch path (`new float[2]`, a `String`-keyed sensitivity map).
- "Warp" pacing modes request 2×/4× the stream frame rate from the host and discard most frames; the option list does not say so.
- Things that are already right: SurfaceView zero-copy path, ALLM via `preferMinimalPostProcessing`, unbuffered input dispatch, upstream-identical audio renderer (2-frame buffer, low-latency mode, 40 ms queue cap by *duration* — so the moonlight-qt audio-queue fix of May 2026 does not apply here), native Opus decode into a preallocated buffer, one JNI upcall per frame, FEATURE_LowLatency-aware decoder selection, 16 KB pages + branch protection, bounded asset-loader executors, sensors unregistered in PiP/stop. The `game`/`nonRoot_game` flavor manifests do carry `isGame`/`appCategory="game"`, so Game Mode integration is intact (an auditor initially flagged this; rechecked and dropped).

---

## 4. Picture-quality findings (ranked)

The pipeline is fundamentally upstream's (SurfaceView → MediaCodec with FEATURE_LowLatency, correct HDR10 static-metadata plumbing via codec restart, Rec.709 + limited/full range signalled through `encoderCscMode`, Qt-derived default bitrate with >60 fps damping, FEC-aware bitrate, PCM16 low-latency AudioTrack with correct 5.1/7.1 masks, ALLM on TVs). Nothing Artemis added improves raw picture quality over upstream; several things make frame delivery less regular, and a few options are dead or inverted.

### Q1 — High — Frame delivery: the dead pacing setting and the dual render path (same root cause as P1) — VERIFIED
- With the default settings the "latest-only" drain **and** the Choreographer queue both run. During any output burst (jitter, 120 fps, decoder catch-up) frame N is queued for the next vsync, the loop comes round, the fast path presents N+1 immediately, then the Choreographer presents the *older* N → visible backwards step/judder. The fast path never updates the 80 % vsync gate or the rendered-frame counter, and `INFO_OUTPUT_FORMAT_CHANGED` from its non-blocking dequeue is discarded.
- The "LFR (Experimental)" checkbox is inverted relative to its description ("Minimizes delay by dropping queued frames"): **off** (default) enables the drain-to-newest fast path, **on** disables it (`MediaCodecDecoderRenderer.java:1213` is `if (!preferLowerDelays)`).
- Fix: one presentation path per mode, as upstream (`FRAME_PACING_MIN_LATENCY` is the same "newest frame, present now" algorithm, properly accounted).

### Q2 — High — AV1 is never used in Auto mode; "Allow AV1 by default" (404d70db) is a no-op — VERIFIED
- `MediaCodecDecoderRenderer.java:330-333`: `findAv1Decoder()` returns null unless the user forces AV1; the commit only flipped `isDecoderWhitelistedForAv1()` to `true`, which is consulted after that gate. So every AV1-capable phone (Snapdragon 8 Gen 2+, Dimensity 9000+, Tensor G3+, Exynos 2200+) streams HEVC in Auto. Upstream has the same gate ("For now, don't use AV1 unless explicitly requested"); moonlight-qt auto-selects AV1 when hardware decode exists.
- Opportunity: AV1 at the same bitrate is noticeably cleaner in motion (roughly 20–30 % more efficient than HEVC on NVENC/AV1-capable hosts). Fix: in Auto, use AV1 when a hardware AV1 decoder with `FEATURE_LowLatency` (or `MEDIA_PERFORMANCE_CLASS ≥ 33`) exists; keep Force-HEVC as the escape hatch. Validate decode latency per SoC — some first-generation AV1 decoders are slower than their HEVC siblings (SUSPECTED, device-dependent).

### Q3 — High — `applySurfaceFrameRate()` (see P2) also sends nonsense for fractional rates — VERIFIED
- With a fractional custom refresh the decoder asks the display for e.g. 59,940 Hz. Delete the function.

### Q4 — Medium — External-display mode forces HDR without a capability check, and gates the HDR checkbox on the wrong display — VERIFIED
- `Game.java:603-606`: `if (onExternelDisplay) { willStreamHdr = true; }` with the comment "Enforce HDR on unsupported hardware can still enable 10bit streaming for better quality". The premise is wrong: there is no 10-bit SDR in the protocol — a 10-bit format sets `dynamicRangeMode=1`/`hdrMode=1`, so the host switches to HDR and encodes PQ/BT.2020, and an SDR monitor gets vendor tone-mapping. Conversely `StreamSettings` hides the HDR checkbox based on the *phone's* default display, so an SDR-panel phone driving an HDR monitor cannot enable HDR.
- Fix: run the `HdrCapabilities` check on `currentDisplay`; evaluate `ServerHelper.getActiveDisplay()` in Settings. (True 10-bit SDR is a Vibepollo/Moonlight-X "Force HDR" style host+client feature — see §5.)

### Q5 — Medium — External-display mode sends fractional panel refresh as millihertz — VERIFIED
- `Game.java:410` sets `prefConfig.fps = currentMode.getRefreshRate()` (TVs report 60.000004 / 59.940063); `StreamConfiguration.getRefreshRate()` returns `(int)(refreshRate * 1000)` for non-integers → `maxFPS=60000`, `&mode=WxHx60000`, `KEY_FRAME_RATE=60000`, `Surface.setFrameRate(60000)`, and a 13 µs vsync gate. Apollo decodes millihertz (`fps < 1000 → ×1000`); Sunshine/GFE have no such convention (outcome SUSPECTED: refused launch or a 60000 fps encoder config). The custom-refresh feature is documented "Apollo only", but external-display mode triggers it silently.
- Fix: round near-integer rates; emit ×1000 only for user-typed fractional rates on Apollo hosts; keep decoder-side values in Hz.

### Q6 — Medium — Reference-frame invalidation force-enabled on all MediaTek and `c2.qcom` decoders — VERIFIED code, SUSPECTED impact
- `MediaCodecHelper.java:405-409` adds `c2.mtk`/`omx.mtk` to the AVC+HEVC RFI lists and `c2.qcom` to HEVC. Upstream enables MTK RFI only on Fire OS / PowerVR GX6 because HEVC RFI hangs newer MTK GPUs. With RFI advertised the client sends `maxNumReferenceFrames=0` (unlimited → up to 16 refs, decoder errata 1/13/14) and skips `num_ref_frames=1`. The fork only disables RFI on odd crash counts. Gate by capability or by `Build.SOC_MODEL`.

### Q7 — Medium — Warp modes request 2×/4× the stream fps at an unchanged bitrate — VERIFIED
- `Game.java:775-777`. Apollo restores the bitrate only when its own `limit_framerate` is on; Sunshine/GFE always encode 120/240 fps at the 60 fps bitrate (half/quarter the bits per frame → blocking in motion), and the client discards most frames. Scale the bitrate client-side or offer Warp only on Apollo.

### Q8 — Medium (3D mode only) — SBS-3D path is 8-bit SDR via a `SurfaceTexture`/GLSurfaceView, samples with `mediump`, and stalls the GL thread
- `ShaderUtils.java:15,196` `precision mediump float` for texture coordinates on a 1920–3840 px source (fp16 UV → up to ~1 px error on Mali/Adreno, SUSPECTED); synchronous `glReadPixels` per frame; movie mode sleeps 1 ms in a loop until inference finishes; unbounded hang if the model failed to load. Use `highp`, the existing async PBO path, a bounded wait; document SDR-only.

### Q9 — Low
- Perf overlay mislabels: "Packet loss" is frame loss %, "FPS" is host send rate including lost frames, "Rendering frame rate" misses fast-path presents.
- Portrait auto-invert passes the inverted resolution to the host *and* an invert flag that `setup()` swaps back, so `KEY_MAX_WIDTH/HEIGHT` are 1920×1080 for a 1080×1920 bitstream (SUSPECTED decoder impact).
- Custom resolution/bitrate inputs barely validated (odd dimensions → 1 px non-1:1 scaling; bitrate up to 99,999 Mbps; no decoder max check before `configure()` fails).
- HDR10 requires exactly the `*Main10HDR10` profile constants; plain `Main10` decoders are refused (same as upstream).
- 4:4:4 is unreachable: the fork's common-c defines all five 4:4:4 formats, but `MoonBridge.java` lacks the constants and defines `VIDEO_FORMAT_MASK_10BIT = 0x2200` where common-c has `0xAA00` — a latent misdetection if a 10-bit 4:4:4 format were ever negotiated. Android hardware decoders do not advertise 4:4:4 profiles anyway, so Sunshine's 2026 4:4:4 work is not portable to this client. Fix the mask for hygiene.
- "Tight Vsync (Experimental)" is dead (never read by `PreferenceConfiguration`; read by reflection; stored and never used).
- Audio is byte-identical to upstream; optional `PCM_FLOAT` + `opus_multistream_decode_float` would be marginal.
- Already good: cutout-aware native-resolution entries, Fit/Fill/Stretch math (verified numerically for 20:9, 16:10, 16:9), compositor-side pan/zoom (no re-encode), Apollo `virtualDisplay`/`scaleFactor` supersampling plumbing, `clientRefreshRateX100`, full encryption when AES is accelerated.

### Capability matrix (what the client can negotiate today)
| Codec / profile | Advertised | Android HW decode | UI |
|---|---|---|---|
| H.264 High 4:2:0 8-bit | always | yes | Force H.264 |
| HEVC Main 8-bit | when a whitelisted / FEATURE_LowLatency / perf-class-12 HW decoder exists | yes | Auto / Force HEVC |
| HEVC Main10 HDR10 | only with HDR enabled and `HEVCProfileMain10HDR10` | HDR SoCs | "Enable HDR" (hidden unless the *default* display reports HDR10) |
| HEVC Main10 SDR ("10-bit SDR") | not expressible in the protocol | — | — |
| AV1 Main 8 / Main10 HDR10 | **only with Force AV1** | SD 8 Gen 2+, Dimensity 9000+, Tensor G3+, Exynos 2200+ | Force AV1 |
| Any 4:4:4 | no (no MoonBridge constants) | no public profile constants / no HW | no |
| Colour | Rec.709 (601 pre-Oreo w/o HEVC), limited or full range | `KEY_COLOR_*` for SDR; HDR via VUI/SEI + `KEY_HDR_STATIC_INFO` | "Full range" |

---

## 5. Ecosystem: what changed elsewhere and what it means for this client

### 5.1 Sunshine (LizardByte)
Latest stable **v2026.914.233613** (2026-09-14); nightly train v2026.1003.x. Client-relevant since mid-2025:
- **Security**: CVE-2026-32253 (Critical 9.8, fixed v2026.516): `openssl_verify_cb` accepted expired/not-yet-valid/unknown-issuer client certs → unauthenticated session launch and input injection. Five more advisories fixed in v2026.906 (pairing-PIN session mix-up GHSA-36ff, revoked-client bypass GHSA-6jvv, malformed input packet OOB write GHSA-26q2, Unicode packet over-read GHSA-6w33, 2-byte ENet DoS GHSA-c428), plus GHSA-fp6g (Linux GUI modules) in v2026.914. All host-side; nothing to change in the client, but see Apollo below.
- **`MODIFIER_EXTENDED` (0x10)** — host side merged 2026-09-29 (#5821), client side is common-c `f900dd4` (2026-09-26): distinguishes Numpad Enter / right Ctrl / Insert-Delete cluster. **Needs the client**: the common-c bump plus setting the flag in `KeyboardTranslator` from the scancode path the fork already has.
- **Automatic SDR fallback when HDR is unavailable** (v2026.906): a client that requests HDR may now receive an SDR-negotiated stream; the renderer must key colour handling off the negotiated `videoFormat`, not the user's HDR toggle (the fork's `MediaCodecDecoderRenderer` does key off the negotiated format — verify after porting the common-c bump).
- **4:4:4 chroma + HDR on NVIDIA Linux** (CUDA): requested via `chromaSamplingType=1`, which common-c emits only when a `*_444` format is advertised — not portable to Android (no hardware 4:4:4 decode).
- Fractional NTSC rates via `clientRefreshRateX100` (PR #4019; the fork already sends it), `minimum_fps_target`, `max_bitrate` and `packetsize` host caps (the host may silently cap the client's request), HEVC level 5.1/5.2 for 4K120, FFmpeg 9 with accurate AV1/HEVC capability detection, libvirtualhid input backend (Windows needs the separate Virtual HID driver), split-frame NVENC.

### 5.2 Apollo (ClassicOldSong)
Latest release **v0.4.8 (2025-09-26)**; master's last commit 2026-05-21. Issue #1512 "Is Apollo abandonware?" has no maintainer reply.
- **No Apollo release contains the CVE-2026-32253 fix.** PR #1496 backported it to master on 2026-05-21, but the maintainer declined a hotfix ("DO NOT directly expose Apollo on the internet … use a VPN"). By inheritance the 2026-09 Sunshine input/pairing/ENet bugs almost certainly apply too (inference; not stated by either project). If you run Apollo: LAN/VPN only, or build from master.
- Nothing in Apollo's 2025-09 → 2026-05 master requires a client change; the Artemis-specific contract (virtual display, clipboard sync, server commands, permissions, Remote Input, UUID launch, `.art` export, fractional refresh, Pause/Resume) is all present in the fork.

### 5.3 Vibepollo (Nonary) — and Vibeshine
A fork of Apollo (so Sunshine → Apollo → Vibepollo) by Nonary; README says ~99 % of the code is AI-generated. Latest **v2.0.0 (2026-09-30)**; sibling Vibeshine carries the same features on a Sunshine base.
- Features: display-setting automation with stuck-virtual-display safeguards, Playnite/Steam/Lutris sync, RTSS/NVIDIA frame limiting matched to the client's fps, Lossless Scaling and NVIDIA Smooth Motion frame generation, scoped API tokens, WebRTC browser streaming, Linux beta + SteamOS Gamescope bundle, own virtual gamepad driver, Remote Monitor (Moonlight clients as extra displays), AMD HEVC intra-refresh, and in 2.0: **PyroWave** (GPU wavelet intra-only codec, SDR/HDR, 8/10-bit, 4:2:0/4:4:4, "hundreds of Mbps, wired LAN"), **VRR capture with presentation-driven timing**, **per-app 10-bit SDR**.
- Client compatibility: ordinary H.264/HEVC/AV1 streaming works with Artemis unchanged. PyroWave and VRR pacing need a special client: Nonary's moonlight-qt fork on PC; on Android `joemossjr16/artemis-android-pyrowave` (Artemis + opt-in Vulkan PyroWave renderer) and Moonlight X. No Android VRR pacer exists. PyroWave is also arriving in Steam Remote Play (Sept 2026 beta), so a Vulkan PyroWave decoder is becoming an ecosystem feature rather than a one-off.
- Verify that the Vibepollo build you run includes the CVE-2026-32253 fix; Nonary merges upstream often, but I could not confirm the exact build from here.

### 5.4 Artemis Android (your upstream)
Stable v20.2.6 (2025-08-14), pre-release v20.3.0-experimental.9 (2025-09-16, "AV1 enabled for more devices", 3D SBS, derflacco's low-latency work — the render-loop changes audited above), nothing since. Open PRs worth cherry-picking, in order of value:
- **#603** Skip `KEY_LOW_LATENCY` and HEVC RFI on Amlogic C2 HEVC decoders (S905X5/S905Y4 TV boxes: slideshow → smooth). A few lines in `MediaCodecHelper`.
- **#601** Fix 4K streams disconnecting at startup (display switched to a 4096×2160 mode mid-decoder-init). A few lines in `Game.prepareDisplayForRendering()`.
- **#590** Capability-gated low latency with bounded fallback ladder and 89 unit tests (QTI/MediaTek vendor profiles; S25+ decode p50 ~1 ms vs 10.9 ms claimed).
- #567 native AAudio renderer for Android TV devices where AudioTrack's fast path is denied (known 5.1/7.1 issues — feature-flag it); #559 force-HDR/10-bit SDR experiment; #592 AC-3/E-AC-3 passthrough (vendors FFmpeg — large); #443 derflacco's latency policy (author later suggested keeping only the two MTK-RFI commits — which this audit recommends *gating*, see Q6); #429 GPU-composition toggle for TV SoCs with hardware-overlay stutter; #437 auto res/fps/bitrate + dual-screen.

### 5.5 moonlight-stream (upstream Moonlight)
- moonlight-android **v12.2 (2026-09-12)**: everything functional landed 2026-08-31 → 09-03; see §6 for the per-commit porting table.
- moonlight-common-c: 44 commits the fork lacks (RTSP hardening, RFI/IDR loss fixes 2026-09-09, SIMD FEC, enqueue-side gamepad batching, lock-free RTT, LTR-ACK, dual touchpad, µs timestamps + `LiGetRTPVideoStats()`/`LiGetRTPAudioStats()` — ideal for the Artemis overlay, `MODIFIER_EXTENDED`, thread-context/`pthread_attr` leak fixes, local-address probe via UDP `connect()`).
- moonlight-qt **v6.2.0 (2026-10-04)**: fixes CVE-2026-33546/33547/41210 ("malicious host can crash Moonlight when connecting"; 41210 is the H.264 SPS-fixup heap overflow in h264bitstream — Android patches SPS with jcodec in Java, so no memory corruption, but a crafted SPS still crashes the client by exception; wrap the parse), full-range colour by default, AV1 HW on macOS M3+, YUV 4:4:4 on DXVA2, VRR support in D3D11/KMSDRM, audio queue bounded by duration (Android already does this), DualSense adaptive triggers, second trackpad, default 1080p.

### 5.6 Other Android forks worth watching
- **Moonlight X** (MoreOrLessSoftware, v0.5.5, 2026-10-02; upstream + Artemis features): experimental Vulkan video renderer (zero-copy, GPU colour conversion, dithering, HDR10), "sync to host frame timing" pacing against their Sunshine fork's present-time capture, refresh-rate matching (measures the real panel rate and requests a matching fps), jitter-buffer presets, PyroWave decode, in-stream codec override, Force HDR (10-bit SDR), Pixel 10/Android 17 fixes. The most complete reference design for the pacing work this audit recommends.
- **Artemide** (derflacco; Artemis-based): async decoding, direct-present "FastGL", FSR upscaler presets, Choreographer VSync modes, "NanoPacer" for 120 Hz, CPU-boost; author's own "chaotic playground" warning.
- **Amlogic HEVC fixes** (Nun-z; Artemis-based): decoder-stall watchdog, non-blocking output queue, latest-frame rendering fix, GPU-composition forcing for S905X5 boxes.
- **Moonlight V+** (qiin2333; upstream-based, large Chinese community): 144/165 Hz unlock, HDR/HLG, gyro aiming, microphone redirection, floating control ball, companion "Foundation Sunshine".
- PC references for pacing/VRR: Nonary/moonlight-qt v6.1.0-vrr18, FoggyBytes/StreamLight v6.4.1.

---

## 6. Porting plan

Mechanics first:
- **Line endings**: 122/171 Java files and 107 other text files are CRLF; upstream is LF. Plain `git cherry-pick` conflicts on every hunk of otherwise clean commits. Every pick below was tested in a scratch worktree with `git cherry-pick -Xignore-space-at-eol`: **15 of 29 upstream commits apply cleanly, 9 conflict only on a version/adjacency line, 4 have trivial Java conflicts, and only `3df0103a` (<API 21 cleanup) conflicts substantively** with Artemis's rewritten renderer. An ignore-EOL pick leaves the added lines as LF inside CRLF files, so run `unix2dos` on touched files before committing. If you intend to keep merging Artemis, do **not** normalize line endings unilaterally (every future Artemis merge becomes whole-file noise in reverse; propose `* text=auto` upstream instead). If the fork is diverging for good, do a one-time `.gitattributes` `* text=auto` + `git add --renormalize .` as a standalone first commit, then plain cherry-picks.
- Fork-local fixes (not cherry-picks) are listed with the finding ids above.

**Phase 0 — Artemis-only security fixes (no upstream dependency):** S2 accessibility focus gate; S3 confirm `.art`/`art://` launches, drop URI PIN/passphrase auto-pair, default port; S4 catch `RuntimeException` in `tryPollIp` + validate `status_code`/ports; S5 don't merge HTTP `serverinfo` after a cert mismatch; cap clipboard fetch; remove the dead `0x3001/0x3002` async-callback entries.

**Phase 1 — pure Java, compileSdk 36 is enough (all apply with `-Xignore-space-at-eol`):** `abde6021` LED ANR (+ `8d720748`) → `3c6a0d12` rumble → `68adf9ec` SPS → `b9c5eddd` '+' key → `8974dcda` F13–F24 (+ `ad861490`) → `0711e236` Quest null-guard and `280454fd` wireless Xbox PIDs by hand (skip `583f662a`, already present) → NvHTTP `.fastFallback(false)`. Then the fork-local render fixes: P1/Q1 (restore the pacing preference and upstream's loop), P2/Q3 (delete `applySurfaceFrameRate`), P4 (lazy OpenCV), P8 (drop the PTS map), Q2 (AV1 in Auto on capable hardware), Q4/Q5 (external-display HDR + refresh), Q6 (gate MTK RFI), and Artemis PRs #603/#601.

**Phase 2 — build chain:** `9221a0ca` Java 17 → `4eb24a8d` desugaring (test discovery on an API 21–25 emulator before/after) → `b3a7e32a` BC 1.85.2/jmDNS 3.6.3 + `excludes '/META-INF/*.md'` (keep the fork's `pickFirsts`) → `98c12beb` OkHttp 5.5 → `1fa0e2a0` NDK r29 → compileSdk 37 (AGP 8.13 with a warning, or `9d8b073c`+`801dba1b` AGP 9.4 with `buildFeatures.resValues = true` and a check of the fork's splits/testOptions/jitpack/root test aggregation) → `6d4c64a5` producer throttling → `ddb674a9` keyboard capture (+ `CAPTURE_KEYBOARD` permission; needs `SDK_INT_FULL`) → `4b2221d3` targetSdk 36 last, with an edge-to-edge pass over the Artemis overlays. Switch release to `proguard-android-optimize.txt`; drop MPAndroidChart.

**Phase 3 — native:** `31b70030` OpenSSL 4.0.2 + libopus 1.6.1 prebuilts (`libopus/` → `opus/`, drop `libssl` from both `Android.mk`s; applies cleanly) → merge `moonlight-stream/moonlight-common-c` master (`f900dd4`, for the September RFI fixes and `MODIFIER_EXTENDED`) into `ClassicOldSong/moonlight-common-c` (clean merge-tree; re-point the submodule; `git submodule update --init --recursive` for the new `nanors` submodule and the enet bump) → port the Android side of `0dc4c4fe`: `Android.mk` sources (`nanors/deps/obl/oblas_common.c`, `oblas_lite.c`, `nanors/rs.c` + include dirs, replacing `reedsolomon/rs.c`), `MoonBridge` constants (`LI_CTYPE_STEAM = 0x04`, `LI_CCAP_DUAL_TOUCHPAD = 0x100`), `callbacks.c`/`VideoDecoderRenderer`/`MediaCodecDecoderRenderer` ms→µs — **and the `baseTimestampUs` normalization**. This is the one real hazard: today `enqueueTimeMs` is `PltGetMillis()` (CLOCK_MONOTONIC, same epoch as `System.nanoTime()`) and the fork's frame-age drop logic at `MediaCodecDecoderRenderer.java:1300` and `:1356` relies on that (`nowNs − presentationTimeUs×1000`). The new `PltGetMicroseconds()` is relative to library init, so without upstream's `baseTimestampUs = uptimeMillis()×1000 − enqueueTimeUs` every frame looks ancient and gets dropped (VERIFIED). Then `c2e224eb` SDL controller sync (needs `LI_CTYPE_STEAM`). Smoke-test: FEC under induced loss, RFI with Sunshine, multi-controller input, overlay stats (consider `LiGetRTPVideoStats()` for the overlay).

**Skip:** `3df0103a` (hand-prune the 18 dead `< LOLLIPOP` sites instead), `b55b4b6d`/`5c0c2390` (Weblate-only), `3f114ac7`/`578f38f6`/`b48494cb` (CI/version).

**Follow-ups once the common-c bump is in:** set `MODIFIER_EXTENDED` from the fork's scancode path; `LiSendControllerTouchEvent2` JNI for dual-touchpad pads; `LI_CTYPE_STEAM` in `ControllerHandler`.

---

## 7. Questions for you (answers change the recommendations)

1. **Which host(s) and GPU do you stream from** — Apollo, Sunshine, Vibepollo, or Moonlight X's Sunshine fork; NVIDIA/AMD/Intel? This decides whether AV1-in-Auto, the millihertz refresh path (Apollo-only), warp modes, 10-bit SDR, and PyroWave are relevant to you, and whether the CVE-2026-32253 warning applies.
2. **Which Android devices** (SoC, panel refresh, HDR capability) and **network** (wired/Wi-Fi, LAN/remote)? The render-loop fix matters everywhere; the MTK/Amlogic items only on those SoCs; SIMD FEC only on lossy links.
3. **Do you intend to keep merging Artemis** (ClassicOldSong) or diverge? This decides the line-ending strategy and whether to wait for Artemis to sync upstream (it has not in two years).
4. **Which Artemis features do you actually use** — the keyboard accessibility service, clipboard sync, `art://`/`.art` launches, external-display mode, SBS-3D? Several findings only bite with those enabled.
5. **Do you want me to implement any of this?** This session can read the fork but cannot push to it; attaching it with push access (or you creating a branch and granting it) is needed before I can open PRs. I would start with Phase 0 + the Phase 1 render-loop fixes as one PR, and the common-c bump as a separate PR.
6. **How do you build and distribute** (Play, Obtainium, sideload)? If you publish, targetSdk 36 is a 2026 Play requirement, and the fork has no CI — a GitHub Actions workflow building the four ABI splits would make the porting work testable.

---

## 8. Appendix

Detailed reports (same scratchpad directory as this file):
- `audit_security.md` — all security findings with file:line references, scenarios, and fixes.
- `audit_performance.md` — all performance findings, build-flag table.
- `audit_picture_quality.md` — all picture-quality findings, capability matrix.
- `upstream_port_analysis.md` — the 29-commit porting table with per-commit cherry-pick results, toolchain diff, common-c comparison.
- `research_ecosystem.md` — ecosystem research with a URL for every claim (Sunshine/Apollo/Vibepollo/Artemis/Moonlight/moonlight-qt/forks/CVEs).

Verification notes: dates and versions for Sunshine, Apollo, Vibepollo, Artemis, moonlight-qt and the CVE/GHSA entries come from the GitHub release/advisory pages fetched during the audit (nvd.nist.gov and docs.lizardbyte.dev were unreachable from this environment). CVE-2026-13506 (BouncyCastle), CVE-2026-32253 (Sunshine), Apollo PR #1496, moonlight-qt v6.2.0 and the Vibepollo 2.0.0 release were re-fetched by the coordinating reviewer. One performance finding from an auditor ("manifest not declared as a game") was checked and dropped: the `game`/`nonRoot_game` flavor manifests carry `isGame`/`appCategory="game"`.
