#!/bin/bash
# compilecheck.sh -- javac-only compile check for the moonlight-android app.
#
#   tools/compilecheck/compilecheck.sh [<worktree>] [--no-flavors] [--keep]
#
# <worktree> defaults to the repository that contains this script.  Run setup.sh
# once before (it fills .cache/ next to this script; nothing is fetched here).
#
# Compiles every Java file under <worktree>/app/src/main/java (pass "main") and,
# for every product flavor that has its own <worktree>/app/src/<flavor>/java,
# a second pass with main + flavor sources (for this app: "root").  Exits
# non-zero with javac's error output if any pass fails.  The worktree is never
# modified: generated sources (R.java, BuildConfig.java), source lists, logs and
# class files go to .cache/work/<worktree-basename>/.
#
# Exit codes: 0 every pass compiled; 1 javac reported errors; 2 bad arguments /
# not an app worktree; 3 setup incomplete (run setup.sh) or a tool is missing.
#
# This is a smoke test, not the Gradle build: no resources/manifest/lint, no
# Kotlin, no native code, and Robolectric's android-all jar stands in for
# android.jar.  See README.md.
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CACHE="$HERE/.cache"
LIB="$CACHE/lib"
TOOLS="$CACHE/tools"

usage() { echo "usage: $0 [<worktree>] [--no-flavors] [--keep]" >&2; exit 2; }

WT=""; NO_FLAVORS=0; KEEP=0
for a in "$@"; do
  case "$a" in
    --no-flavors) NO_FLAVORS=1 ;;
    --keep) KEEP=1 ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $a" >&2; usage ;;
    *) [ -z "$WT" ] && WT="$a" || usage ;;
  esac
done
if [ -z "$WT" ]; then
  WT=$(cd "$HERE/../.." && pwd)
fi
WT=$(cd "$WT" 2>/dev/null && pwd) || { echo "error: no such directory: $WT" >&2; exit 2; }
APP="$WT/app"
[ -d "$APP/src/main/java" ] || { echo "error: $APP/src/main/java not found (not an app worktree?)" >&2; exit 2; }

command -v javac >/dev/null || { echo "error: javac not on PATH" >&2; exit 3; }
command -v java >/dev/null || { echo "error: java not on PATH" >&2; exit 3; }
command -v python3 >/dev/null || { echo "error: python3 not on PATH (needed by gradlecfg.py)" >&2; exit 3; }
SDK_JAR=$(ls "$CACHE"/sdk/android-all-*.jar 2>/dev/null | head -1)
[ -n "$SDK_JAR" ] || { echo "error: no android-all jar in $CACHE/sdk -- run $HERE/setup.sh first" >&2; exit 3; }
[ "$(ls "$LIB"/*.jar 2>/dev/null | wc -l)" -gt 0 ] || { echo "error: $LIB holds no jars -- run $HERE/setup.sh first" >&2; exit 3; }
[ -f "$CACHE/.setup-ok" ] || echo "compilecheck: WARNING: $CACHE/.setup-ok missing -- setup.sh did not finish cleanly; results may be incomplete"

# JVMs print "Picked up JAVA_TOOL_OPTIONS" on stderr when that variable is set; drop it.
filter_noise() { grep -v "^Picked up JAVA_TOOL_OPTIONS" || true; }

# RGen (R.java generator) is compiled on demand into .cache/tools.
if [ ! -f "$TOOLS/RGen.class" ] || [ "$HERE/RGen.java" -nt "$TOOLS/RGen.class" ]; then
  mkdir -p "$TOOLS"
  javac -nowarn -d "$TOOLS" "$HERE/RGen.java" 2>&1 | filter_noise
  [ -f "$TOOLS/RGen.class" ] || { echo "error: could not compile $HERE/RGen.java" >&2; exit 3; }
fi

NAME=$(basename "$WT")
WORK="$CACHE/work/$NAME"
mkdir -p "$CACHE/work"
if command -v flock >/dev/null; then
  exec 9>"$CACHE/work/.$NAME.lock"
  flock -w 900 9 || { echo "error: another compilecheck run on $NAME is still holding the lock" >&2; exit 3; }
fi
rm -rf "$WORK"
mkdir -p "$WORK/gen" "$WORK/out"

CP="$SDK_JAR"
for j in $(ls "$LIB"/*.jar | LC_ALL=C sort); do CP="$CP:$j"; done

echo "compilecheck: worktree $WT"
echo "compilecheck: work dir $WORK"
START=$(date +%s)

# ---- build.gradle -> namespace, flavors, BuildConfig.java per flavor -------------------
CFG=$(python3 "$HERE/gradlecfg.py" "$APP/build.gradle" "$WORK/gen/buildconfig") || { echo "error: could not parse $APP/build.gradle" >&2; exit 3; }
eval "$CFG"
echo "compilecheck: namespace=$NAMESPACE version=$VERSION_NAME ($VERSION_CODE) flavors=[$FLAVORS]"

# ---- pass planning ----------------------------------------------------------------------
# main pass: main/java (+ src/release/java) with the BuildConfig of the first flavor that
# has NO java dir of its own; one extra pass per flavor that has src/<flavor>/java.
EXTRA_DIRS=()
[ -d "$APP/src/release/java" ] && EXTRA_DIRS+=("$APP/src/release/java")
MAIN_FLAVOR=""
FLAVOR_PASSES=()
for f in $FLAVORS; do
  if [ -d "$APP/src/$f/java" ]; then
    FLAVOR_PASSES+=("$f")
  elif [ -z "$MAIN_FLAVOR" ]; then
    MAIN_FLAVOR="$f"
  fi
done
[ -z "$MAIN_FLAVOR" ] && MAIN_FLAVOR=$(echo $FLAVORS | awk '{print $1}')
[ "$NO_FLAVORS" = 1 ] && FLAVOR_PASSES=()

# ---- library R classes the sources reference explicitly (androidx.appcompat.R, ...) -----
gen_library_r() {   # <gen-dir> <srcdir>...
  local gen="$1"; shift
  local pkgs
  pkgs=$( { grep -rhoE "^import [a-z][A-Za-z0-9_.]*\.R;" "$@" 2>/dev/null | sed -E 's/^import //; s/\.R;$//';
            grep -rhoE "\b[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+\.R\.[a-z]" "$@" 2>/dev/null | sed -E 's/\.R\.[a-z]$//'; } \
          | LC_ALL=C sort -u | grep -vE "^(android|$NAMESPACE)$" || true)
  local p meta
  for p in $pkgs; do
    meta=$(grep -l "package=\"$p\"" "$LIB"/aar-meta/*/AndroidManifest.xml 2>/dev/null | head -1)
    if [ -n "$meta" ] && [ -f "$(dirname "$meta")/R.txt" ]; then
      java -cp "$TOOLS" RGen rtxt "$gen" "$p" "$(dirname "$meta")/R.txt" 2>&1 | filter_noise
      echo "compilecheck: generated $p.R from $(basename "$(dirname "$meta")")/R.txt"
    else
      echo "compilecheck: WARNING: sources reference $p.R but no AAR with that package is in lib/aar-meta -- expect 'package $p does not exist'"
    fi
  done
}

FAILED=0
PASSES_RUN=0

run_pass() {   # <pass-name> <flavor-for-BuildConfig> <srcdir>...
  local pass="$1" flavor="$2"; shift 2
  local srcdirs=("$@")
  local gen="$WORK/gen/$pass" out="$WORK/out/$pass" list="$WORK/sources-$pass.txt" log="$WORK/javac-$pass.log"
  mkdir -p "$gen" "$out"
  local t0; t0=$(date +%s)

  # R.java from the app's resources (main + flavor + release res dirs, resValue strings).
  local resargs=(--res "$APP/src/main/res")
  [ "$flavor" != default ] && [ -d "$APP/src/$flavor/res" ] && resargs+=(--res "$APP/src/$flavor/res")
  [ -d "$APP/src/release/res" ] && resargs+=(--res "$APP/src/release/res")
  local s; for s in $RES_STRINGS; do resargs+=(--string "$s"); done
  java -cp "$TOOLS" RGen app "$gen" "$NAMESPACE" "${resargs[@]}" 2>&1 | filter_noise | sed 's/^/compilecheck: /'
  cp -r "$WORK/gen/buildconfig/$flavor/." "$gen/"
  gen_library_r "$gen" "${srcdirs[@]}"

  { find "${srcdirs[@]}" -type f -name '*.java'; find "$gen" -type f -name '*.java'; } | LC_ALL=C sort > "$list"
  local n; n=$(wc -l < "$list")
  echo "compilecheck: [$pass] javac: $n sources (BuildConfig flavor=$flavor; dirs: ${srcdirs[*]#$WT/})"
  javac -nowarn -Xlint:none -proc:none -source 11 -target 11 -encoding UTF-8 -Xmaxerrs 1000 \
        -cp "$CP" -d "$out" @"$list" > "$log" 2>&1
  local rc=$?
  filter_noise < "$log" > "$log.clean"; mv "$log.clean" "$log"
  local t1; t1=$(date +%s)
  PASSES_RUN=$((PASSES_RUN + 1))
  if [ $rc -eq 0 ]; then
    echo "compilecheck: [$pass] OK ($((t1 - t0))s, $(find "$out" -name '*.class' | wc -l) class files)"
  else
    FAILED=1
    echo "compilecheck: [$pass] FAILED (javac exit $rc, $((t1 - t0))s) -- javac output:"
    cat "$log"
  fi
}

run_pass main "$MAIN_FLAVOR" "$APP/src/main/java" "${EXTRA_DIRS[@]}"
for f in "${FLAVOR_PASSES[@]}"; do
  run_pass "$f" "$f" "$APP/src/main/java" "$APP/src/$f/java" "${EXTRA_DIRS[@]}"
done

END=$(date +%s)
if [ "$KEEP" = 0 ]; then rm -rf "$WORK/out"; fi
echo "compilecheck: done, $PASSES_RUN pass(es) in $((END - START))s; sources+logs in $WORK (class files removed unless --keep)"
echo "compilecheck: note: javac-only check -- no resource/manifest validation, no lint, no Kotlin, android-all stands in for android.jar (see README.md)"
if [ "$FAILED" != 0 ]; then echo "compilecheck: RESULT: FAIL"; exit 1; fi
echo "compilecheck: RESULT: PASS"
exit 0
