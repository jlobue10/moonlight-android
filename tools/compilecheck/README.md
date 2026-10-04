# tools/compilecheck -- javac-only compile check

A smoke test for environments that cannot run the real Gradle build: no Android
SDK/NDK, no access to Google Maven (`dl.google.com`) or JitPack -- typically a
cloud agent session or a minimal CI box.  It answers one question quickly
(about 10-20 s): *do the app's Java sources still compile?*

It is **not** a replacement for `./gradlew assemble`.  See "What it does not
check" before trusting a PASS.

## Usage

    tools/compilecheck/setup.sh            # once per checkout (downloads ~200 MB into tools/compilecheck/.cache/)
    tools/compilecheck/compilecheck.sh     # compiles the repository that contains the script
    tools/compilecheck/compilecheck.sh /path/to/other/worktree    # or any other worktree of this repo

`compilecheck.sh [<worktree>] [--no-flavors] [--keep]`

* pass **main**: every `.java` under `app/src/main/java` (plus `app/src/release/java`
  if it exists) with the `nonRoot_game` BuildConfig;
* pass **root**: `main` + `app/src/root/java` with the `root` BuildConfig
  (`ROOT_BUILD = true`) -- generically, one extra pass per product flavor that has its
  own `src/<flavor>/java`; `--no-flavors` skips those;
* `--keep` keeps the class files (`.cache/work/<worktree-basename>/out/`).

The worktree is never written to.  Generated `R.java`/`BuildConfig.java`, the
source lists and the javac logs are left in `.cache/work/<worktree-basename>/`.

Exit codes: **0** every pass compiled (`RESULT: PASS`); **1** javac reported
errors (full javac output is printed, `RESULT: FAIL`); **2** bad arguments or not
an app worktree; **3** setup incomplete (run `setup.sh`) or a tool is missing.

Requirements: JDK 11+ (`javac`, `java`, `jar`), `python3`, and for `setup.sh`
also `git`, `curl`, `unzip` and Gradle (`gradle` on PATH, else the repo's
`./gradlew`, or `--gradle <path>`).

## What setup.sh does (idempotent -- re-run it after dependency bumps)

Everything lands in `tools/compilecheck/.cache/` (git-ignored); nothing is
committed and nothing is fetched by `compilecheck.sh` itself.

1. **Android framework**: Robolectric's `org.robolectric:android-all:16-robolectric-13921718`
   from Maven Central (Android 16 / API 36 framework classes, sha1-verified).
2. **Dependencies** are read from `app/build.gradle` (`gradlecfg.py --deps`, so a version
   bump there is picked up) and resolved -- with their transitive closure -- by the small
   Gradle project in `deps/`, leniently, through **Google Maven, Maven Central and JitPack**.
   Jars are copied to `.cache/lib/`, AARs are unpacked (`classes.jar` -> `<module>-<version>.jar`,
   `R.txt`/`AndroidManifest.xml` -> `.cache/lib/aar-meta/`).  On a normal developer
   machine this step gets everything and the fallbacks below never run.
3. **Fallbacks** for whatever step 2 could not fetch (here: Google Maven answers every
   artifact request with a redirect to the blocked `dl.google.com`, JitPack is blocked):
   * `androidx.*` and `com.google.android.material` -> AOSP `platform/prebuilts/sdk`
     snapshot, pinned to commit `3af7c935` of the GitHub mirror
     `msft-mirror-aosp/platform.prebuilts.sdk` (blob-less sparse fetch of 50 files:
     `current/androidx/m2repository/...` and `current/extras/material-design-x/...`).
     These are **newer** than the versions declared in `app/build.gradle`
     (e.g. appcompat 1.8.0-alpha01 for 1.7.1, material 1.13.0-alpha08 for 1.13.0,
     preference 1.3.0-alpha01 for 1.2.1).
   * `com.google.ai.edge.litert:litert` / `litert-gpu` -> the Java API
     (`org.tensorflow.lite.*`, gpu and nnapi delegates) compiled with javac from
     `google-ai-edge/LiteRT` at tag `v<version>` (no native libraries).
   * `com.github.cgutman:ShieldControllerExtensions` -> compiled with javac from
     `cgutman/ShieldControllerExtensions` at tag `<version>` (plain Java, no AIDL).
   * `com.github.ByteHamster:SearchPreference`, `com.github.PhilJay:MPAndroidChart` ->
     skipped as long as no Java source under `app/src` imports them (they are used
     from XML/Gradle only); if a source starts importing them, setup fails until
     JitPack is reachable.
4. Compiles `RGen.java` and writes `.cache/lib/DEPENDENCIES.txt` (coordinates, route,
   file) and `.cache/.setup-ok`.  The route taken for every dependency is printed at
   the end (`google-maven`, `maven-central`, `jitpack`, `aosp-prebuilts`,
   `source-build`, `unused`).  If anything is still missing, setup exits 1 and
   lists it.

Options: `--force` redoes every step; `--no-google` / `--no-jitpack` skip those
repositories (faster where they are known to be blocked); `--worktree <repo>`
reads a different `app/build.gradle`; `--gradle <path>`.
`COMPILECHECK_SEED=<other checkout>/tools/compilecheck/.cache` copies an existing
cache first so nothing already present is downloaded again.

## How compilecheck.sh compiles

* `gradlecfg.py` pattern-matches `app/build.gradle` for namespace, versionName/Code,
  product flavors, `applicationId` (+ release `applicationIdSuffix`), every
  `buildConfigField` and `resValue "string"` and writes one stand-in
  `BuildConfig.java` per flavor (`DEBUG = false`, `BUILD_TYPE = "release"`).
* `RGen.java` generates `R.java` from `app/src/main/res` (+ `src/<flavor>/res`,
  `src/release/res`): every `name=` in `values*/` XML by element type (string,
  string-array/integer-array/array, plurals, color, dimen, bool, integer, fraction,
  style, attr, declare-styleable with `<Styleable>_<attr>` indices, `item type=`),
  file-based resources by directory type, `@+id/` and `@id/` references, and the
  `resValue` strings.  Dots and dashes become underscores; values are arbitrary but
  unique.  Library R classes that the sources reference explicitly
  (`import androidx.appcompat.R;`) are generated from the matching AAR's `R.txt`.
* `javac -nowarn -Xlint:none -proc:none -source 11 -target 11 -encoding UTF-8
  -cp .cache/sdk/android-all-*.jar:.cache/lib/*.jar`.

## What it does NOT check

* **Resources, manifest, lint, packaging**: no aapt2, so a missing drawable, a broken
  layout, a manifest error, lint, ProGuard/R8 and the APK/AAB are all out of scope.
  `R` is a *superset* (every `@id/` reference and every styleable attr is declared
  even if nothing defines it), and its values are fake.
* **Kotlin, native code, annotation processors, tests**: none are compiled or run.
* **Hidden APIs**: android-all is the real framework *implementation*, not the SDK
  stub jar, so `@hide`/internal framework members resolve here although the real
  build would reject them.  `java.*` resolves against the JDK's class library, so
  JDK-only APIs also pass.
* **Versions**: with the AOSP fallback, AndroidX/Material are newer than the declared
  versions (an API that only exists in the newer snapshot would compile here but
  not in the real build); LiteRT and ShieldControllerExtensions are the declared
  versions but built from source (Java API only).
* Only the `release` build type is modelled (`DEBUG` is always `false`,
  debug-only `buildConfigField`s are ignored); non-transitive R classes (the AGP 8
  default) are assumed.

## Adding or fixing a dependency

* Add it to `app/build.gradle` as usual and re-run `setup.sh`: Maven Central and
  (where reachable) Google Maven/JitPack artifacts need nothing else.
* If it is Google-Maven-only and Google Maven is unreachable, add its AAR/jar path to
  `aosp_artifacts()` in `setup.sh` (anything that exists in AOSP's
  `prebuilts/sdk/current/androidx/m2repository` or `current/extras`), or add a
  source-build fallback next to the LiteRT one.
* A library that is used only from XML/Gradle can be listed as "unused" the way
  SearchPreference is.
* `.cache/lib/DEPENDENCIES.txt` shows where every jar on the classpath came from;
  `setup.sh --force` rebuilds the cache from scratch.
