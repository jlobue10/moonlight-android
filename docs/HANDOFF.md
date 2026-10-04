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
| 3D depth quality: selectable depth model (MiDaS / Depth Anything V2 Small 252 / 364, verified on-demand download), model-agnostic renderer, highp shaders, async PBO readback, bounded synced-mode wait, joint-bilateral depth upsampling; `docs/3D_DEPTH_MODELS.md` | PR from `feat/3d-depth-quality` | see §2 |

## 2. Where it stopped, and the next steps

Check the live state first (`gh pr list`, the Actions tab); this section is a snapshot (2026-10-04, late).

1. **Released:** v20.3.0-fork.2 is published and signed with the persistent key (the four signing
   secrets exist in the repository; the keystore and its password are on the owner's machine, the
   keystore must never be committed, `*.jks` is now in `.gitignore`). From here on releases install
   over each other; fork.1 and the official Artemis build still have to be uninstalled once.
2. **Open PR: 3D depth quality** (`feat/3d-depth-quality`). CI-verified only; review and merge, then
   bump `versionName`/`versionCode` and dispatch *Build and release APKs* with `release_tag=v<versionName>`.
   What it changes and what it still owes (device validation order) is in `docs/3D_DEPTH_MODELS.md`.
   Deviation from the agreed plan: the Depth Anything files are downloaded by the app **from the
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
  from the scancode path, `LiSendControllerTouchEvent2` JNI for dual-touchpad pads, `LI_CTYPE_STEAM`
  in `ControllerHandler`, `LiGetRTPVideoStats()` for the overlay. Artemis PR #590 (capability-gated
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
