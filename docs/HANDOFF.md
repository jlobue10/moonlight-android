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

## 2. Where it stopped, and the next steps

Check the live state first (`gh pr list`, the Actions tab); this section is a snapshot.

1. **PRs #11, #9 and #10 are merged** into `moonlight-noir` (merge commits 83d95dea, e7c0ad8b,
   baa62bbb). The build-only validation run of #11's branch was green
   (https://github.com/jlobue10/moonlight-android/actions/runs/37211903167); a build-only run of
   the merged `moonlight-noir` (baa62bbb) was dispatched right after merging — confirm it is green
   in the Actions tab before releasing.
2. **Signing secrets (needs the repository owner's machine):** run
   `tools/release-signing/setup-signing-secrets.sh` (or the `.ps1`) with a JDK and a logged-in `gh`.
   It creates `release.jks` locally and stores `KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`,
   `KEY_PASSWORD` as repository secrets. A Claude Code cloud session cannot do this: its GitHub proxy
   denies the Actions secrets API ("Access to this GitHub Actions path is not permitted").
3. **Release v20.3.0-fork.2:** dispatch *Build and release APKs* on `moonlight-noir` with
   `release_tag = v20.3.0-fork.2` (the tag must equal `v` + `versionName` in `app/build.gradle`,
   which is `20.3.0-fork.2` / versionCode 59 since PR #10). With the secrets in place the APKs are
   signed with the persistent key; without them the workflow refuses to publish unless
   `allow_throwaway_key` is set. Tag pushes from a cloud session are rejected by the git proxy
   (HTTP 403), which is why the `release_tag` input exists.
4. Install on the Galaxy XR via Obtainium: source `https://github.com/jlobue10/moonlight-android`,
   APK filter `arm64-v8a`. The package id is still Artemis's `com.limelight.noir`, and fork.1 was
   throwaway-signed, so both the official Artemis build and fork.1 must be uninstalled before
   fork.2; from fork.2 on, releases install as updates.
5. Device testing on the Galaxy XR has not happened yet for anything in this series: the render
   loop / frame pacing change (PR #1), AV1-in-Auto (PR #4), the warp bitrate change (PR #9) and the
   AGP 9 build (PR #11) are all CI-verified only.

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

- **Q8** SBS-3D path: `precision mediump` in `ShaderUtils`, synchronous `glReadPixels`, unbounded
  wait for the depth model — not touched.
- **Q9** low items: perf overlay labels ("Packet loss" is frame loss, "FPS" is host send rate), the
  portrait auto-invert resolution swap, the dead "Tight Vsync (Experimental)" option (still in
  `strings.xml`/preferences), optional `PCM_FLOAT` audio.
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
- **javac compile check (fast, local, no SDK):** `tools/compilecheck/setup.sh` once (in a cloud
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
