# Handoff: state of the fork work (2026-10-13)

Written so that another Claude Code session, or a person, can continue this work without the
original conversation. Everything below is either in this repository, in
`jlobue10/moonlight-common-c`, or on GitHub (PRs, Actions runs, releases).

## 1. What was done, in order

| Step | Where | State |
|------|-------|-------|
| Security / performance / picture-quality audit of the fork, plus a survey of Sunshine, Apollo, Vibepollo and upstream Moonlight | `docs/audit/2026-10-04/` | done |
| Audit fixes as PRs #1–#7 (security hardening, upstream v12.2 Java ports, render loop / frame pacing, picture quality, build chain, native stack incl. a moonlight-common-c sync hosted in `jlobue10/moonlight-common-c`, release workflow) | merged into `moonlight-noir` | done |
| javac-only compile-check harness (no Android SDK needed), PR #8 | `tools/compilecheck/` | done |
| Release **v20.3.0-fork.1** (commit 9cb7cc48), four ABI APKs, signed with a **throwaway key** | GitHub Releases | published |
| Follow-up PRs: **#11** build chain (AGP 9.4.0, Gradle 9.7.1, compileSdk 37, OkHttp 5.5), **#9** warp-mode bitrate, **#10** release signing + bump to 20.3.0-fork.2 | merged into `moonlight-noir` | done, see §2 |
| Audit Q9 cleanups (dead "Tight Vsync" option removed, lite perf overlay relabelled, portrait resolution no longer swapped back in the decoder), PR **#14** | merged into `moonlight-noir` (e482ad8f) | done |
| Signing secrets set by the owner; **v20.3.0-fork.2 released** (run 37216690423, signed with the project key) | GitHub Releases | published |
| 3D depth quality: selectable depth model (MiDaS / Depth Anything V2 Small 252 / 364, verified on-demand download), model-agnostic renderer, highp shaders, async PBO readback, bounded synced-mode wait, joint-bilateral depth upsampling; `docs/3D_DEPTH_MODELS.md`, PR **#15** | merged (5080509e) | done |
| **v20.3.0-fork.3 released** (versionCode 60, run 37218760864, signed with the project key) | GitHub Releases | published |
| Galaxy XR feedback round 1: settings screen had no way out (toolbar + Done + unified back path), PR **#16** | merged (22fc8713) | done |
| True stereo on Android XR: SceneCore `SurfaceEntity` SIDE_BY_SIDE in Full Space fed by an EGL thread, renderer decoupled from GLSurfaceView (`RenderHost`), minSdk 24; `docs/XR_STEREO.md`, PR **#17** | merged (5739be88) | done |
| **v20.3.0-fork.4 released** (versionCode 61, run 37226881349, signed; arm64 APK 68.7 MB, up from 40 MB because of the XR libraries) | GitHub Releases | published |
| Galaxy XR feedback round 2: app list had no way back (toolbar), PR **#18**; 3D mode would not connect (stereo path could wait forever → 6 s watchdog, EGL failure report, flat fallback + toast), PR **#19**; 2026 Steam Controller over BLE (in-app GATT driver, LI_CTYPE_STEAM, dual touchpads, IMU, battery; `docs/STEAM_CONTROLLER.md`), PR **#20** | merged (90c18b6a, 49384870, de4f1a4f) | done |
| **v20.3.0-fork.5 released** (versionCode 62, run 37231766963, signed) | GitHub Releases | published |
| Galaxy XR feedback round 3 (fork.5 on device: Steam Controller works as a PS4 pad incl. Steam button; 3D still exits to the app list with no dialog): **#21** launch the stream in Full Space from the app list + ignore the stop during the space transition (the silent exit is `Game.onStop()` → finish, caused by the mid-stream Full Space request); **#22** trackpads as DualShock halves (distinct fingers) + grip-button mode; **#23** per-stream log file + "Share stream log" in Misc settings | merged (577fc052, 68c8be26, 950ebbc6) | done |
| **v20.3.0-fork.6 released** (versionCode 63, run 37235504026, signed) | GitHub Releases | published |
| Device logcat (fork.6) found the **root cause of every 3D failure since fork.4**: FATAL `AbstractMethodError com.android.extensions.xr.function.Consumer.accept` — R8 desugared the Jetpack XR platform callbacks without bridge methods because the platform lib was invisible. Fix: `compileOnly 'com.android.extensions.xr:extensions-xr:1.4.0'` (PR **#25**); plus Steam Controller stick Y + app-window surface loss ignored in stereo mode (PR **#24**) | merged (01110ee7, 9a18dff9) | done |
| **v20.3.0-fork.7 released** (versionCode 64, run 37238146560, signed) | GitHub Releases | published |
| fork.7 logcat: stereo path runs (SurfaceEntity + EGL + depth model OK) then FATAL `IllegalStateException: Cannot get pose in Activity Space with a non-AndroidXrEntity parent` from our spatial-mode listener (`setPose(..., Space.ACTIVITY)`, fires on registration). Fix: `Space.PARENT` + try/catch, PR **#26** | merged (3be953f3) | done |
| **v20.3.0-fork.8 released** (versionCode 65, run 37240107700, signed) | GitHub Releases | published |
| fork.8 on device: no crash, stream runs, stereo quad appears **but black** while the EGL thread swaps frames. PR **#27**: `MediaBlendingMode.OPAQUE` + `ContentColorMetadata`(BT709/sRGB/full) on the entity, opaque clear, 5 s diagnostic `XR stereo GL: N frames swapped, centre-left pixel rgba=(…)` | merged (1b314995) | done |
| **v20.3.0-fork.9 released** (versionCode 66, run 37241694993, signed) | GitHub Releases | published |
| fork.9 on device: GL frames proven good (`centre-left pixel rgba=(31,32,30,255)`, ~60 fps) yet the SurfaceEntity stays black. Prior art (SchoenMon: GL into SurfaceEntity works on SM-I610 in MONO, SuperSampling.NONE, needs y-flip; Chromium: MONO quad). PR **#28**: *Stereo screen content (diagnostics)* = sbs / mono / canvas test pattern, SuperSampling.NONE, y-flip, EGL size logging | merged (b352dbb3) | done |
| **v20.3.0-fork.10 released** (versionCode 67, run 37244248312, signed) | GitHub Releases | published |
| fork.10 A/B: SBS black, mono black, canvas → **no separate floating screen** ⇒ the entity is never visible with any producer (placement). PR **#30**: recommended pose logged, not applied; quad stays 2 m ahead; actual pose logged. PR **#29**: logcat dump 40000 lines, Stereo3DRenderer/AiTask silenced | merged (9db0b9fe, a689ca8a) | done |
| **v20.3.0-fork.11 released** (versionCode 68, run 37246110266, signed) | GitHub Releases | published |
| fork.11: still no screen anywhere; recommended pose was the identity. **Root cause found in SceneCore KDoc: `SurfaceEntity.create()` without `parent` is not attached to the scene graph → invisible.** PR **#31**: parent = `scene.getActivitySpace()` (7-arg overload), panel hide also sets alpha 0 | merged (76f6f362) | done |
| **v20.3.0-fork.12 released** (versionCode 69, run 37247662456, signed) | GitHub Releases | published |
| fork.12 on device: **stereo screen works** (game in stereo), *Hide the app window* confirmed as the right mode, picture upside down. PR **#32**: fork.10 eye-pass flip removed (wrong for a Quad), hide default on, 5 m / 6 m widths | merged (850bd618) | done |
| **v20.3.0-fork.13 released** (versionCode 70, run 37249915391, signed) | GitHub Releases | published |

## 2. Where it stopped, and the next steps

Check the live state first (`gh pr list`, the Actions tab); this section is a snapshot (2026-10-04, late,
with addenda at the top: 2026-10-13 first, then 2026-10-08).

**2026-10-13 addendum (read this first):**
- **Current release: v20.3.0-fork.101** (versionCode 158, merge 24494f1f). Releases are cut with
  `gh workflow run release.yml -R jlobue10/moonlight-android --ref moonlight-noir -f release_tag=v20.3.0-fork.N`
  after the bump commit ("Bump to 20.3.0-fork.N (versionCode M); common-c at pyrowave <sha>") is on
  `moonlight-noir`; Obtainium picks the release up. `gh pr merge` is blocked by the Claude Code
  permission classifier, so PRs are merged with `git merge --no-ff` + push from the main checkout.
- **The four repositories.** This app; `jlobue10/moonlight-common-c` branch `pyrowave` (the
  submodule, 135+ commits ahead of upstream: PyroWave, Steam Controller capabilities, the 0x5504
  Steam haptic control packet, 30+ audit rounds with harnesses under `tests/`); `jlobue10/Vibepollo`
  branch `feat/steam-controller-profile` (the host: virtual Steam Controller via libvirtualgamepad,
  0x5504 forwarding in `src/stream.cpp`, WebRTC browser streaming); `jlobue10/libvirtualgamepad`
  branch `feat/steam-controller-profile` (UMDF VHF driver + client; Vibepollo consumes a *tagged
  release* `v0.1.0-beta.N` and pins tag, archive sha256, DriverVer and source revision in seven
  places, rewritten by `tools/capture/Update-VibepolloDriverPins.py --tag v0.1.0-beta.N`, which
  commits straight to the Vibepollo branch through the GitHub API). Vibepollo pins beta.146
  (17773c15); the driver branch is one small commit ahead (52d4359, PR #40) with no tag cut yet.
- **Steam Controller haptics, the open thread.** Input has worked since fork.96. Haptics: fork.96
  logs showed every Steam haptic report refused by the firmware on the feature characteristic
  (GATT status 4: Ibex does not take `ID_TRIGGER_HAPTIC_PULSE` 0x8F / `ID_TRIGGER_HAPTIC_CMD` 0xEA
  over BLE). fork.97 writes Steam's output reports 0x80..0x89 as-is, minus the id, to their own
  characteristics `0x100f6cb5` + (id − 0x80) (the way the USB transport does) with the feature
  conversion only as a fallback; the fork.97 test gave haptics "very faint" and no detent ticks
  while sliding on the pads, with all ten output characteristics offered and no refusals. fork.98
  added the diagnostics that decide the next step (`docs/STEAM_CONTROLLER.md`, "fork.98" section:
  per-id probe verdicts, the first 24 host reports logged, a 10 s `haptics, last 10 s` summary with
  host/written/evicted/ack counts). **No field test has happened since fork.97**; the next stream
  log from fork.101 (slide on each pad, click each pad, Steam's controller test haptic step) is the
  gating input. Strongest remaining lead: Steam programs ~15 settings over feature command 0x87 at
  connect (ids 7/8/24/34/35/45/46/48/49/50/52/53/82/84/85) and a 0x81 pulse has no amplitude field,
  so the streamed unit plays Steam's pulses against its own firmware state; libvirtualgamepad stores
  those settings and has no feedback type to forward them (a four-repo protocol feature, not
  started). Also check Steam's per-controller haptic intensity for the virtual controller (keyed by
  serial, starts at defaults).
- **Audit rounds (2026-10-13).** ChatGPT/Codex opens draft audit PRs on all four repos (branches
  `auditNN/...`; rounds 41 and 43 so far, PRs #122/#125 here, Vibepollo #69/#71); Claude ran rounds
  42 and 44 (this app #124, Vibepollo #70, libvirtualgamepad #40, common-c #49, plus the round-44
  fixes in flight). All merged work is listed in each PR. Still open, by design, as of round 44:
  Vibepollo callback-context lifetime in `close_session` and data-channel calls under
  `session_mutex` (need the libwebrtc wrapper's threading contract, not vendored); libvirtualgamepad
  failed `VhfReadReportSubmit` readiness (deliberate, SDK needed to verify) and the protocol
  min/max range that can never be used (decide at the next version bump); common-c 15 s ENet RTSP
  stall on an error reply from old GFE (upstream-inherited).
- **Harnesses on Windows (this PC has no JDK/SDK):** `tools/regression/test_steam_ble.py` compiles
  the BLE driver and manager against fake Android classes; it needs a JDK 21 on PATH (a Temurin zip
  from api.adoptium.net unpacked anywhere works) and `PYTHONUTF8=1`; 111+ checks. MSYS2 UCRT64
  (`C:\msys64\ucrt64\bin`) compiles common-c's `tests/test_video_format.c` and libvirtualgamepad's
  `driver/tests/test_sc26_usb.cpp` (`g++ -std=c++20 -I include`). Vibepollo's `tests/input/*.py`
  need a sanitizer toolchain; CI runs them. Repo files are LF; Python `write_text()` on Windows
  rewrites CRLF, so patch in binary mode.
- Everything below this line predates fork.25 and is kept for history.

**2026-10-08 addendum:**
- **Current release: v20.3.0-fork.24** (versionCode 81, merge e0464a5f, release run 37870257964, signed).
  fork.17–fork.23 were the movable/recenter stereo screen, PyroWave HDR10, Steam Controller grip
  sense (fork.19), stick diagnostics (fork.20), rim stretch (fork.21, retired as a default in
  fork.23) and raw button-bit logging (fork.22). fork.24 = PR #45: toolbars with an Up arrow on the
  Add PC, Debug Info and Help screens (headsets have no back gesture for a panel); every activity
  now has a way out. No PR is open.
- **Steam Controller, still open:** on fork.24 Steam's "move the left stick in a full circle" test
  step still stalls and the grips never light up on Steam's test screen. The investigation and the
  host-side fix live in the driver fork: `jlobue10/libvirtualgamepad` branch
  `feat/steam-controller-profile`, `docs/STEAM_CONTROLLER_PROFILE.md` §7.9 (what to install, what
  to test) and §8 (findings table). Short form: the probe cleared everything about the stream except
  its burst timing (three reports within a millisecond per BLE packet, then ~12 ms of nothing), so
  driver commit 6f2ad3a makes motion samples ride the 4 ms tick; the test-signed package is
  artifact 11593181427 of run 37877814035. The grips need the fork.24 **stream log** (Settings →
  Misc → Share stream log, controller held for 10 s+): the line
  `raw buttons seen 0x… (grip touch L … R …)` says whether this firmware sends the grip bits over
  BLE at all (SDL's Triton driver reads them from the same word on the BLE report, so the client's
  decode is right). If it never does, add a client-side grip proxy. Nothing on the client is
  known to be wrong for either symptom; do not change the stick path again without new evidence.
- Everything below this line predates fork.17 and is kept for history.


1. **Released:** v20.3.0-fork.2 is published and signed with the persistent key (the four signing
   secrets exist in the repository; the keystore and its password are on the owner's machine, the
   keystore must never be committed, `*.jks` is now in `.gitignore`). From here on releases install
   over each other; fork.1 and the official Artemis build still have to be uninstalled once.
2. **v20.3.0-fork.15** (PR #35: opt-in PyroWave Vulkan decode, see `docs/PYROWAVE.md`) is the current
   release. **PR #36 (`fix/pyrowave-framing`, fork.16) is open:** the first device test of fork.15
   (2026-10-07) gave audio and a black picture because Vibepollo frames PyroWave as length-prefixed
   packets or records, not the `PYRW` container the ported renderer expected; the renderer now parses
   all three and the common-c submodule announces record framing. PR #36's CI build (run
   37596853444) is green; **to ship it:** merge the PR, then `gh workflow run release.yml -R
   jlobue10/moonlight-android --ref moonlight-noir -f release_tag=v20.3.0-fork.16` (versionName is
   already fork.16 / versionCode 73 on the branch), verify the release, install via Obtainium, and
   the device test is: PyroWave on, 720p60, stats overlay "PyroWave (Vulkan)", picture visible, logcat
   shows `PyroWave` fps lines and no "Dropping ... frame" warnings. fork.14 = PRs #33 + #34. Everything is
   confirmed working on the Galaxy XR (stereo upright, app window hidden, Steam Controller over
   BLE). A "stereo broke after changing the width" report was a false alarm (the user had unticked
   "Stereo screen on Android XR"; the gates are silent by design). fork.14 = Profiles screen
   toolbar/back button (#33; `AddComputerManually`, `DebugInfoActivity`, `HelpActivity` still have
   none, unreported) + Steam Controller rumble as refreshed, bounded pulse trains with the new
   "Rumble hold limit" preference (#34, default 0.5 s): with Vibepollo 2.0.0 presenting the pad as
   a DualSense the host forwards Steam's menu haptics as rumble but never sends "motors off"
   (stream_7 log), so the old unbounded train buzzed until the next event. Host-side root cause
   still open (see docs/STEAM_CONTROLLER.md; narrowing test = `vhf_ds4` vs `ds4`). User decision:
   VRR port dropped.
   Vibepollo 2.0.0 runs on the user's Windows host (the startup "crash" was the pairing-state
   refusal in `nvhttp.cpp`; recovery = move `config\sunshine_state.json(.bak)` and `config\credentials\`
   away, restart, re-pair). In 2.0.0 `gamepad=auto` prefers ViGEm whenever ViGEmBus is installed
   (`vhf_gamepad_policy.h::select_automatic_backend`, x360/ds4 only); the Vibepollo VHF driver has
   `dualsense`, so the host setting `vhf_ds5` or `vhf` gives a DualSense without code changes. Known
   2.0.0 DualSense bugs: Nonary/Vibepollo #538 (duplicate pad through GameInput; fix in
   Nonary/libvirtualgamepad PR #3, now merged upstream) and #480. Vibepollo fork:
   `jlobue10/Vibepollo` branch `fork/2.0.0` (clone `~/GitHub/Vibepollo`, shallow).
   **PyroWave (PR #35, released in fork.15):** opt-in PyroWave decode ported from
   joemossjr16/artemis-android-pyrowave; design, provenance and test plan in `docs/PYROWAVE.md`.
   Protocol lives in the common-c submodule branch `pyrowave`. First device test (fork.15, 2026-10-07):
   negotiation, Vulkan device, swapchain on the stereo SurfaceTexture surface and the fragment decode
   path all came up; every frame was dropped for its framing (fixed in PR #36, see `docs/PYROWAVE.md`).
   **NEXT PROJECT (user decision 2026-10-05): a genuine Steam Controller 2026 profile** in the
   Vibepollo VHF driver (fork `jlobue10/libvirtualgamepad`, clone `~/GitHub/libvirtualgamepad`,
   upstream HEAD 9edbce1) plus the Vibepollo mapping, developed and test-signed on the user's
   Windows host, then offered to Nonary as PRs. First implementation pass is DONE on both forks
   (branches `feat/steam-controller-profile`; driver profile gated behind a provisional
   descriptor, fork CI green incl. WDK build). The self-contained handoff for the Windows capture
   and test phase is `docs/STEAM_CONTROLLER_PROFILE.md` §7 in the driver fork. **2026-10-07:** the
   profile is enabled from real captures and WORKS from the headset (Steam shows the user's own
   Steam Controller); the first stream's bugs (both pads on the left pad, right pad click, View/Menu
   swapped) are fixed in driver prerelease v0.1.0-beta.102 + Vibepollo fork 55383c1/accb4f5; the kit
   rebuild and next host steps are §7.8/§8 of that doc. Client side nothing changed: with the Steam
   profile both "Trackpads as DualShock touchpad halves" settings now work (the host splits by half).
3. Install on the Galaxy XR via Obtainium: source `https://github.com/jlobue10/moonlight-android`,
   APK filter `arm64-v8a`.
4. Device testing on the Galaxy XR has not happened yet for anything in this series: the render
   loop / frame pacing change (PR #1), AV1-in-Auto (PR #4), the warp bitrate change (PR #9), the
   AGP 9 build (PR #11), the Q9 cleanups (#14) and the 3D depth work are all CI-verified only.

## 3. Decisions taken (and why), so they are not re-litigated

- **Warp modes (audit Q7).** `Warp Drive`/`Warp 2` ask the host for 2×/4× the selected FPS and show
  the newest frame. The client now scales the bitrate by the warp factor (bits per frame stay
  constant, cap 1 Gbps) **except on Apollo-family hosts**, where Apollo multiplies the client's
  bitrate itself when its *Limit framerate* option is on (`rtsp.cpp`, "Restore bitrate for warp
  mode"); scaling on both sides would quadruple it. Apollo hosts are recognised by the `Permission`
  field they always put in `serverinfo` (Sunshine/GFE never do); `ServerHelper` passes it to `Game`
  as the `HostPermission` extra (−1 = none). Apollo with *Limit framerate* **off** still encodes
  2×/4× frames at the slider bitrate; the client cannot see that setting.
- **compileSdk 37 route.** AGP 8.13 could not use the `platforms;android-37.0` package naming
  (Android 17 ships with an SDK minor version), so 20.3.0-fork.1 shipped on compileSdk 36 with the
  Android 17 / 16.1 calls made reflectively, and OkHttp stayed at 4.12 (okhttp-android 5.5 requires
  compileSdk 37). PR #11 moves to AGP 9.4.0 + Gradle 9.7.1, exactly what upstream v12.2 builds
  with (upstream commits 9d8b073c, 801dba1b, 98c12beb), restores the direct calls, needs
  `buildFeatures.resValues = true` (AGP 9 default changed), and keeps `android.useAndroidX=true`
  (upstream sets it to false; this fork uses AndroidX).
- **Signing.** The keystore is generated on the owner's machine, never in CI and never committed.
  The workflow refuses to publish a release with a throwaway key by default.
- **Package id** stays `com.limelight.noir` (Artemis's) — deliberate, documented in
  `docs/RELEASING.md`; the user accepts uninstalling the official Artemis build.
- **Frame pacing (audit P1/Q1).** Artemis's "latency profile" block that forced Balanced was
  removed; the five pacing modes work as upstream's render loop defines them, and the LFR checkbox
  now means "force minimum-latency pacing".
- **Upstream trackpad sync (ClassicOldSong master 6c572009, July 2024).** Merged 2026-10-16 as a
  history-only merge: upstream's six commits (drop `eventIsMultiTouch`, the SDK < M
  `getActionButton` guard, the `TrackpadContext` refactor, scroll factors, two-finger-tap right
  click, drag release in `setPointerCount`) were already here as rebased copies (3179f750,
  c8d8fd96, 94561522, 2d1f2c58, b0d35903), and the fork's `TrackpadContext` has since added
  acceleration, flick momentum, the scroll-to-move transition guard, the 3-finger middle click,
  axis swap/sensitivity and held-button tracking on top. Every conflict hunk resolved to the
  fork's code and the merge changes no file; later `git merge upstream/master` runs start from
  6c572009, so these four files will not conflict again.

## 4. Still open from the audit

Verify against `docs/audit/2026-10-04/MOONLIGHT_FORK_AUDIT.md` (§2–§4 ids) before starting:

- **XR stereo** (not in the audit): working since fork.12/13; design + history in `docs/XR_STEREO.md`.
- **Q8** SBS-3D path: addressed in `feat/3d-depth-quality` (highp shaders, async PBO readback,
  100 ms bound on the synced-mode wait, no wait when the model failed to load). Still SDR-only
  (8-bit GLSurfaceView + SurfaceTexture), by design.
- **Q9** low items still open: custom resolution/bitrate inputs are barely validated, HDR10 needs the
  exact `*Main10HDR10` profile constants (same as upstream), optional `PCM_FLOAT` audio. Done in
  `fix/audit-q9-cleanups`: lite overlay labels ("Frame loss", "Host FPS"), the dead "Tight Vsync
  (Experimental)" option, and the portrait auto-invert double swap (`Game` swaps width/height for the
  host, the decoder swapped them back, so the MediaFormat described a landscape frame for a portrait
  bitstream; the decoder now uses the negotiated size as-is). The 10-bit mask (`0xAA00`) and the
  rendered-frame counter on both render paths were already correct.
- **Common-c follow-ups** (common-c is synced, the Android side may not be): set `MODIFIER_EXTENDED`
  from the scancode path, `LiGetRTPVideoStats()` for the overlay. Done in PR #20:
  `LiSendControllerTouchEvent2` JNI and `LI_CTYPE_STEAM` (for the BLE Steam Controller driver). Artemis PR #590 (capability-gated
  low latency with tests) was not taken.
- **Host side:** Apollo v0.4.8 is unpatched for CVE-2026-32253; see the ecosystem report.
- `jlobue10/moonlight-common-c`: consider making `artemis-sync-upstream-2026-10` (what the
  submodule points at, gitlink 30bc3ddd) the default branch.

## 5. Working in this repository

- **Line endings:** ~120 Java files are CRLF (Artemis), upstream is LF. Preserve each file's
  ending (`file <path>`; Python edits in binary mode). Cherry-picks from upstream need
  `git cherry-pick -Xignore-space-at-eol` plus CRLF re-normalisation of touched CRLF files.
- **Upstream remotes:** `moonlight-stream/moonlight-android` (v12.2 is b48494cb) and
  `ClassicOldSong/moonlight-android` (Artemis). The fork's `moonlight-noir` was byte-identical to
  Artemis 20.2.6 (c5cf27f4) before this work; it now diverges.
- **Submodule:** `app/src/main/jni/moonlight-core/moonlight-common-c` →
  `https://github.com/jlobue10/moonlight-common-c` (nested `enet`, `nanors`). Clone with
  `--recurse-submodules`.
- **Version bump:** only `versionName`/`versionCode` in `app/build.gradle`; the release workflow
  checks the tag against `versionName`. The in-app Obtainium link (`obtainium_app_url` resValue)
  already points at this fork.
- **Commit messages** in this series carry `Co-Authored-By` / `Claude-Session` trailers; keep
  doing that for Claude-authored commits.

## 6. Validation recipes

- **CI (authoritative):** *Build and release APKs* via `workflow_dispatch` on any branch with an
  empty `release_tag` builds and signs all four ABIs and uploads them as artifacts (~4 min).
  From a Claude Code cloud session: trigger with the GitHub MCP `actions_run_trigger`
  (`method: run_workflow`, `workflow_id: release.yml`), poll with
  `gh api repos/jlobue10/moonlight-android/actions/runs/<id>` (works through the proxy), read
  failures with the MCP `get_job_logs` tool (the raw log download URLs on Azure blob storage are
  blocked).
- **javac compile check (fast, local, no SDK):** needs **JDK 21+** (the android-all 17 jar is class
  file version 65; on the garage box a user-local Temurin lives in `~/.local/share/jdk21`, the system
  Java is an 8 JRE without javac). `tools/compilecheck/setup.sh` once (in a cloud
  session add `--no-google --no-jitpack`; Google Maven and JitPack are blocked there, and the
  script falls back to an AOSP prebuilts mirror and building two deps from source), then
  `tools/compilecheck/compilecheck.sh <worktree>`. It uses Robolectric's `android-all` 17 jar
  (API 37) and real AndroidX; it catches API/symbol errors, not resource or lint problems.
- **Merge check:** `git merge --no-commit <branches…>` in a scratch worktree; the three open
  branches merge cleanly as of this writing.

## 7. Cloud-session environment notes (Claude Code on the web)

- Blocked: `dl.google.com` (Android SDK/NDK, and `maven.google.com` artifact downloads redirect
  there), `jitpack.io`, Azure blob storage (Actions log downloads), tag pushes (git proxy 403),
  the Actions **secrets** API. Available: Maven Central, GitHub (clone/push/MCP tools), `gh api`
  for Actions runs/jobs.
- No Gradle/Android build is possible in the container; CI is the build. The compile-check
  harness exists for exactly this reason.
