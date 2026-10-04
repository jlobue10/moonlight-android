#!/usr/bin/env bash
# Creates (or reuses) the APK release keystore and stores it in the GitHub repository as the
# four Actions secrets that .github/workflows/release.yml signs with:
#   KEYSTORE_BASE64, KEYSTORE_PASSWORD, KEY_ALIAS, KEY_PASSWORD
#
# Usage:  tools/release-signing/setup-signing-secrets.sh [keystore-file]
#   keystore-file   default ./release.jks -- created if missing, reused if present
# Environment (optional):
#   REPO=owner/name   target repository (default: this checkout's origin, else jlobue10/moonlight-android)
#   KEY_ALIAS=release
#   KEY_DNAME="CN=moonlight-android fork release key"
#
# Needs: keytool (any JDK 9+), gh (https://cli.github.com, logged in), base64.
# Never commit the keystore or its password. Keep both in a password manager: without them a
# future release cannot be installed over the ones signed with this key.
set -euo pipefail
die() { echo "error: $*" >&2; exit 1; }

KS="${1:-release.jks}"
ALIAS="${KEY_ALIAS:-release}"
DNAME="${KEY_DNAME:-CN=moonlight-android fork release key}"
if [ -z "${REPO:-}" ]; then
  REPO=$(git config --get remote.origin.url 2>/dev/null | sed -E 's#^(https://github.com/|git@github.com:)##; s#\.git$##' || true)
  [ -n "$REPO" ] || REPO=jlobue10/moonlight-android
fi

command -v keytool >/dev/null || die "keytool not found (install a JDK)"
command -v gh >/dev/null || die "gh not found: https://cli.github.com/"
gh auth status >/dev/null 2>&1 || die "gh is not logged in: run 'gh auth login'"
b64() { if base64 --help 2>&1 | grep -q -- '-w'; then base64 -w0 "$1"; else base64 "$1" | tr -d '\n'; fi; }

if [ -f "$KS" ]; then
  echo "Using the existing keystore $KS (alias $ALIAS)"
  read -r -s -p "Keystore password: " PW; echo
  keytool -list -keystore "$KS" -storepass "$PW" -alias "$ALIAS" >/dev/null 2>&1 \
    || die "cannot open $KS with that password, or alias $ALIAS is not in it"
else
  echo "Creating the keystore $KS (alias $ALIAS, RSA 4096, valid 100 years)"
  read -r -s -p "Choose a keystore password (it is also the key password): " PW; echo
  read -r -s -p "Repeat it: " PW2; echo
  [ "$PW" = "$PW2" ] || die "the passwords differ"
  [ ${#PW} -ge 6 ] || die "keytool needs at least 6 characters"
  keytool -genkeypair -v -keystore "$KS" -storetype PKCS12 -storepass "$PW" -keypass "$PW" \
    -alias "$ALIAS" -keyalg RSA -keysize 4096 -validity 36500 -dname "$DNAME"
fi

echo "Storing the secrets in $REPO"
b64 "$KS"           | gh secret set KEYSTORE_BASE64   --repo "$REPO"
printf '%s' "$PW"    | gh secret set KEYSTORE_PASSWORD --repo "$REPO"
printf '%s' "$ALIAS" | gh secret set KEY_ALIAS         --repo "$REPO"
printf '%s' "$PW"    | gh secret set KEY_PASSWORD      --repo "$REPO"
gh secret list --repo "$REPO"
echo
echo "Done. Back up $KS and its password now. Every release built from here on is signed with it."
keytool -list -keystore "$KS" -storepass "$PW" -alias "$ALIAS" 2>/dev/null | grep -i 'SHA256' || true
