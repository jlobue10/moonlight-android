# Moonlight / Sunshine ecosystem — state as of 2026-10-04

Scope: what has changed elsewhere that may be worth porting into an Artemis-based Android client (ClassicOldSong/moonlight-android, branch `moonlight-noir`, identical to upstream Artemis at c5cf27f, 2026-09-09). Priorities: security, streaming performance (latency, frame pacing), picture quality.

Research notes: all GitHub pages were read directly. `docs.lizardbyte.dev`, `nvd.nist.gov`, `cve.org`, `osv.dev`, `openssl.org`, `opus-codec.org` and `snyk.io` are blocked by this environment's egress proxy, so configuration docs, advisories and release notes were read from their GitHub mirrors (`docs/configuration.md`, `NEWS.md`, GHSA pages, xiph/opus releases) instead. Where a fact could not be confirmed it is marked as such.

## 0. Baseline — what the fork actually ships today (verified from the repo)

- App: `versionName 20.2.6` / `versionCode 57`, `compileSdk 36`, **`targetSdk 34`**, `minSdk 21`, **NDK r27 (27.0.12077973)**, Java 11, ABI splits x86/x86_64/armeabi-v7a/arm64-v8a — https://raw.githubusercontent.com/ClassicOldSong/moonlight-android/moonlight-noir/app/build.gradle
- Java deps: **okhttp 4.12.0**, **bcprov/bcpkix-jdk18on 1.81**, **jmdns 3.6.2**, jcodec 0.2.5, gson 2.13.1, LiteRT 1.4.0 (+gpu), OpenCV 4.12.0, MPAndroidChart, SearchPreference.
- Native deps (`app/src/main/jni/moonlight-core`): submodule **ClassicOldSong/moonlight-common-c @ c999436** (2025-09-01, "Add send empty payload method"; its last upstream merge is b4d2af7 on 2025-09-01, which brought upstream up to 5f22801, 2025-07-15) — https://github.com/ClassicOldSong/moonlight-android/tree/moonlight-noir/app/src/main/jni/moonlight-core and https://github.com/ClassicOldSong/moonlight-common-c/commits/master
- Prebuilt **OpenSSL 1.1.1q (+cgutman patch)** — the fork never took anything newer than upstream commit 9d5ff72 (2022-08-02, "Update OpenSSL to fix _armv7_tick() crash", body: "OpenSSL 1.1.1q + cgutman/openssl@fe1a23c") — https://github.com/moonlight-stream/moonlight-android/commit/9d5ff72
- Prebuilt **libopus 1.5.2** (upstream f10085f, 2024-07-27) — https://github.com/moonlight-stream/moonlight-android/commits/master/app/src/main/jni/moonlight-core/libopus
- Verified by grepping both `SdpGenerator.c` files: the SDP attributes the fork sends are **identical** to upstream's (including `x-nv-video[0].clientRefreshRateX100`, `x-ss-video[0].chromaSamplingType`, `x-ss-general.encryptionEnabled`, `x-ml-video.configuredBitrateKbps`). Apollo's client extensions live in the HTTP layer (`launch` params `virtualDisplay`, `scaleFactor`, `hdrMode`, `localAudioPlayMode`, `surroundAudioInfo`, `gcmap`, `gcpersist`, `sops`; `actions/clipboard`; per-client `uniqueid` passed in by the caller instead of upstream's hard-coded `0123456789ABCDEF`) and in the control stream (`LiSendExecServerCmd(cmdId)` / `LiSendEmptyPayload()`, packet index `IDX_EXEC_SERVER_CMD = 12`) — https://raw.githubusercontent.com/ClassicOldSong/moonlight-android/moonlight-noir/app/src/main/java/com/limelight/nvstream/http/NvHTTP.java, https://raw.githubusercontent.com/ClassicOldSong/moonlight-common-c/master/src/Limelight.h

---

## 1. LizardByte/Sunshine

Releases page: https://github.com/LizardByte/Sunshine/releases — tags: https://github.com/LizardByte/Sunshine/tags
Advisories: https://github.com/LizardByte/Sunshine/security/advisories

**Latest stable: v2026.914.233613 (2026-09-14).** Latest pre-release ("nightly" train): v2026.1003.221627 (2026-10-03; v2026.1003.171600 same day). Releases since mid-2025: v2025.628.4510 (2025-06-28), v2025.923.33222 (2025-09-23), v2025.924.154138 (2025-09-24), v2026.516.143833 (2026-05-16), v2026.906.222525 (2026-09-06), v2026.914.233613 (2026-09-14), v2026.1003.x (2026-10-03). There is no tagged 2026.329 release — the `>= 2026.329.165800` in GHSA-6jvv-jqr7-m6m3 is a nightly build id.

### Client-relevant changes per release

**v2025.628.4510** — https://github.com/LizardByte/Sunshine/releases/tag/v2025.628.4510
- NVENC **async encode** (#3629); "Base min frame time strictly on client framerate" (#3844) — the host's frame-duplication floor is now derived from the fps the client requests; nothing to do client-side, but it means the client's `maxFPS` value matters for idle bandwidth.
- **Max Bitrate** host option (#3628): host may silently cap below the client-requested bitrate (`max_bitrate`, default 0 = honor client).
- "Improved timestamp accuracy for RTP video"; Linux native DualSense + adaptive triggers (#3600/#3738) — the client side (`LiSendControllerAdaptiveTrigger…`, common-c e95feaf, 2025-03-25) is already in the fork's common-c.
- Security: GHSA-39hj-fxvw-758m (Critical, app-wide CSRF → command injection in Web UI) and GHSA-x97g-h2vp-g2c5 (Moderate, clickjacking). Web-UI only.

**v2025.923.33222 / v2025.924.154138** — https://github.com/LizardByte/Sunshine/releases/tag/v2025.923.33222
- "attempt to use level 5.1/5.2 for hevc" (#3888) — relevant to 4K120 HEVC: Android's `MediaCodecDecoderRenderer` level handling must tolerate level 5.2 SPS/VPS.
- "restore the ability to set a minimum fps target" (#4114) → `minimum_fps_target` (default 0) — https://github.com/LizardByte/Sunshine/blob/master/docs/configuration.md
- "don't wake up every 500ms to poll while not streaming" (#4051); FFmpeg 8.0 (#4143); DS5 fixed MAC per controller index (#4158).
- Security: GHSA-6p7j-5v8v-w45h (Moderate, unquoted Windows service path, local).

**v2026.516.143833** — https://github.com/LizardByte/Sunshine/releases/tag/v2026.516.143833
- Security: **GHSA-ph75-mgxh-mv57 / CVE-2026-32253 (Critical 9.8)** — see §9.
- **Fractional NTSC frame rates via `x-nv-video[0].clientRefreshRateX100`** (PR #4019, merged 2025-10-11; 5994 → exact 60000/1001) — https://github.com/LizardByte/Sunshine/pull/4019. The fork already sends this attribute (it is upstream common-c; Artemis sends it for Apollo since v12.1.250410), so an Artemis client now gets exact 59.94/119.88 on stock Sunshine too. Sunshine also validates the client's refresh rate with a ±1 % tolerance.
- **Split-frame encoding** on GPUs with 2+ NVENC blocks (`nvenc_split_encode`) — host-only latency win.
- HDR: "10-bit RGB formats, Rec. 2020/SMPTE 2084 PQ" capture path; Linux XDG-Portal/PipeWire/KWin capture; **Vulkan encoding** on Linux; Windows Graphics.Capture 60 fps cap fixed; **client enable/disable from the Web UI** (a paired cert can be revoked); F13–F24 keycodes on Linux (pairs with upstream Android 8974dcd); CSRF protection (`csrf_allowed_origins`); signed Windows/macOS binaries; Windows ARM64.

**v2026.906.222525** — https://github.com/LizardByte/Sunshine/releases/tag/v2026.906.222525
- Security: five advisories (GHSA-6w33-pjh7-p77c, GHSA-6jvv-jqr7-m6m3, GHSA-36ff-frg7-492f, GHSA-26q2-58j6-qmvv, GHSA-c428-87f8-rrv5) — see §9.
- Input backend migrated to **libvirtualhid** (#5368): Windows needs the separately installed Virtual HID Driver for full gamepad/keyboard/mouse; "automatic gamepad emulation may now expose an Xbox Series controller"; gamepad profiles Xbox 360/One/Series, DS4, DualSense, Switch Pro, generic; expanded touchscreen/pen/trackpad support. No new client packets — the client's existing `LiSendTouchEvent`/`LiSendPenEvent`/controller-type reporting is what gets mapped.
- **Hardware YUV 4:4:4 + HDR on NVIDIA Linux (CUDA/CUDA-GL)** (#4965, #5315). 4:4:4 is requested by the client with `x-ss-video[0].chromaSamplingType=1`, which common-c only emits when the decoder advertises a `VIDEO_FORMAT_*_444` format. Android MediaCodec has no 4:4:4 hardware decode, so this is **not portable** to the Android client.
- **"Automatic SDR fallback when HDR is unavailable"** — a client that requests HDR can now receive an SDR-negotiated stream instead of a failure; the Android renderer must key its color handling off the negotiated `videoFormat`, not off the user's HDR toggle.
- **`packetsize`** host option (#5153) — caps the client's `x-nv-video[0].packetSize`; useful for low-MTU links; nothing to change client-side.
- VA-API rate-control/quality options (#5388); Wayland/PipeWire variable-rate capture, fractional refresh, DMA-BUF modifiers (#5423); `SUNSHINE_CLIENT_NAME` env var; Cloudsmith repos; NSIS installer dropped (MSI only).

**v2026.914.233613** — https://github.com/LizardByte/Sunshine/releases/tag/v2026.914.233613
- Security: GHSA-fp6g-27w5-489j (High, Linux: untrusted GUI modules run with Sunshine's capabilities).
- FFmpeg 9 with **accurate AV1/HEVC capability detection** (#5554) — hosts may now advertise AV1/HEVC Main10 differently; NVENC on Linux without CUDA (#5698); `gamepad_driver` selector (#5653); `bind_address` honored (#5700).

**v2026.1003.221627 (pre-release)** — https://github.com/LizardByte/Sunshine/releases/tag/v2026.1003.221627
- **"fix(input): honor extended keyboard modifier" (#5821, merged 2026-09-29)** — https://github.com/LizardByte/Sunshine/pull/5821. Host side of the new `MODIFIER_EXTENDED` (0x10) keyboard flag (common-c f900dd4, 2026-09-26) that distinguishes 0xE0-prefixed keys (Numpad Enter vs Enter, etc.). **Requires a client change** (common-c + the Android `KeyboardTranslator` must set the flag). Not in the fork.
- "fix(input): serialize queued packets per stream" (#5818) — fixes a Linux multi-gamepad allocation race; host only.
- `vk_quality` Vulkan encoder option (#5783); macOS gamepads via libvirtualhid (#5807); permissions exposure (#5820); dynamic app reload (#5506).

### Host options worth knowing as a client author (from `docs/configuration.md`)
`minimum_fps_target` (0), `packetsize` (0), `max_bitrate` (0 = honor client), `fec_percentage` (20), `qp` (28), `hevc_mode` 0–3, `av1_mode` 0–3, `nvenc_preset` (1 = P1), `nvenc_twopass` (quarter_res), `nvenc_spatial_aq`, `nvenc_vbv_increase` (0–400), `nvenc_latency_over_power` (enabled), `nvenc_realtime_hags`, `nvenc_split_encode` (driver_decides), `amd_rc` (vbr_latency), `vaapi_rc` (auto), `vk_rc_mode` (2 = CBR), `vk_tune` (2 = low latency).

---

## 2. ClassicOldSong/Apollo

Releases: https://github.com/ClassicOldSong/Apollo/releases (atom: https://github.com/ClassicOldSong/Apollo/releases.atom)

**Latest release: v0.4.8 (2025-09-26)** — "Apply theme to login page" (closes #1094). No release since; master's last commit is 2026-05-21 (CVE backport). Issue #1512 "Is Apollo abandonware? No update for almost a year" (2026-05-29) has no maintainer reply — https://github.com/ClassicOldSong/Apollo/issues/1512

### Changelog since mid-2025 (release timestamps from the atom feed)
- **v0.4.0 (2025-07-13)** — major upstream Sunshine merge; fixes the 60 fps lock of WGC capture via `MinUpdateInterval` (#785); option to disable controller rumble (#886); Spanish (#853); CONTRIBUTING.md.
- **v0.4.1 (2025-07-15)** — always create a temporary virtual display when initial encoder probing fails.
- **v0.4.2 / v0.4.3 (2025-07-17)** — revert libdisplaydevice / revert build-deps (compat regressions).
- **v0.4.5 (2025-08-03)** — "Stabilize encode timing"; restores Apollo's "sudo-frame-pacing".
- **v0.4.6 (2025-08-06)** — Spanish fix; double-refresh-rate mode with custom refresh rates; **automatic minimal fps target** (prevents framerate overshoot — Apollo's analogue of Sunshine's `minimum_fps_target`); **SudoVDA driver re-signed for 5 years — users had to update before 2025-08-19**; release text warns that "some other forks of Sunshine merge on-going PRs from upstream (and without credits to the PR author)" with unencrypted communication / shared audio streams — https://github.com/ClassicOldSong/Apollo/releases/tag/v0.4.6
- **v0.4.7-alpha.1 (2025-08-12)** — "GPU priority hack" for HAGS freezes.
- **v0.4.8 (2025-09-26)** — login page theme.
- **Unreleased master (Oct 2025 → May 2026)**: frame-timestamp assert fix (#1170), state-file corruption fix when saving credentials (2025-12-16), Boost 1.89 hash, wlgrab frame timestamps for Wayland (#1430), VAAPI explicit `rc_mode` + rate-control buffer (#1435), AMD AMF profile/coder options (2026-03-30), PSP compatibility (#1438), **CVE-2026-32253 backport (#1496, 2026-05-21)** — https://github.com/ClassicOldSong/Apollo/commits/master

Earlier-2025 context that defines the client contract (all require Artemis): v0.3.0 (2025-02-14) Remote Input mode, Double Refresh Rate, captured-frame-rate limiter, client connect/disconnect commands, per-app per-client identity, SudoVDA SDR10/HDR12/WCG; v0.3.3 (2025-04-01) fractional refresh (Artemis ≥ v12.1.250410); v0.3.4 (2025-04-14) bitrate-limiter fixes, mDNS off option; v0.3.5/0.3.6 (May 2025) UUID launch, app reordering (Artemis ≥ v12.1.250514), isolated virtual display, `APOLLO_*` env vars, DXGI HDR state detection; v0.3.7 (2025-06-05) `.art` export, Pause/Resume commands, `APOLLO_APP_STATUS`, "Terminate on Pause", "Always create Virtual Display" client option, WebUI → client direct launch (requires Artemis), `SUNSHINE_CLIENT_FPS` becomes float.

### Apollo-specific client features and what the client must implement
| Feature | Client-side surface | Status in fork |
|---|---|---|
| Virtual display (SudoVDA) matching client res/fps, HDR | `launch` params `virtualDisplay`, `scaleFactor`, `hdrMode`, fractional `clientRefreshRateX100` | present |
| Clipboard sync | `actions/clipboard` HTTP endpoint | present |
| Server commands | control-stream `LiSendExecServerCmd` (fork-only common-c extension) | present |
| Permissions (first client full; later clients View/List only) | per-client `uniqueid` + cert identity | present |
| Input-only / Remote Input mode | launch flags; v0.3.7 auto-terminates when clients leave | present |
| UUID launch, app order, `.art` / `art://launch` | HTTP + deep links | present (v12.1.2505xx+) |
| Pause/Resume on disconnect/reconnect | host-side; nothing new to send | n/a |
| HDR handling | Apollo README still says "Enabling HDR is generally not recommended with ANY streaming solutions at this moment"; DXGI HDR state detection host-side | n/a |

Nothing in Apollo's 2025-09 → 2026-05 master requires a client change. The one thing to note for your users: **no Apollo release contains the CVE-2026-32253 fix** (maintainer declined a hotfix and recommends VPN-only exposure — https://github.com/ClassicOldSong/Apollo/pull/1496), and because Apollo's last upstream merge is mid-2025, the five Sunshine advisories of 2026-09-07 that affect "all versions since 0.1.0/0.16.0/0.21.0" almost certainly apply to Apollo as well (inference; not stated by either project).

---

## 3. Vibepollo (Nonary/Vibepollo)

Repo: https://github.com/Nonary/Vibepollo (1.2k stars, 5,349 commits, default `master`). Releases: https://github.com/Nonary/Vibepollo/releases

- **What it is**: a fork of ClassicOldSong/Apollo (so Sunshine → Apollo → Vibepollo) maintained by **Nonary**; README states ~99 % of the code is AI-generated (GPT-5.3-Codex) and that it exists because the upstream review process could not keep up. Nonary also maintains **Vibeshine** (https://github.com/Nonary/vibeshine), the same feature set on a Sunshine base (~99.8k changed lines); Vibepollo "brings Vibeshine features to Apollo's ecosystem". Apollo's v0.4.6 note about forks that merge upstream PRs "without credits" is widely read as aimed at these forks (not named by Apollo).
- **Latest release: v2.0.0 (2026-09-30)** — https://github.com/Nonary/Vibepollo/releases/tag/2.0.0. Release train in the window: v1.18.4-stable.3 (2026-08-19), v1.19.0-beta.1/2/3 (2026-08-20/21/22), v2.0.0-beta.1/2 (2026-09-08), v1.18.4-stable.4 (2026-09-10), v2.0.0-beta.3 (2026-09-14), 2.0.0-beta.4 (2026-09-23), v2.0.0 (2026-09-30).
- **Distinctive features**: display-setting automation with "stuck virtual display" safeguards; WGC capture in service mode; native virtual display driver; browser UI; **Playnite** library sync/launch; **RTSS + NVIDIA Control Panel** frame limiting / V-Sync management (cap = client's requested fps); frame-generation capture fixes (DLSS/FSR); **Lossless Scaling** and **NVIDIA Smooth Motion** (RTX 40+) frame generation; scoped API tokens; WebRTC streaming to a browser (`/webrtc`); Linux beta (Arch/CachyOS, Plasma Wayland, kernel 6.16+) and a SteamOS Gamescope bundle (SDR/HDR); v1.19: own virtual gamepad driver (Xbox Series/One, DualSense, DS4, Switch Pro; rumble, adaptive triggers, touchpad, motion, battery), Remote Monitor (up to four Moonlight clients as extra displays) and Remote Input, AMD HEVC intra-refresh (GDR) on explicit client request; v2.0.0: **PyroWave** GPU wavelet codec (intra-only, SDR/HDR, 8/10-bit, 4:2:0/4:4:4, "hundreds of Mbps, wired LAN"), **VRR capture with presentation-driven timing** (Windows uses a fixed 1000 Hz virtual-display mode), per-app **10-bit SDR** preference, Steam/Lutris sync, DS4 emulation + wireless DualSense haptics on Linux.
- **Client compatibility**: ordinary H.264/HEVC/AV1 streaming works with stock Moonlight and Artemis unchanged. Two things need a special client: (1) **PyroWave** — on PC Nonary's own fork **Nonary/moonlight-qt v6.1.0-vrr18 (2026-09-30)** (https://github.com/Nonary/moonlight-qt/releases/tag/v6.1.0-vrr18), on Android **joemossjr16/artemis-android-pyrowave** (Artemis + opt-in Vulkan PyroWave renderer, branches `pyrowave` / `pyrowave-adreno-tile`, https://github.com/joemossjr16/artemis-android-pyrowave) and Moonlight X (§8); (2) **VRR pacing** — client-side adaptive jitter buffer in Nonary's moonlight-qt (presets Low latency 4.17 ms / Balanced 8.33 ms / Smooth 24 ms, decode no longer blocks presentation, Steam Deck present-to-flip 3.9 → 2.0 ms). No Android implementation of the VRR pacer exists. PyroWave is also arriving in Steam Remote Play (Steam beta, Sept 2026 — https://www.gamingonlinux.com/2026/09/steam-beta-adds-experimental-new-pyrowave-video-codec-for-remote-play/), so a Vulkan PyroWave decoder is becoming an ecosystem feature rather than a one-off.

---

## 4. ClassicOldSong/moonlight-android (Artemis Android)

Releases: https://github.com/ClassicOldSong/moonlight-android/releases (atom: …/releases.atom) · PRs: https://github.com/ClassicOldSong/moonlight-android/pulls

- **Latest stable: v20.2.6 (2025-08-14)**; **latest pre-release: v20.3.0-experimental.9 (2025-09-16)** (exp.1/2 2025-08-30, exp.3 09-01, exp.4 09-05, exp.5/6 09-08/09, exp.7 09-14 "Merge branch '3dmode' into exp", exp.8 09-14, exp.9 09-16). exp.9 notes: fix #359; merged derflacco's low-latency work; experimental AI 3D SBS mode (Janyger) — APK grew because of bundled AI models; remember mouse-mode during stream; disable-rumble option; "prevent packet loss on some devices" option; no screen dim with keyboard in External Display mode; preferences reorganized; **AV1 enabled for more devices**; trackpad scrolling fix (wefcdse); allow system status bar (matejdro). Declared "inter-changeable with v20.2.6".
- **Nothing has been released since 2025-09-16.** Commits on `moonlight-noir` after that: zh-rTW translations (Sep–Oct 2025, PR #412), then only PR #573 "right alt command c/v mapping" (commits Jun–Jul 2026, merged 2026-09-09 = c5cf27f) — https://github.com/ClassicOldSong/moonlight-android/commits/moonlight-noir
- **Has development moved?** No evidence. README still names the project "Artemis Android (Moonlight Noir)", default branch `moonlight-noir`, with the "independent fork, will diverge from Moonlight/Sunshine" notice; the `exp` branch no longer exists (only `moonlight-noir` is listed). ClassicOldSong's profile shows no new client repo (recent pushes: Plexsonic 2026-09-17, moonlight-android 2026-09-09, MacMTP 2026-09-02, Apollo 2026-05-21; moonlight-common-c last 2025-09-01) — https://github.com/ClassicOldSong?tab=repositories&sort=pushed. The only "new Artemis" is third-party: wjbeckett/artemis, a moonlight-qt fork implementing Artemis features for PC (clipboard sync, server commands, fractional refresh, resolution scaling, virtual-display toggle), announced in Apollo issue #937 (2025-07-16).
- **Open PRs (26) that are performance / picture-quality relevant** (all unmerged):
  - **#603** "Skip KEY_LOW_LATENCY and HEVC RFI on the Amlogic C2 HEVC decoder" (JustinZeus, 2026-09-23) — `c2.amlogic.hevc.*` advertises `FEATURE_LowLatency` but turns into a slideshow with `KEY_LOW_LATENCY`; also disables HEVC RFI for it. Matches by decoder-name prefix; fixes S905X5/S905Y4 boxes (Xiaomi TV Box S 3rd gen: 59.94 fps "decoded" but slideshow → smooth 57 fps, 5.4 ms decode). Touches `MediaCodecHelper.setDecoderLowLatencyOptions()` try-0 and `decoderSupportsRefFrameInvalidationHevc()`. https://github.com/ClassicOldSong/moonlight-android/pull/603
  - **#601** "Fix 4K streams disconnecting during startup" (Mad-Melon, 2026-09-09) — `Game.prepareDisplayForRendering()` switched a 3840x2160 display to the 4096x2160 mode, recreating the Surface mid-decoder-init ("The surface has been released"); keeps the current mode when it already matches at ≤60 fps. `Game.java` ~L1490. https://github.com/ClassicOldSong/moonlight-android/pull/601
  - **#590** "Improve capability-gated codec low latency and testing" (bynkook, 2026-08-10) — auto-enables Android `KEY_LOW_LATENCY` when the decoder supports it; vendor profiles for QTI and MediaTek gated by capability, bounded config attempts with base-codec fallback, runtime downgrade of unsafe profiles, thread-safe decode-latency sampling, 89 unit tests, separate `lowlatencyDebug` build variant; Galaxy S25+ ~0.9–1.0 ms p50 vs 10.9 ms. https://github.com/ClassicOldSong/moonlight-android/pull/590
  - **#567** "Add native AAudio low-latency renderer" (olibols, 2026-06-05) — AAudio exclusive/low-latency with ring buffer for devices (Google TV Streamer, ATV 14) where AudioTrack's fast path is denied (0.5–1 s audio delay). Known issues with 5.1/7.1 (silent channels, hangs); author proposes a feature flag. https://github.com/ClassicOldSong/moonlight-android/pull/567
  - **#559** "[Experimental] Allow force-enable of HDR to allow SDR 10bit stream" (udbue, draft, 2026-05-09) — borrowed from "nova"; same idea as Moonlight X's "Force HDR". Touches `Game.java`, `StreamSettings.java`. https://github.com/ClassicOldSong/moonlight-android/pull/559
  - **#592** "Add optional AC-3 and E-AC-3 5.1 output" (BuffMcBigHuge, 2026-08-19) — encodes decoded 5.1 PCM to AC-3/E-AC-3 on an audio-priority worker for HDMI sinks that drop multichannel PCM; vendors LGPL FFmpeg n7.1.1 prebuilts for 4 ABIs; one-way failover to PCM. https://github.com/ClassicOldSong/moonlight-android/pull/592
  - **#443** "Latency policy / LFR and various fixes" (derflacco, 2025-10-21) — per-profile dequeue timeouts (Balanced 1000 µs, CapFPS 1500 µs, Max Smoothness 2000 µs, else 500 µs), LFR drains to newest frame, decode latency measured on all decoded frames, `SemWindowManager` gated to Samsung, **RFI enabled for `c2.mtk`/`omx.mtk`**; author later suggested keeping only the two MTK-RFI commits. https://github.com/ClassicOldSong/moonlight-android/pull/443
  - **#429** "mini performance overlay and GPU composition toggle for Android TV" (PacificSilent, 2025-10-07) — forces GPU composition with an invisible 1sp TextView to defeat hardware-overlay stutter on some TV SoCs; confirmed working by users 2025-12-06. https://github.com/ClassicOldSong/moonlight-android/pull/429
  - **#437** "Improvements 3D, AutoConfigurations (Res, Fps, Bitrate), DualScreenDeviceSupport" (Janyger, 2025-10-15, 23 commits to Apr 2026) — auto res/fps/bitrate from the active display (bitrate 0 = auto), AYN Thor dual-screen, refactored AI-3D shaders. https://github.com/ClassicOldSong/moonlight-android/pull/437
  - Input/other: #589 Samsung One UI fully-external display (bynkook), #577 dual-screen/input (kiloiam), #587 rumble rate limiting (KillianG), #537 microphone streaming for Apollo hosts (logabell, 2026-03-19), #531/#529 mouse emulation rewrite + sensitivity (mevouc), #528 Steam Controller (Crazyphil), #571/#570 PowerA / Joy-Con (CoolRobloxDeveloper), #584 Japanese IME keys (ah-kun), #593 JMGO projector popup (Erwwyh), #556 keyboard accessibility prompt, #482 right-stick vector trackpad (HsunLu), #475 "Diana OSCSuite", #409 three-finger tap, #389 quick toggle SpecialKeys/VirtualController, #330 gaming touchscreen (VFXNO).

---

## 5. moonlight-stream/moonlight-android (upstream)

Releases: https://github.com/moonlight-stream/moonlight-android/releases (atom confirms dates)

- **v12.2 — 2026-09-12** (previous: v12.1 2024-02-28, v12.0.2 2023-11-02). Notes: target API Android 16; keyboard capture on Android 16.1+; F13–F24 keycodes; fixed crashes/ANRs with RGB-LED gamepads; '+' key on virtual keyboard; Meta Quest crash; Xbox controllers via built-in USB driver; dependency updates; translations.
- v12.1 (2024-02-28) predates the fork's last sync (Nov 2024), so the fork already has: codec-consistent bitrate logic, full E2E stream encryption with Sunshine ≥ 0.22, rumble-intensity and controller-mouse scroll options, reconnection-reliability work, special-key pass-through.
- Everything upstream did between Nov 2024 and Aug 2026 was Weblate translations; **all functional changes landed 2026-09-01 → 09-03** — https://github.com/moonlight-stream/moonlight-android/commits/master. Commit-by-commit:
  - **6d4c64a5 "Disable producer throttling on the video surface"** — `Game.java`: after the frame-rate setup, `if (Build.VERSION.SDK_INT >= CINNAMON_BUN) holder.getSurface().setProducerThrottlingEnabled(false)` with comment "for reduced latency" (new Android 17 API). 5 lines; zero risk; **direct latency win on new devices**. https://github.com/moonlight-stream/moonlight-android/commit/6d4c64a5
  - **68adf9ec "Disable H.264 constraint and level_idc modifications on Oreo and later"** — `MediaCodecDecoderRenderer.java`: constraint-flag patching now only `< Build.VERSION_CODES.O`; level_idc patching only when `!refFrameInvalidationActive && SDK < O`. Two one-line condition changes; reduces SPS rewriting (and thus the attack surface that produced the Qt CVE below). https://github.com/moonlight-stream/moonlight-android/commit/68adf9ec
  - **ddb674a9 "Add support for Android 16.1 keyboard capture"** (fixes #975, #1281, #1505) — manifest `android.permission.CAPTURE_KEYBOARD`; `setMetaKeyCaptureState()` uses `WindowManager.LayoutParams.setKeyboardCaptureEnabled()` on API 36.1 (`BAKLAVA_1`) via `getWindow().getAttributes()/setAttributes()`, falling back to Samsung `SemWindowManager` in try/catch. Lets Alt-Tab/Win keys reach the host on stock Android for the first time. https://github.com/moonlight-stream/moonlight-android/commit/ddb674a9
  - **abde6021 "Fix crashes and ANRs in controller LED handling"** — `ControllerHandler.java` (+53/−22): `Light.hasRgbControl()` always returned true before API 34 because `LIGHT_CAPABILITY_RGB == 0`, so non-PlayStation pads got LED sessions; LED writes blocked the main thread (ANR); now RGB LED limited to PlayStation pads pre-34, LED updates run on a background runnable, `lightsSession.close()` wrapped, LED value migrated across device-context changes. https://github.com/moonlight-stream/moonlight-android/commit/abde6021
  - **8974dcda "Add support for newer keycodes from Android 15 and 16"** — `KeyboardTranslator.java`: `VK_F13 = 0x7C` + F13–F24 range, `KEYCODE_PRINT → 0x2A`, `KEYCODE_SCREENSHOT → 0x2C`. https://github.com/moonlight-stream/moonlight-android/commit/8974dcda
  - **98c12beb "Update to OkHttp 5.5"** — 4.12.0 → 5.5.0; adds `.fastFallback(false)` ("intolerant of thread interruptions"). https://github.com/moonlight-stream/moonlight-android/commit/98c12beb
  - **b3a7e32a "Update jMDNS and BouncyCastle dependencies"** — bcprov 1.77 → **1.85.2**, bcpkix 1.77 → **1.85**, jmdns 3.5.9 → **3.6.3**; adds `packaging { resources { excludes += '/META-INF/*.md' } }`; drops `android.useAndroidX=false`. Follow-up 4eb24a8 enables core-library desugaring (`desugar_jdk_libs:2.1.5`) so jmDNS 3.6.x works on older Android. https://github.com/moonlight-stream/moonlight-android/commit/b3a7e32a
  - **31b70030 "Update to libopus 1.6.1 and openssl 4.0.2"** — prebuilts from cgutman/moonlight-mobile-deps@419349a; **libssl removed entirely** (`LOCAL_STATIC_LIBRARIES := libopus libcrypto cpufeatures`; the openssl `Android.mk` keeps only libcrypto); 173 files. https://github.com/moonlight-stream/moonlight-android/commit/31b70030
  - **4b2221d3 "Target API level 36"** — `targetSdk 34 → 36` only (compileSdk/AGP bumped separately in 9d8b073 "Update SDK and AGP", 801dba1 "AGP 9.4.0", 9221a0c "JDK 17"). Note Google Play's 2026 target-SDK floor and the 16 KB page-size requirement for native libs apply to the fork too. https://github.com/moonlight-stream/moonlight-android/commit/4b2221d3
  - **1fa0e2a0 "Update NDK to r29"** — `ndkVersion "27.0.12077973" → "29.0.14206865"` (r29 defaults to 16 KB-aligned ELF segments). https://github.com/moonlight-stream/moonlight-android/commit/1fa0e2a0
  - **c2e224eb "Sync SDL joystick code with upstream"** — `controller_list.h` (+137/−52), `controller_type.h` (adds Steam Controller Neptune/Triton, Xbox Elite, PS5 Edge, Hori Steam Controller, 8BitDo, Switch 2), `minisdl.c` (+205/−69: central `GuessControllerType()` + ~150 Xbox One VID/PIDs), `minisdl.h` (stdbool), `simplejni.c`, `usb_ids.h`. https://github.com/moonlight-stream/moonlight-android/commit/c2e224eb
  - **0dc4c4fe "Update moonlight-common-c"** (submodule 8af4562 → 874ac95) — `MediaCodecDecoderRenderer`: `receiveTimeMs/enqueueTimeMs → receiveTimeUs/enqueueTimeUs`, new `baseTimestampUs` normalization to uptime µs; `VideoDecoderRenderer` signature; `MoonBridge`: `LI_CTYPE_STEAM = 0x04`, `LI_CCAP_DUAL_TOUCHPAD = 0x100`; `callbacks.c` passes µs; **`Android.mk` replaces `reedsolomon/rs.c` with `nanors/rs.c` + `nanors/deps/obl/oblas_common.c`/`oblas_lite.c`** and include paths. https://github.com/moonlight-stream/moonlight-android/commit/0dc4c4fe
  - Also in the same push: 3c6a0d1 "Fix rumble not working due to deprecated API call" (`VibratorManager.getDefaultVibrator()` on API 31+ — https://github.com/moonlight-stream/moonlight-android/commit/3c6a0d1), 280454f Xbox Series S/X in the Xbox One USB driver, 583f662 8BitDo vendor, 0711e23 Meta Quest UI helper, b9c5edd '+' key shift modifier, 3df0103 remove `< API 21` paths, ad86149 lint suppressions.

---

## 6. moonlight-stream/moonlight-common-c

Commits: https://github.com/moonlight-stream/moonlight-common-c/commits/master. The fork's common-c (c999436) contains upstream through 2025-07-15; **everything below "Not in fork" was verified missing by grepping the fork's sources.**

Already in the fork (Oct 2024 – Jul 2025): 0fa805d "Guard against rtsp response with no content", dff1690 "Validate channel count before parsing Opus param string" (2024-10-16), 04a2f11/12e603e/d3d3e6c ENet ping/wakeup improvements (2024-10-20), e95feaf DualSense adaptive triggers protocol extension (2025-03-25), 84f3763/58902e3 ByteBuffer multi-byte APIs (2025-06-08), 1176ca6 enet bump, c86e053 small-MTU QSV edge case, 0975a86/5f22801 iOS synthesized IPv6 / CGN mask (2025-07-15).

**Not in fork** (newest first):
- **f900dd4 (2026-09-26) "Add MODIFIER_EXTENDED flag to indicate extended keys"** — `Limelight.h` `#define MODIFIER_EXTENDED 0x10` ("0xE0 scancode prefix"); `InputStream.c` strips it for non-Sunshine hosts. Needs host ≥ Sunshine #5821. Client input correctness (Numpad Enter, right Ctrl/Alt, Insert/Delete cluster).
- **62e0663 / be43885 / d85371c (2026-09-09) RFI/IDR fixes** — `RtpVideoQueue.c`: don't speculatively report losses unless `isReferenceFrameInvalidationEnabled()` (otherwise up to 120 consecutive frames could be dropped waiting for an IDR that was never requested); `reportedLostFrame` reset moved so each lost frame is reported after a multi-block loss (and `nvPacket->frameIndex` used instead of `queue->currentFrameNumber`); `VideoDepacketizer.c`: a partially-dropped IDR now forces a wait for a fresh IDR. **Direct picture-quality/recovery win on lossy Wi-Fi; moonlight-qt shipped them 2026-09-09 (14c26d8).**
- 874ac95 (2026-08-19) `LI_CTYPE_STEAM`; 518b244 MbedTLS → PSA (irrelevant on Android/OpenSSL).
- 703a069 (2026-07-18) / 6268780 (2026-02-24) / 6250fa2 (2026-02-07) ENet submodule bumps (FreeBSD/wakeup), e41355e/2ea4775 nanors bumps.
- **1f76427 (2026-07-04) "Switch to upstream nanors with native SIMD and GFNI runtime dispatching"**, **de364b6 (2026-02-19) "Use nanors for optimized Reed-Solomon FEC decoding" (#125)**, 5551d29 SIMDe NEON path, a063522/3872285/b187204 SIMD selection fixes, 1fddbcb header hygiene, 99c45d3 `__has_builtin` compat — replaces `reedsolomon/rs.c` with vectorized GF arithmetic (NEON on arm64). **Lower FEC-recovery CPU time on packet loss = fewer late frames**; requires the `Android.mk` change from upstream 0dc4c4f.
- 2600bea (2026-05-15) dual touchpads: `LiSendControllerTouchEvent2(touchpadIndex)`, `LI_CCAP_DUAL_TOUCHPAD 0x100`.
- **7b026e7 (2026-03-30, PR #134 by foxirain) "Harden RTSP handling for malformed Session headers and oversized responses"** — `RtspConnection.c`: `MAX_RTSP_RESPONSE_SIZE (1 MiB)` enforced at three points; Session header token validated before `strdup`. The fork still does `sessionIdString = strdup(strtok_r(sessionId, ";", &strtokCtx));` with no NULL check (empty `Session:` header → `strdup(NULL)` → crash) and accumulates RTSP responses without bound. **Security hardening against a malicious/compromised host; see §9.** https://github.com/moonlight-stream/moonlight-common-c/pull/134
- 7022b33 (2026-03-28) BSD socket error refactor + `EMSGSIZE`; 3fa9191 lowercase Windows headers.
- 305993b / 07c32c8 (2026-01-30 / 02-07) use UDP `connect()` with a valid port to probe the local address (fixes bogus local-address detection on some stacks).
- **2a5a1f3 (2026-01-21) "Add support for LTR ACK control messages" (#122)** — new `SS_LTR_FRAME_ACK_PTYPE 0x0350` on `CTRL_CHANNEL_URGENT`, sent when a long-term-reference frame completes (Sunshine + RFI only); refactors RFI/LTR into one queue. Host-side LTR usage is not yet in a Sunshine release that I could identify, but moonlight-qt ships it (05ef938, 2026-01-21).
- 0586f3d / 3a377e7 (2026-01-05) thread-context leak fix; 435bc6a `pthread_attr` leak.
- **b126e48 / 20c05ed (2025-11-25)** better locking for batched mouse/gamepad sensor events; **don't take the ENet mutex when querying RTT** (`LiGetEstimatedRttInfo`) — the Android overlay polls RTT, so this removes a lock that contends with the control-stream thread.
- **e59a5f5 (2025-11-09) "Rewrite gamepad input batching to batch on the enqueue-side"** — `InputStream.c` (+101/−87), `currentQueuedControllerPacket[MAX_GAMEPADS]`; batches interleaved multi-controller input and avoids queue allocation — input-latency/CPU win.
- **82ee2d6 / a3ebaaf / e356b2c / fdd0265 (2025-11-07) high-resolution stats** — `LiGetMicroseconds()`, `LiGetRTPAudioStats()`, `LiGetRTPVideoStats()`, `DECODE_UNIT.receiveTimeUs/enqueueTimeUs/presentationTimeUs` (API break; the Android side is upstream 0dc4c4f). Sub-millisecond frame-time stats for the overlay.
- 2d984f4 ENet FreeBSD; 1c86405 Vita.

Apollo-specific additions the fork must keep when rebasing: `LiSendExecServerCmd`, `LiSendEmptyPayload`, `IDX_EXEC_SERVER_CMD` (ClassicOldSong 84af637 2024-09-10, c999436 2025-09-01).

---

## 7. moonlight-stream/moonlight-qt

Releases: https://github.com/moonlight-stream/moonlight-qt/releases — **v6.2.0 released 2026-10-04** (first release since v6.1.0, 2024-09-17) — https://github.com/moonlight-stream/moonlight-qt/releases/tag/v6.2.0

Items with an Android analogue (commit list https://github.com/moonlight-stream/moonlight-qt/commits/master):
- **Security**: v6.2.0 fixes CVE-2026-33546, CVE-2026-33547, CVE-2026-41210 ("could allow a malicious host to crash Moonlight when connecting") — §9.
- **Frame-loss recovery**: 14c26d8 (2026-09-09) "Update moonlight-common-c with frame loss fixes" → port via §6.
- **Audio latency**: 4cf498b (2026-05-11) "Use queued audio duration instead of queued frame count to constrain latency" — caps the SDL queue at ≤ 50 ms of audio instead of 10 frames, because negotiating 10 ms Opus packets (`x-nv-aqos.packetDuration`, chosen from decoder capabilities) doubled the latency cap. Android's `AndroidAudioRenderer` has the same packet-count assumption to check.
- **Enter vs Numpad Enter**: c0f62ad (2026-09-26) + `MODIFIER_EXTENDED`.
- **Steam Controller type**: d2f6990 (2026-08-19) `LI_CTYPE_STEAM`; 8369d1a gamepad CPU reduction.
- **Color range**: Aug 6 2026 series (c1623ff "Switch the default renderer color range to full", 3f26217/4570fba/a903c5c/2bea406, plus FFmpeg fix 2e13ed9 for 8-bit D3D11VA full range) — client now requests full range by default (`x-nv-video[0].encoderCscMode` = (colorSpace << 1) | colorRange). The Android renderer should be checked for whether it ever requests/handles full-range correctly on MediaCodec; upstream Android made no change here.
- **SPS fixups**: 2fc0d84/b7adc70/3e24a7a (2026-07-26/27) "Write the original H.264 SPS if it required no fixups", vendored h264bitstream — this is the path that produced CVE-2026-41210; the Android analogue is the jcodec-based SPS patching that upstream 68adf9ec narrowed.
- **Real client UID**: 170801b (2026-06-21) "Send the real client UID for non-GFE hosts" (`uniqueid` from `IdentityManager` instead of `0123456789ABCDEF`) — Artemis already does this (Apollo per-client identity); upstream Android still hard-codes the placeholder.
- **Experimental tags removed from HDR, AV1 and YUV 4:4:4** (444c6cc, 2026-06-28); 53a7680 (2026-03-28) "Fix incorrect autoselection of SW AV1 over HW H.264 for SDR" — same codec-preference pitfall exists in `MediaCodecHelper`'s AV1 selection.
- **Pipeline/pacing**: 1e825c8 (2026-01-20) render↔decode fence optimization, f6e08f8 separate D3D11 decode/render devices, b41c402 "Ensure there are enough hwframes for Pacer", d865c77 "Crop the incoming frames if they deviate slightly from the expected size", efa67fe (2026-02-14) "Disable VBlank virtualization with dynamic refresh rates" (VRR), cd13910 YUV 4:4:4 in DXVA2 — all renderer-specific; the conceptual analogue on Android is not holding the output buffer across the render (see Artemis PR #443/#429 and Moonlight X's pacer).
- **Robustness vs. host**: 63c48be (2026-05-24) "Improve handling of malformed XML responses" (bounds checks in `getAppList()`/`getDisplayModeList()`, pairing stage 2/3 decrypted-size checks), 1eb76bb QSslKey BIO deep copy (potential UAF), f9bb455 (2025-08-25) CFG/EHCont/CET on Windows.
- SIMD FEC (e596c2d/09675bf/8f994dd Feb 2026, 0c89703 Jul 2026), LTR ACK (05ef938), dual touchpads (8fe279c), OpenSSL 4.0 build (e785be0), Qt 6.12, SDL3 + sdl2-compat, default 1080p (018c851).

---

## 8. Other Moonlight Android forks and enhancements (2025–2026)

| Fork | Maintainer | Base | What it adds | Latest |
|---|---|---|---|---|
| **Moonlight X** — https://github.com/MoreOrLessSoftware/moonlight-android | MoreOrLessSoftware | upstream Moonlight + "features, fixes and optimizations from Artemis" | Experimental **Vulkan video renderer** (zero-copy from decoder, GPU color conversion, optional dithering, SDR or **HDR10** output); **"Sync to host frame timing"** pacing mode for their Sunshine fork's present-time capture (frame shown at host timestamp + jitter buffer, locked to whole vsyncs, drift corrected one vsync at a time; 60 fps stream on a 120 Hz panel keeps 120 Hz → ~half present-to-screen latency); jitter-buffer presets "Lowest latency…Smooth"; **refresh-rate matching** (measures real panel rate, requests matching fps to stop drift repeats/skips); **PyroWave** decode on arm64/Vulkan 1.3 with late/lossy-frame display (v0.5.5: five blur-tolerance levels); in-stream codec override; per-app overrides; ULL flags for Exynos/Amlogic; **Force HDR (10-bit SDR)**; LFR logic removed (v0.4.4); Pixel 10/Android 17 perf fixes. Companion host **MoreOrLessSoftware/Sunshine** mlsoft-r6 (2026-09-30: capture-on-present, **NVENC sub-frame sending**, configurable send rate 800 Mbps, cursor updates synced to game frames, precise client refresh rate) / r7 (2026-10-02: bitrate boost when game fps < stream fps, per-frame latency-trace CSV, PyroWave negotiation with Nonary hosts) — https://github.com/MoreOrLessSoftware/Sunshine/releases | v0.5.5, 2026-10-02 (v0.5.2 2026-09-30, v0.4.12 2026-07-23, v0.2.16 2025-12-31) |
| **Artemide** — https://github.com/derflacco/moonlight-android | derflacco | **Artemis** | MediaTek decode-latency work (RFI for `c2.mtk`/`omx.mtk`, LFR), then a full rework: async decoding by default, "FastGL" direct present, FSR upscaler presets, VSync/FastVsync via Choreographer, exposed dequeue timeout, lock-free latency tracking, "NanoPacer" for 120 Hz, CPU-boost with cluster selection, surface frame-rate hints from stream fps, immediate frame release ("GPU Raw"), ATV compositor workaround. Author's own "chaotic playground" warning. | Artemide_Experimental_Async_0.5.1, 2026-08-30 (0.1 2026-01-14) |
| **Artemis Amlogic HEVC fixes** — https://github.com/Nun-z/artemis-moonlight-android-hevc-fix | sven253 (+ farnsworth3010, Viktsolovevwork278) | **Artemis** | Optional HEVC low-latency modes, RFI disable, **decoder-stall watchdog**, non-blocking output queue (renderer shutdown hangs), "latest-frame rendering fix" restoring vsync pacing (micro-stutter), GPU-composition forcing; Homatics Box 4K Pro V2 (S905X5M). All off by default. | no tags |
| https://github.com/osanchezgr-hue/moonlight-android-hevc-fix | osanchezgr-hue | upstream | Xiaomi TV Stick MiTV-AYFR0 / Android TV 14 HEVC slideshow fix (same Amlogic `KEY_LOW_LATENCY` bug as Artemis PR #603 / upstream issue #1584) | 2026-07-28 |
| https://github.com/joemossjr16/artemis-android-pyrowave | joemossjr16 (also Vibepollo fork "pyrollo" with a D3D11→Vulkan zero-copy PyroWave encoder) | **Artemis** | Opt-in **Vulkan PyroWave renderer**; branches `pyrowave` (stable) / `pyrowave-adreno-tile` (experimental) | no tags |
| https://github.com/alonsojr1980/moonlight-android-turbo | alonsojr1980 | upstream | Decode-latency fixes for Tab S9 FE / S23; README: "merged to Artemis fork, please use that" | — |
| https://github.com/marcusbooker77/artemis-android | Marssvoodoo | **Artemis** | "SudoVDA adaptive streaming features" (undocumented; 2 stars) | — |
| **Moonlight V+** — https://github.com/qiin2333/moonlight-vplus | qiin2333 | upstream (4k+ stars, 3,965 commits) | 144/165 Hz unlock, **HDR/HLG**, custom resolutions, gyro aiming, audio-driven haptics, **microphone redirection**, floating control ball; companion host "Foundation Sunshine" | see releases page |
| https://github.com/Gilleece/moonlight-android-xr | Sean Gilleece | upstream | Native OpenXR client (Quest 3, Pico 4 Ultra; XR2 Gen 1 reduced) with on-device depth-model 3D conversion | — |
| Niche: TrueZhuangJia/moonlight-android-Enhanced-MultiTouch (mobile-game UI), LeiaInc/Moonlight3D (Lume Pad 2), informalTechCode/moonlight-android-RayNeoX3, MobinYengejehi/Artemis (plain fork) | | | | |

PC-side forks relevant as reference designs: Nonary/moonlight-qt v6.1.0-vrr18 (VRR adaptive jitter buffer, PyroWave), FoggyBytes/StreamLight v6.4.1 (Windows; VRR with three timing profiles, fractional V-Sync, in-stream res/fps/bitrate/HDR changes, PyroWave, StreamTweak host bridge — https://github.com/FoggyBytes/StreamLight), karsyboy/moonlight-qt-pyrowave, wjbeckett/artemis. Other hosts: RamazanKara/Butterpollo (AMD AMF/WGC, PyroWave wire-compatible with the pyrowave clients), Polaris (named in https://github.com/4o66/sunshine-apps-ui/issues/51; not researched further).

No relevant Reddit threads surfaced through the search tool (results were dominated by NASA "Artemis"); the GitHub sources above are the primary evidence.

---

## 9. Security (CVEs/GHSAs since 2024) — relevance to a client that only talks to its own host

Threat model used: the host is yours, so the realistic attacker is (a) a compromised or spoofed host on the LAN/VPN (DNS/mDNS spoofing, a rogue "host" you accidentally pair with), (b) an attacker on the path, (c) a malicious app on the Android device. "Host-only" issues matter indirectly: a compromised host can then attack the client.

**Moonlight clients / moonlight-common-c**
- **CVE-2026-41210 / GHSA-5rvh-v25g-vrgv** (Moderate 6.5, 2026-10-04): remote heap overflow in moonlight-qt's H.264 SPS fixup path (`ffmpeg.cpp` `read_nal_unit()` → h264bitstream `sps_table[seq_parameter_set_id]`, 32 entries, no bounds check) when the decoder lacks `CAPABILITY_REFERENCE_FRAME_INVALIDATION_AVC`. Affects ≤ v6.1.0, fixed v6.2.0. Reporter foxirain. **Android does not share the code** (it patches SPS with jcodec in Java, so no memory corruption), but the Android parse is not wrapped in try/catch — a crafted SPS can crash the client by exception. https://github.com/moonlight-stream/moonlight-qt/security/advisories/GHSA-5rvh-v25g-vrgv
- **CVE-2026-33546, CVE-2026-33547**: listed in the v6.2.0 notes as fixed; no GHSA, NVD, OSV or cve.org record was reachable from this environment and GitHub's advisory DB has no entry. By elimination — same reporter, same window, described as "malicious host can crash Moonlight when connecting" — they most plausibly correspond to the two issues fixed in common-c PR #134 (empty `Session:` header → `strdup(NULL)`; unbounded RTSP response buffering). **The fork's common-c has neither fix** (verified by grep). Treat as unconfirmed mapping but a confirmed missing hardening.
- CVE-2023-42799/42800/42801 (common-c buffer overflows) — already fixed in the fork's base (Android v12.0).

**Sunshine (host) — https://github.com/LizardByte/Sunshine/security/advisories**
| Advisory | Sev | Published | Affected → fixed | Client relevance |
|---|---|---|---|---|
| GHSA-ph75-mgxh-mv57 / **CVE-2026-32253** | Critical 9.8 | 2026-05-21 | < 2026.516.143833 | `openssl_verify_cb` in `src/crypto.cpp` treated missing-issuer / expired / not-yet-valid client certs as valid → unauthenticated session launch + input injection. **Apollo (all releases, incl. v0.4.8) and therefore Vibepollo builds before their own backport are vulnerable**; Apollo master fixed 2026-05-21 but unreleased. Advise users: VPN/LAN only. |
| GHSA-36ff-frg7-492f | High 7.5 | 2026-09-07 | ≥ 0.1.0 → 2026.906 | Pairing PIN could be applied to the wrong pending session during concurrent pairing; attacker on the network can hijack a pairing. Host-side fix; clients unchanged. |
| GHSA-6jvv-jqr7-m6m3 | High 7.6 | 2026-09-07 | ≥ 2026.329 nightly → 2026.906 | Disabled client could bypass revocation with a cert derived from its paired cert. Host-side. |
| GHSA-26q2-58j6-qmvv | High 8.1 | 2026-09-07 | ≥ 0.21.0 → 2026.906 | Paired client's malformed input packet → OOB heap write (DoS/memory corruption). Host-side. |
| GHSA-6w33-pjh7-p77c | High 8.1 | 2026-09-07 | 0.16.0–2026.516 → 2026.906 | Unicode input packet length not validated → heap over-read / crash. Host-side. |
| GHSA-c428-87f8-rrv5 | Moderate 5.9 | 2026-09-07 | ≥ 0.1.0 → 2026.906 | Unauthenticated DoS via < 2-byte ENet control packet. Host-side. |
| GHSA-fp6g-27w5-489j | High | 2026-09-15 | → 2026.914 | Linux: untrusted GUI modules run with Sunshine capabilities. Host-side. |
| GHSA-6p7j-5v8v-w45h | Moderate | 2025-09-23 | → 2025.923 | Windows unquoted service path (local). |
| GHSA-39hj-fxvw-758m / GHSA-x97g-h2vp-g2c5 | Critical / Moderate | 2025-06-30 | → 2025.628 | Web-UI CSRF → admin command injection; clickjacking. |

**OpenSSL** — the fork ships **1.1.1q (2022)**. 1.1.1 reached end-of-life on 2023-09-11; public fixes after 1.1.1q (from the 1.1.1-stable `NEWS`): 1.1.1t (2023-02-07) CVE-2022-4304 (RSA timing oracle), CVE-2022-4450 (PEM double free), CVE-2023-0215 (BIO UAF), CVE-2023-0286 (X.400 type confusion); 1.1.1u (2023-05-30) CVE-2023-0464/0465/0466 (policy checks), CVE-2023-2650 (OBJ_obj2txt DoS); 1.1.1v (2023-08-01) CVE-2023-3446/3817 (DH checks); 1.1.1w (2023-09-11) CVE-2023-4807 (POLY1305 AVX512 register corruption on Windows). Later advisories keep listing 1.1.1 as affected with no public fix, e.g. CVE-2026-54874 (DTLS future-epoch memory) and CVE-2026-84782 (DTLS heap leak, CVSS 8.2, 2026-09-29 advisory) — https://securityonline.info/openssl-vulnerabilities-sept-2026/, https://groups.google.com/a/openssl.org/g/openssl-announce/c/sKWebyQlhy4. Relevance: the Android client uses libcrypto only for the stream's AES-GCM/CBC and hashing inside common-c (TLS to the host is Android's platform stack via OkHttp, pairing crypto is BouncyCastle), so the TLS/DTLS/PKCS/CMS CVEs are mostly dead code — but it is EOL crypto statically linked into every APK. Upstream's 31b7003 moves to **4.0.2 (2026-08-25)** and drops libssl entirely; note 4.0.2 itself predates the 2026-09-29 advisory (a 4.0.3 is implied; not verified). Source for the 3.x/4.0 CVE lists: https://raw.githubusercontent.com/openssl/openssl/master/NEWS.md.

**BouncyCastle** — fork: bcprov/bcpkix-jdk18on **1.81**; upstream now 1.85.2/1.85.
- **CVE-2026-13506 / GHSA-qp49-qgx5-5m26** (High, CVSS4 8.7, 2026-08-03): lazy ASN.1 sequence forcing resets the nesting-depth guard → uncontrolled recursion/DoS on crafted ASN.1; **bcprov-jdk18on < 1.85**. Relevant: the client parses the host's X.509 certificate (`plaincert` during pairing) with BC — a hostile host can crash the client. https://github.com/advisories/GHSA-qp49-qgx5-5m26
- CVE-2026-59651 / GHSA-mwmr-38hj-q7gm (BKS keystore accepts 16-bit MAC, < 1.85) — Moonlight stores PEM files, not BKS → n/a.
- CVE-2026-5598 (FrodoKEM timing, fixed 1.80.2/1.81.1/1.84), CVE-2026-0636 (LDAP injection 1.74–1.83), CVE-2026-3505 (bcpg PGP AEAD) — code paths not used → n/a. CVE-2025-8885 (≤ 1.77) / CVE-2025-8916 (≤ 1.78) — 1.81 already clear.

**OkHttp 4.12.0** — no advisories for 4.12 (GitHub DB lists only CVE-2021-0341, hostname verifier, fixed 4.9.2; Moonlight pins the server cert in its own TrustManager anyway). Upstream moved to 5.5.0 for maintenance; `fastFallback(false)` is required with 5.x.

**jmDNS 3.6.2** — no known advisories; upstream's 3.6.3 needed `coreLibraryDesugaring` for older Android (4eb24a8).

**libopus 1.5.2** — no Opus CVEs in 2025–2026 (the only recent "opus" CVE, CVE-2026-63633, is in FreeRDP's use of the decoder). 1.6 (2025-12-15) adds DRED improvements, BWE, 96 kHz "Opus HD", 24-bit API; 1.6.1 (2026-01-13/14) is a bug-fix release — https://github.com/xiph/opus/releases/tag/v1.6

**ENet** — no CVEs (last ones are from 2006). Sunshine's GHSA-c428 is the host's handler. cgutman/enet's last change is ECN/L4S (2024-02-03); upstream common-c bumped its enet submodule in Jul 2025, Feb 2026 and Jul 2026 (FreeBSD/wakeup logic).

**Android MediaCodec** — **CVE-2025-54957** (Dolby UDC DD+ decoder inside the `mediacodec` process; zero-click RCE chain on Pixel 9 published by Project Zero, 2026-01) is the current reminder that host-supplied bitstreams reach vendor decoders. No specific H.264/HEVC/AV1 decoder CVEs could be pulled (NVD blocked). Mitigation posture: keep `targetSdk` current, keep the SPS rewriting minimal (upstream 68adf9ec), and gate exotic audio paths (PR #592's AC-3 encoding) behind opt-in. https://projectzero.google/2026/01/pixel-0-click-part-1.html

---

## Top candidates to port into an Artemis-based fork (ranked)

**Security**
1. **Rebase `moonlight-common-c` onto upstream 874ac95+ (ideally f900dd4) while re-applying the three Apollo additions** (`LiSendExecServerCmd`, `LiSendEmptyPayload`, `IDX_EXEC_SERVER_CMD`). This single move brings the RTSP hardening (PR #134: Session-header NULL check, 1 MiB response cap — the likely CVE-2026-33546/33547 fixes), the Sep-2026 RFI/IDR fixes, SIMD FEC, enqueue-side gamepad batching, lock-free RTT reads, µs stats, LTR ACK, dual touchpads, `LI_CTYPE_STEAM` and `MODIFIER_EXTENDED`. Pair it with upstream Android 0dc4c4f (`Android.mk` nanors sources, `MoonBridge` constants, µs decoder timestamps, `callbacks.c`).
2. **BouncyCastle 1.81 → 1.85.x** (upstream b3a7e32a: `bcprov-jdk18on:1.85.2`, `bcpkix-jdk18on:1.85`, `packaging.resources.excludes += '/META-INF/*.md'`) — closes CVE-2026-13506 (crafted host cert → client DoS). Trivial.
3. **OpenSSL 1.1.1q → 4.0.2 / libopus 1.5.2 → 1.6.1** (upstream 31b7003 prebuilts; drop `libssl` from `LOCAL_STATIC_LIBRARIES`). Removes EOL crypto from the APK; also gives Opus 1.6 decoder improvements.
4. Wrap the jcodec `H264Utils.readSPS()` path in `MediaCodecDecoderRenderer.submitDecodeUnit()` in try/catch and take **68adf9ec** (no constraint/level patching on Oreo+) — the Android analogue of CVE-2026-41210's lesson.
5. Tell users: Apollo v0.4.8 is unpatched for CVE-2026-32253 and (by inheritance) the 2026-09 Sunshine input/pairing/ENet bugs; expose only over LAN/VPN. Consider surfacing a warning in the client when `serverinfo` reports an Apollo/Sunshine version below the fixed ones.

**Performance / latency**
6. **6d4c64a5** — `Surface.setProducerThrottlingEnabled(false)` on API ≥ 37 (five lines in `Game.java`).
7. **Capability-gated low latency (PR #590) + Amlogic exclusions (PR #603) + 4K display-mode fix (PR #601)** — the three best-tested open Artemis PRs; #603 and #601 are a handful of lines each, #590 brings tests and a bounded fallback ladder.
8. **Audio queue bounded by duration, not frame count** (moonlight-qt 4cf498b) — check `AndroidAudioRenderer` for the same 10-packet assumption that doubles latency when 10 ms Opus packets are negotiated; optionally the AAudio renderer (PR #567) behind a flag for ATV devices.
9. **Enqueue-side gamepad batching + lock-free RTT** (common-c e59a5f5, 20c05ed, b126e48) — comes with item 1.
10. **Refresh-rate matching and "sync to host timestamp" pacing** as in Moonlight X (v0.5.2/0.5.5): measure the real panel rate before connecting and request a matching fps (`clientRefreshRateX100` is already sent — fill it from the measured rate), keep a 120 Hz panel at 120 Hz for a 60 fps stream, and when the host is the MoreOrLessSoftware Sunshine fork use host presentation timestamps + a small jitter buffer. Larger effort; the design is public.

**Picture quality**
11. **RFI/IDR fixes (62e0663, be43885, d85371c)** — fewer dropped-frame bursts and faster recovery on Wi-Fi; comes with item 1.
12. **Force-HDR / 10-bit SDR** (Artemis PR #559, Moonlight X "Force HDR", Vibepollo per-app 10-bit SDR): advertise HEVC/AV1 Main10 and send `dynamicRangeMode=1` without engaging the display's HDR mode so SDR content streams in 10-bit (less banding). Needs a host that does not force HDR when the content is SDR (Vibepollo handles it; stock Sunshine will switch HDR on).
13. Full-range color handling review (moonlight-qt Aug-2026 series): confirm what `encoderCscMode` the Android client requests and that MediaCodec/SurfaceView honor it; upstream Android has not addressed this.
14. **PyroWave Vulkan decode** (joemossjr16 Artemis branch, Moonlight X implementation, Vibepollo/Butterpollo/MoreOrLessSoftware hosts and now Steam): the highest-ceiling picture-quality/latency feature for wired LAN, but a large port (Vulkan compute renderer, new codec negotiation) and only for arm64 Vulkan 1.3 devices.

**Input / platform hygiene (cheap, from upstream v12.2)**
15. ddb674a9 (Android 16.1 keyboard capture + `CAPTURE_KEYBOARD` permission), 8974dcda (F13–F24/Print/Screenshot), abde6021 (LED ANR fix), 3c6a0d1 (`VibratorManager` rumble fix), c2e224eb (SDL controller DB sync), 4b2221d3 + 1fa0e2a0 + 9d8b073/801dba1/9221a0c (targetSdk 36, NDK r29 16 KB pages, AGP 9.4, JDK 17), 98c12beb (OkHttp 5.5 with `fastFallback(false)`), 4eb24a8 (desugaring for jmDNS 3.6.3). The fork is at targetSdk 34, which Google Play will stop accepting for updates before the end of 2026.
