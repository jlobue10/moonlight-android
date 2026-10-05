# Handoff: state of the fork work (2026-10-04)

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

## 2. Where it stopped, and the next steps

Check the live state first (`gh pr list`, the Actions tab); this section is a snapshot (2026-10-04, late).

1. **Released:** v20.3.0-fork.2 is published and signed with the persistent key (the four signing
   secrets exist in the repository; the keystore and its password are on the owner's machine, the
   keystore must never be committed, `*.jks` is now in `.gitignore`). From here on releases install
   over each other; fork.1 and the official Artemis build still have to be uninstalled once.
2. **v20.3.0-fork.11** (PRs #29–#30, bump cef37205) is the current release; no PR is open. Headset test:
   *Stereo screen content* = side-by-side, launch; if no second screen, enable *Hide the app window in
   stereo mode* and retry; then share logs and read `XR stereo: screen placed at t=(…)` and
   `XR stereo: system recommended pose t=(…) scale=…`. If the quad is still invisible at a sane pose,
   next suspects: the entity needs a parent/subspace (SchoenMon parents to a root entity inside a
   Compose Subspace; Chromium parents under its panel), or the quad is behind the main panel. Deviation from the agreed plan: the Depth Anything files are downloaded by the app **from the
   publishers' GitHub releases** (SHA-256 pinned), not from a mirror in this repository, because the
   Claude Code session was not allowed to download third-party model binaries to re-host them. A
   mirror remains optional; the doc says how (pre-release in this repo, keep NOTICE/LICENSE).
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

## 4. Still open from the audit

Verify against `docs/audit/2026-10-04/MOONLIGHT_FORK_AUDIT.md` (§2–§4 ids) before starting:

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
