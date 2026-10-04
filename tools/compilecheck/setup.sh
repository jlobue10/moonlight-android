#!/bin/bash
# setup.sh -- one-time, idempotent preparation for tools/compilecheck/compilecheck.sh.
#
#   tools/compilecheck/setup.sh [--worktree <repo>] [--gradle <path>] [--force] [--no-google] [--no-jitpack]
#
# Fills .cache/ next to this script (git-ignored):
#   .cache/sdk/android-all-<ver>.jar  Robolectric's android-all jar (Android framework classes), sha1-verified
#   .cache/lib/*.jar                  every compile dependency; AAR R.txt/AndroidManifest.xml under lib/aar-meta/
#   .cache/lib/DEPENDENCIES.txt       coordinates -> route -> file(s) (see routes below)
#   .cache/tools/RGen.class           the R.java generator
#   .cache/src/                       git checkouts used by the fallback routes
#   .cache/.setup-ok                  written only when every dependency was obtained
#
# Routes, tried in this order (the Gradle project in deps/ resolves leniently, so
# whatever Google Maven / Maven Central / JitPack can serve is used as-is):
#   1. Google Maven, Maven Central, JitPack      -- the normal developer-machine path
#   2. androidx.* / com.google.android.material  -> AOSP platform/prebuilts/sdk snapshot (pinned commit, GitHub mirror)
#   3. com.google.ai.edge.litert:*               -> LiteRT Java API compiled from google-ai-edge/LiteRT tag v<version>
#   4. com.github.cgutman:ShieldControllerExtensions -> compiled from cgutman/ShieldControllerExtensions tag <version>
#   5. com.github.ByteHamster:SearchPreference, com.github.PhilJay:MPAndroidChart
#                                                -> skipped when no Java source under app/src imports them
# Anything still missing afterwards makes setup fail (exit 1) with a list of what is missing.
#
# The dependency coordinates are read from <repo>/app/build.gradle (gradlecfg.py --deps),
# so version bumps there are picked up automatically; routes 2-4 are re-run when the
# resolver could not fetch the Google-Maven / JitPack artifacts.
#
# COMPILECHECK_SEED=<another checkout's .cache dir> copies that cache first; already
# present (and valid) items are then skipped.
#
# Exit codes: 0 ready; 1 a dependency could not be obtained; 2 bad arguments; 3 missing tool.
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CACHE="$HERE/.cache"
LIB="$CACHE/lib"
SRC="$CACHE/src"

ANDROID_ALL_VERSION=17-robolectric-15733970          # Android 17 / API 37 framework (matches compileSdk 37)
ANDROID_ALL_SHA1=c4074fdab740a7ddbb7e6a6a4960810d91ec30f9
MAVEN_CENTRAL=https://repo1.maven.org/maven2
AOSP_REPO=https://github.com/msft-mirror-aosp/platform.prebuilts.sdk   # mirror of AOSP platform/prebuilts/sdk
AOSP_COMMIT=3af7c93524be6f51e092b87b17f009f13ee98b43                  # 2025-03-14, androidx snapshot of early 2025
AOSP_M2=current/androidx/m2repository
AOSP_MATERIAL=current/extras/material-design-x
LITERT_REPO=https://github.com/google-ai-edge/LiteRT
LITERT_KNOWN_1_4_0=0348ffbe4232df35ab2651e6383528b3d8bf792f           # what tag v1.4.0 pointed at when this was written
SCE_REPO=https://github.com/cgutman/ShieldControllerExtensions
SCE_KNOWN_1_0_1=48356a2263839949fdfdf0795b3d8cc38edeef9b              # what tag 1.0.1 pointed at when this was written
# Needed by the fallback routes (LiteRT sources use checker-qual; androidx.concurrent needs ListenableFuture).
EXTRA_DEPS="org.checkerframework:checker-qual:4.3.0 com.google.guava:listenablefuture:1.0"

# The AndroidX closure for appcompat/preference/recyclerview/cardview/material as present
# in the pinned AOSP snapshot (paths below $AOSP_M2, or EXTRAS: for $AOSP_MATERIAL).
aosp_artifacts() {
cat <<'LIST'
EXTRAS:com/google/android/material/material/1.13.0-alpha08/material-1.13.0-alpha08.aar
androidx/activity/activity/1.11.0-alpha01/activity-1.11.0-alpha01.aar
androidx/annotation/annotation-experimental/1.5.0-alpha01/annotation-experimental-1.5.0-alpha01.aar
androidx/annotation/annotation-jvm/1.9.0-rc01/annotation-jvm-1.9.0-rc01.jar
androidx/appcompat/appcompat-resources/1.8.0-alpha01/appcompat-resources-1.8.0-alpha01.aar
androidx/appcompat/appcompat/1.8.0-alpha01/appcompat-1.8.0-alpha01.aar
androidx/arch/core/core-common/2.3.0-alpha01/core-common-2.3.0-alpha01.jar
androidx/arch/core/core-runtime/2.3.0-alpha01/core-runtime-2.3.0-alpha01.aar
androidx/cardview/cardview/1.1.0-alpha01/cardview-1.1.0-alpha01.aar
androidx/collection/collection-jvm/1.5.0-beta03/collection-jvm-1.5.0-beta03.jar
androidx/collection/collection-ktx/1.5.0-beta03/collection-ktx-1.5.0-beta03.jar
androidx/concurrent/concurrent-futures/1.3.0-alpha01/concurrent-futures-1.3.0-alpha01.jar
androidx/coordinatorlayout/coordinatorlayout/1.3.0-alpha02/coordinatorlayout-1.3.0-alpha02.aar
androidx/core/core-ktx/1.16.0-beta01/core-ktx-1.16.0-beta01.aar
androidx/core/core-viewtree/1.1.0-alpha01/core-viewtree-1.1.0-alpha01.aar
androidx/core/core/1.16.0-beta01/core-1.16.0-beta01.aar
androidx/cursoradapter/cursoradapter/1.1.0-alpha01/cursoradapter-1.1.0-alpha01.aar
androidx/customview/customview-poolingcontainer/1.1.0-alpha01/customview-poolingcontainer-1.1.0-alpha01.aar
androidx/customview/customview/1.2.0-alpha03/customview-1.2.0-alpha03.aar
androidx/drawerlayout/drawerlayout/1.3.0-alpha01/drawerlayout-1.3.0-alpha01.aar
androidx/dynamicanimation/dynamicanimation/1.1.0-alpha04/dynamicanimation-1.1.0-alpha04.aar
androidx/emoji2/emoji2-views-helper/1.5.0-rc01/emoji2-views-helper-1.5.0-rc01.aar
androidx/emoji2/emoji2/1.5.0-rc01/emoji2-1.5.0-rc01.aar
androidx/fragment/fragment/1.9.0-alpha01/fragment-1.9.0-alpha01.aar
androidx/graphics/graphics-shapes-android/1.1.0-alpha01/graphics-shapes-android-1.1.0-alpha01.aar
androidx/interpolator/interpolator/1.1.0-alpha01/interpolator-1.1.0-alpha01.aar
androidx/lifecycle/lifecycle-common-java8/2.9.0-alpha11/lifecycle-common-java8-2.9.0-alpha11.jar
androidx/lifecycle/lifecycle-common-jvm/2.9.0-alpha11/lifecycle-common-jvm-2.9.0-alpha11.jar
androidx/lifecycle/lifecycle-livedata-core/2.9.0-alpha11/lifecycle-livedata-core-2.9.0-alpha11.aar
androidx/lifecycle/lifecycle-livedata/2.9.0-alpha11/lifecycle-livedata-2.9.0-alpha11.aar
androidx/lifecycle/lifecycle-process/2.9.0-alpha11/lifecycle-process-2.9.0-alpha11.aar
androidx/lifecycle/lifecycle-runtime-android/2.9.0-alpha11/lifecycle-runtime-android-2.9.0-alpha11.aar
androidx/lifecycle/lifecycle-viewmodel-android/2.9.0-alpha11/lifecycle-viewmodel-android-2.9.0-alpha11.aar
androidx/lifecycle/lifecycle-viewmodel-ktx/2.9.0-alpha11/lifecycle-viewmodel-ktx-2.9.0-alpha11.aar
androidx/lifecycle/lifecycle-viewmodel-savedstate-android/2.9.0-alpha11/lifecycle-viewmodel-savedstate-android-2.9.0-alpha11.aar
androidx/loader/loader/1.2.0-alpha01/loader-1.2.0-alpha01.aar
androidx/preference/preference/1.3.0-alpha01/preference-1.3.0-alpha01.aar
androidx/recyclerview/recyclerview/1.5.0-alpha01/recyclerview-1.5.0-alpha01.aar
androidx/resourceinspection/resourceinspection-annotation/1.1.0-alpha01/resourceinspection-annotation-1.1.0-alpha01.jar
androidx/savedstate/savedstate-android/1.3.0-alpha05/savedstate-android-1.3.0-alpha05.aar
androidx/slidingpanelayout/slidingpanelayout/1.3.0-alpha01/slidingpanelayout-1.3.0-alpha01.aar
androidx/startup/startup-runtime/1.2.0-alpha02/startup-runtime-1.2.0-alpha02.aar
androidx/tracing/tracing-android/1.3.0-beta01/tracing-android-1.3.0-beta01.aar
androidx/transition/transition/1.5.0-rc01/transition-1.5.0-rc01.aar
androidx/vectordrawable/vectordrawable-animated/1.2.0/vectordrawable-animated-1.2.0.aar
androidx/vectordrawable/vectordrawable/1.2.0/vectordrawable-1.2.0.aar
androidx/versionedparcelable/versionedparcelable/1.2.0-rc01/versionedparcelable-1.2.0-rc01.aar
androidx/viewpager/viewpager/1.1.0-alpha02/viewpager-1.1.0-alpha02.aar
androidx/viewpager2/viewpager2/1.2.0-alpha01/viewpager2-1.2.0-alpha01.aar
androidx/window/extensions/core/core/1.1.0-alpha01/core-1.1.0-alpha01.aar
androidx/window/window/1.6.0-alpha01/window-1.6.0-alpha01.aar
LIST
}

log() { echo "setup: $*"; }
die() { echo "setup: ERROR: $*" >&2; exit "${2:-1}"; }
usage() { echo "usage: $0 [--worktree <repo>] [--gradle <path>] [--force] [--no-google] [--no-jitpack]" >&2; exit 2; }
filter_noise() { grep -v "^Picked up JAVA_TOOL_OPTIONS" || true; }

WT=""; GRADLE=""; FORCE=0; USE_GOOGLE=true; USE_JITPACK=true
while [ $# -gt 0 ]; do
  case "$1" in
    --worktree) [ $# -ge 2 ] || usage; WT=$2; shift 2 ;;
    --gradle) [ $# -ge 2 ] || usage; GRADLE=$2; shift 2 ;;
    --force) FORCE=1; shift ;;
    --no-google) USE_GOOGLE=false; shift ;;
    --no-jitpack) USE_JITPACK=false; shift ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done
[ -z "$WT" ] && WT="$HERE/../.."
WT=$(cd "$WT" 2>/dev/null && pwd) || die "no such directory: $WT" 2
BUILD_GRADLE="$WT/app/build.gradle"
[ -f "$BUILD_GRADLE" ] || die "$BUILD_GRADLE not found (use --worktree <repo>)" 2

for t in java javac git curl unzip python3; do command -v "$t" >/dev/null || die "$t not found on PATH" 3; done
if [ -z "$GRADLE" ]; then
  if command -v gradle >/dev/null; then GRADLE=gradle
  elif [ -x "$WT/gradlew" ]; then GRADLE="$WT/gradlew"; log "no gradle on PATH, using $WT/gradlew (downloads Gradle on first use)"
  else die "no gradle on PATH and no gradlew in $WT (use --gradle <path>)" 3; fi
fi
command -v sha1sum >/dev/null || command -v shasum >/dev/null || die "neither sha1sum nor shasum found" 3
sha1_of() { if command -v sha1sum >/dev/null; then sha1sum "$1" | awk '{print $1}'; else shasum -a 1 "$1" | awk '{print $1}'; fi; }

mkdir -p "$CACHE/sdk" "$LIB/aar-meta" "$CACHE/tools" "$SRC" "$CACHE/build" || die "cannot create $CACHE"
rm -f "$CACHE/.setup-ok"
export GIT_TERMINAL_PROMPT=0

# ---- optional seed from another checkout's cache ------------------------------------------
if [ -n "${COMPILECHECK_SEED:-}" ]; then
  [ -d "$COMPILECHECK_SEED" ] || die "COMPILECHECK_SEED=$COMPILECHECK_SEED is not a directory" 2
  log "seeding from $COMPILECHECK_SEED"
  for d in sdk lib src tools; do
    [ -d "$COMPILECHECK_SEED/$d" ] && cp -R "$COMPILECHECK_SEED/$d/." "$CACHE/$d/"
  done
fi

# ---- bookkeeping: lib/.resolved-gradle.txt (written by deps/) + lib/.resolved-fallback.txt ----
record_fallback() {   # <coords> <route> <files> <detail>
  local f="$LIB/.resolved-fallback.txt"
  touch "$f"
  grep -v "^$1	" "$f" > "$f.tmp" 2>/dev/null; mv "$f.tmp" "$f"
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$f"
}

import_artifact() {   # <file.aar|file.jar> <group> <module> <version> <route> <detail>
  local f=$1 g=$2 m=$3 v=$4 route=$5 detail=$6 out="" e
  case "$f" in
    *.aar)
      local meta="$LIB/aar-meta/${g}__${m}-${v}"
      mkdir -p "$meta"
      if unzip -l "$f" classes.jar >/dev/null 2>&1; then
        unzip -p "$f" classes.jar > "$LIB/$m-$v.jar" || die "cannot extract classes.jar from $f"; out="$m-$v.jar"
      fi
      for e in $(unzip -Z1 "$f" | grep -E '^libs/.*\.jar$'); do
        unzip -p "$f" "$e" > "$LIB/$m-$v-libs-$(basename "$e")"; out="$out,$m-$v-libs-$(basename "$e")"
      done
      for e in R.txt AndroidManifest.xml api.jar; do
        unzip -l "$f" "$e" >/dev/null 2>&1 && unzip -p "$f" "$e" > "$meta/$e"
      done
      [ -f "$meta/api.jar" ] && out="$out,aar-meta/$(basename "$meta")/api.jar(API-JAR-PRESENT)"
      ;;
    *.jar) cp "$f" "$LIB/$m-$v.jar" || die "cannot copy $f"; out="$m-$v.jar" ;;
    *) die "import_artifact: unknown artifact type: $f" ;;
  esac
  record_fallback "$g:$m:$v" "$route" "$out" "$detail"
}

git_fetch() {   # <dir> <url> <commit-or-ref>  -> FETCH_HEAD, blob-less + shallow (small)
  local dir=$1 url=$2 ref=$3
  if [ ! -d "$dir/.git" ]; then
    mkdir -p "$dir" && git -C "$dir" init -q && git -C "$dir" remote add origin "$url" || die "cannot init $dir"
  fi
  git -C "$dir" -c gc.auto=0 fetch -q --filter=blob:none --depth 1 origin "$ref" || die "could not fetch $ref from $url"
  git -C "$dir" rev-parse FETCH_HEAD
}

javac_jar() {   # <name> <classpath> <source-list-file> -> lib/<name>.jar
  local name=$1 cp=$2 list=$3
  local build="$CACHE/build/$name"
  rm -rf "$build" && mkdir -p "$build/classes"
  if ! javac -nowarn -Xlint:none -proc:none -source 11 -target 11 -encoding UTF-8 -cp "$cp" -d "$build/classes" @"$list" > "$build/javac.log" 2>&1; then
    filter_noise < "$build/javac.log" | grep -v "^Note:" | head -40 >&2
    die "javac failed for $name (full log: $build/javac.log)"
  fi
  (cd "$build/classes" && jar cf "$LIB/$name.jar" .) 2>&1 | filter_noise
  [ -f "$LIB/$name.jar" ] || die "jar failed for $name"
}

# ---- 1. android-all (framework classes) ---------------------------------------------------
JAR="$CACHE/sdk/android-all-$ANDROID_ALL_VERSION.jar"
if [ "$FORCE" = 0 ] && [ -f "$JAR" ] && [ "$(sha1_of "$JAR")" = "$ANDROID_ALL_SHA1" ]; then
  log "android-all $ANDROID_ALL_VERSION: present, sha1 ok"
else
  URL="$MAVEN_CENTRAL/org/robolectric/android-all/$ANDROID_ALL_VERSION/android-all-$ANDROID_ALL_VERSION.jar"
  log "android-all $ANDROID_ALL_VERSION: downloading from Maven Central (~190 MB)"
  rm -f "$CACHE"/sdk/android-all-*.jar
  curl -fsSL --retry 3 --connect-timeout 30 -o "$JAR.part" "$URL" || die "download failed: $URL"
  [ "$(sha1_of "$JAR.part")" = "$ANDROID_ALL_SHA1" ] || die "sha1 mismatch for $URL (expected $ANDROID_ALL_SHA1)"
  mv "$JAR.part" "$JAR"
  log "android-all $ANDROID_ALL_VERSION: downloaded, sha1 ok (route: maven-central)"
fi

# ---- 2. dependencies through the Gradle resolver (Google Maven / Maven Central / JitPack) --
DEPS=$(python3 "$HERE/gradlecfg.py" --deps "$BUILD_GRADLE") || die "could not read dependencies from $BUILD_GRADLE"
[ -n "$DEPS" ] || die "no implementation dependencies found in $BUILD_GRADLE"
DEPS_ALL="$(echo $DEPS) $EXTRA_DEPS"
DEPS_HASH=$(printf '%s\n%s\n%s\n' "$DEPS_ALL" "$USE_GOOGLE/$USE_JITPACK" "$(cat "$HERE/deps/build.gradle")" | { if command -v sha1sum >/dev/null; then sha1sum; else shasum -a 1; fi; } | awk '{print $1}')
gradle_outputs_present() {
  [ -f "$LIB/.resolved-gradle.txt" ] || return 1
  local line out o
  while IFS=$'\t' read -r _ _ _ out _; do
    for o in $(echo "$out" | tr ',' ' '); do
      case "$o" in *API-JAR-PRESENT*|SKIPPED) ;; *) [ -f "$LIB/$o" ] || return 1 ;; esac
    done
  done < "$LIB/.resolved-gradle.txt"
  return 0
}
if [ "$FORCE" = 0 ] && [ -f "$LIB/.deps-hash" ] && [ "$(cat "$LIB/.deps-hash")" = "$DEPS_HASH" ] && gradle_outputs_present; then
  log "gradle resolver: dependency list unchanged, reusing $LIB (--force to redo)"
else
  if [ -f "$LIB/.resolved-gradle.txt" ]; then   # drop artifacts of the previous resolution (version bumps)
    while IFS=$'\t' read -r _ _ _ out _; do
      for o in $(echo "$out" | tr ',' ' '); do case "$o" in *.jar) rm -f "$LIB/$o" ;; esac; done
    done < "$LIB/.resolved-gradle.txt"
    rm -f "$LIB/.resolved-gradle.txt" "$LIB/.failed-gradle.txt" "$LIB/.deps-hash"
  fi
  log "gradle resolver: $GRADLE (google=$USE_GOOGLE, jitpack=$USE_JITPACK), resolving: $DEPS_ALL"
  if ! "$GRADLE" --no-daemon --console=plain -q -p "$HERE/deps" copyDeps \
        "-PcompileDeps=$DEPS_ALL" "-PlibDir=$LIB" "-PuseGoogle=$USE_GOOGLE" "-PuseJitpack=$USE_JITPACK" \
        > "$CACHE/gradle-resolve.log" 2>&1; then
    filter_noise < "$CACHE/gradle-resolve.log" | tail -40 >&2
    die "the Gradle resolver failed (log: $CACHE/gradle-resolve.log)"
  fi
  [ -f "$LIB/.resolved-gradle.txt" ] || die "the Gradle resolver produced no $LIB/.resolved-gradle.txt (log: $CACHE/gradle-resolve.log)"
  echo "$DEPS_HASH" > "$LIB/.deps-hash"
  log "gradle resolver: $(wc -l < "$LIB/.resolved-gradle.txt" | tr -d ' ') artifact(s) copied, $(wc -l < "$LIB/.failed-gradle.txt" | tr -d ' ') unresolved"
fi

# ---- 3. fallbacks for whatever the resolver could not fetch ----------------------------------
NEED_AOSP=0; LITERT_VERSION=""; SCE_VERSION=""; MISSING=""
java_uses() { grep -rqE "^import $1" "$WT/app/src" --include='*.java' 2>/dev/null; }
while IFS=$'\t' read -r coord msg; do
  [ -n "$coord" ] || continue
  g=${coord%%:*}; rest=${coord#*:}; a=${rest%%:*}; v=${rest#*:}
  case "$g" in
    androidx.*|com.google.android.material) NEED_AOSP=1 ;;
    com.google.ai.edge.litert) LITERT_VERSION=$v ;;
    com.github.cgutman) if [ "$a" = ShieldControllerExtensions ]; then SCE_VERSION=$v; else MISSING="$MISSING $coord"; fi ;;
    com.github.ByteHamster)
      if java_uses "com\.bytehamster\."; then MISSING="$MISSING $coord"; else record_fallback "$coord" "unused" "-" "not resolvable here and no Java source imports com.bytehamster.* (used from XML/Gradle only)"; fi ;;
    com.github.PhilJay)
      if java_uses "com\.github\.mikephil\."; then MISSING="$MISSING $coord"; else record_fallback "$coord" "unused" "-" "not resolvable here and no Java source imports com.github.mikephil.*"; fi ;;
    org.checkerframework|com.google.guava) MISSING="$MISSING $coord" ;;
    *) MISSING="$MISSING $coord" ;;
  esac
  log "unresolved by the Gradle resolver: $coord -- ${msg:0:160}"
done < "$LIB/.failed-gradle.txt"

# 3a. AndroidX / Material from the pinned AOSP prebuilts snapshot
if [ "$NEED_AOSP" = 1 ]; then
  MARK="$LIB/.aosp-$AOSP_COMMIT"
  if [ "$FORCE" = 0 ] && [ -f "$MARK" ]; then
    log "androidx/material: AOSP prebuilts ${AOSP_COMMIT:0:8} already imported"
  else
    log "androidx/material: fetching AOSP platform/prebuilts/sdk @ ${AOSP_COMMIT:0:8} via $AOSP_REPO (blob-less, 50 files)"
    AOSP_DIR="$SRC/platform.prebuilts.sdk"
    GOT=$(git_fetch "$AOSP_DIR" "$AOSP_REPO" "$AOSP_COMMIT") || exit 1
    [ "$GOT" = "$AOSP_COMMIT" ] || die "fetched $GOT instead of the pinned $AOSP_COMMIT"
    PATHS=$(aosp_artifacts | sed -e "s|^EXTRAS:|$AOSP_MATERIAL/|" -e "s|^androidx/|$AOSP_M2/androidx/|")
    git -C "$AOSP_DIR" checkout -q "$AOSP_COMMIT" -- $PATHS || die "checkout of the AOSP artifacts failed"
    n=0
    for p in $PATHS; do
      rel=${p#$AOSP_M2/}; rel=${rel#$AOSP_MATERIAL/}
      ver=$(basename "$(dirname "$p")"); mod=$(basename "$(dirname "$(dirname "$p")")")
      grp=$(dirname "$(dirname "$(dirname "$rel")")" | tr '/' '.')
      import_artifact "$AOSP_DIR/$p" "$grp" "$mod" "$ver" "aosp-prebuilts" "AOSP platform/prebuilts/sdk ${AOSP_COMMIT:0:8}: $p"
      n=$((n + 1))
    done
    touch "$MARK"
    log "androidx/material: imported $n artifacts from the AOSP snapshot (route: aosp-prebuilts; versions are the snapshot's, newer than app/build.gradle's)"
  fi
  while IFS=$'\t' read -r coord _; do
    case "${coord%%:*}" in androidx.*|com.google.android.material) ;; *) continue ;; esac
    rest=${coord#*:}; a=${rest%%:*}
    have=$(ls "$LIB"/"$a"-[0-9]*.jar 2>/dev/null | grep -vE -- "-libs-" | head -1)
    [ -z "$have" ] && [ "$a" = annotation ] && have=$(ls "$LIB"/annotation-jvm-*.jar 2>/dev/null | head -1)
    if [ -n "$have" ]; then record_fallback "$coord" "aosp-prebuilts" "$(basename "$have")" "declared version replaced by the AOSP snapshot's"
    else MISSING="$MISSING $coord"; fi
  done < "$LIB/.failed-gradle.txt"
fi

CP_ALL() { local cp="$JAR" j; for j in $(ls "$LIB"/*.jar | LC_ALL=C sort); do cp="$cp:$j"; done; echo "$cp"; }

# 3b. LiteRT Java API from source (tag v<version>)
if [ -n "$LITERT_VERSION" ]; then
  if [ "$FORCE" = 0 ] && [ -f "$LIB/litert-$LITERT_VERSION.jar" ]; then
    log "litert $LITERT_VERSION: already built from source"
  else
    log "litert $LITERT_VERSION: compiling the Java API from $LITERT_REPO tag v$LITERT_VERSION"
    LDIR="$SRC/LiteRT"
    GOT=$(git_fetch "$LDIR" "$LITERT_REPO" "refs/tags/v$LITERT_VERSION") || exit 1
    if [ "$LITERT_VERSION" = 1.4.0 ] && [ "$GOT" != "$LITERT_KNOWN_1_4_0" ]; then die "LiteRT tag v1.4.0 now points at $GOT, not the pinned $LITERT_KNOWN_1_4_0"; fi
    LITERT_DIRS="tflite/java/src/main/java tflite/delegates/gpu/java/src/main/java tflite/delegates/nnapi/java/src/main/java"
    git -C "$LDIR" checkout -q FETCH_HEAD -- $LITERT_DIRS || die "checkout of the LiteRT Java sources failed"
    rm -f "$LIB"/litert-*.jar
    (cd "$LDIR" && find $LITERT_DIRS -name '*.java' | LC_ALL=C sort | sed "s|^|$LDIR/|") > "$CACHE/build/litert-sources.txt"
    javac_jar "litert-$LITERT_VERSION" "$(CP_ALL)" "$CACHE/build/litert-sources.txt"
    log "litert $LITERT_VERSION: built $(wc -l < "$CACHE/build/litert-sources.txt" | tr -d ' ') sources from commit ${GOT:0:8} (route: source-build; Java API only, no native libraries)"
  fi
  record_fallback "com.google.ai.edge.litert:litert:$LITERT_VERSION" "source-build" "litert-$LITERT_VERSION.jar" "google-ai-edge/LiteRT tag v$LITERT_VERSION, tflite/java + delegates/{gpu,nnapi} Java API compiled with javac (no natives)"
  record_fallback "com.google.ai.edge.litert:litert-gpu:$LITERT_VERSION" "source-build" "litert-$LITERT_VERSION.jar" "org.tensorflow.lite.gpu.* is in the same jar"
fi

# 3c. ShieldControllerExtensions from source (tag <version>)
if [ -n "$SCE_VERSION" ]; then
  if [ "$FORCE" = 0 ] && [ -f "$LIB/ShieldControllerExtensions-$SCE_VERSION.jar" ]; then
    log "ShieldControllerExtensions $SCE_VERSION: already built from source"
  else
    log "ShieldControllerExtensions $SCE_VERSION: compiling from $SCE_REPO tag $SCE_VERSION"
    SDIR="$SRC/ShieldControllerExtensions"
    GOT=$(git_fetch "$SDIR" "$SCE_REPO" "refs/tags/$SCE_VERSION") || exit 1
    if [ "$SCE_VERSION" = 1.0.1 ] && [ "$GOT" != "$SCE_KNOWN_1_0_1" ]; then die "ShieldControllerExtensions tag 1.0.1 now points at $GOT, not the pinned $SCE_KNOWN_1_0_1"; fi
    git -C "$SDIR" checkout -q FETCH_HEAD -- ShieldControllerExtensions/src/main/java || die "checkout of the ShieldControllerExtensions sources failed"
    [ -z "$(find "$SDIR/ShieldControllerExtensions/src/main" -name '*.aidl' 2>/dev/null)" ] || die "ShieldControllerExtensions now contains .aidl files; the aidl compiler is not available"
    rm -f "$LIB"/ShieldControllerExtensions-*.jar
    find "$SDIR/ShieldControllerExtensions/src/main/java" -name '*.java' | LC_ALL=C sort > "$CACHE/build/sce-sources.txt"
    javac_jar "ShieldControllerExtensions-$SCE_VERSION" "$JAR" "$CACHE/build/sce-sources.txt"
    log "ShieldControllerExtensions $SCE_VERSION: built $(wc -l < "$CACHE/build/sce-sources.txt" | tr -d ' ') sources from commit ${GOT:0:8} (route: source-build)"
  fi
  record_fallback "com.github.cgutman:ShieldControllerExtensions:$SCE_VERSION" "source-build" "ShieldControllerExtensions-$SCE_VERSION.jar" "cgutman/ShieldControllerExtensions tag $SCE_VERSION compiled with javac (no AIDL in the library)"
fi

# ---- 4. RGen ----------------------------------------------------------------------------------
if [ "$FORCE" = 1 ] || [ ! -f "$CACHE/tools/RGen.class" ] || [ "$HERE/RGen.java" -nt "$CACHE/tools/RGen.class" ]; then
  javac -nowarn -d "$CACHE/tools" "$HERE/RGen.java" 2>&1 | filter_noise
  [ -f "$CACHE/tools/RGen.class" ] || die "could not compile $HERE/RGen.java"
fi

# ---- 5. DEPENDENCIES.txt + summary -------------------------------------------------------------
{
  echo "# coordinates	route	file(s) in lib/	detail   -- generated by tools/compilecheck/setup.sh $(date -u +%Y-%m-%dT%H:%MZ)"
  while IFS=$'\t' read -r coord typ file out repo; do
    case "$repo" in Google) r=google-maven ;; MavenRepo) r=maven-central ;; JitPack) r=jitpack ;; *) r="gradle($repo)" ;; esac
    printf '%s\t%s\t%s\t%s %s\n' "$coord" "$r" "$out" "$typ" "$file"
  done < "$LIB/.resolved-gradle.txt"
  [ -f "$LIB/.resolved-fallback.txt" ] && cat "$LIB/.resolved-fallback.txt"
} | LC_ALL=C sort -u > "$LIB/DEPENDENCIES.txt"

log "routes used for app/build.gradle's dependencies:"
for d in $DEPS; do
  ga=${d%:*}
  line=$(grep "^$d	" "$LIB/DEPENDENCIES.txt" | head -1)
  [ -z "$line" ] && line=$(grep "^$ga:" "$LIB/DEPENDENCIES.txt" | head -1)
  if [ -n "$line" ]; then printf 'setup:   %-55s %-15s %s\n' "$d" "$(echo "$line" | cut -f2)" "$(echo "$line" | cut -f3)"
  else printf 'setup:   %-55s %s\n' "$d" "MISSING"; fi
done
log "$(ls "$LIB"/*.jar | wc -l | tr -d ' ') jars in $LIB; android-all $ANDROID_ALL_VERSION in $CACHE/sdk"
if [ -n "$MISSING" ]; then
  echo "setup: ERROR: these dependencies could not be obtained by any route:$MISSING" >&2
  echo "setup: ERROR: compilecheck.sh will report 'package ... does not exist' for them; see README.md ('Adding or fixing a dependency')" >&2
  exit 1
fi
printf 'deps-hash %s\nandroid-all %s\n%s\n' "$DEPS_HASH" "$ANDROID_ALL_VERSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$CACHE/.setup-ok"
log "ready -- run $HERE/compilecheck.sh"
exit 0
