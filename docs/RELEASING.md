# Releasing a build

Releases are built by the GitHub Actions workflow in `.github/workflows/release.yml`.
It compiles the `nonRoot_game` release flavor (one APK per ABI: arm64-v8a, armeabi-v7a,
x86, x86_64), signs the APKs, and publishes them as a GitHub release when a tag is pushed.

## Cutting a release

1. Bump `versionName` and `versionCode` in `app/build.gradle` on `moonlight-noir`.
2. Tag the commit with `v<versionName>` (the workflow refuses a tag that does not match
   `versionName`) and push the tag:

       git tag v20.3.0-fork.1
       git push origin v20.3.0-fork.1

3. Watch the "Build and release APKs" workflow. When it finishes, the release appears under
   *Releases* with the APKs and a `SHA256SUMS.txt` attached.

`workflow_dispatch` (the *Run workflow* button) builds and signs the same APKs and uploads
them as workflow artifacts. If its `release_tag` input is set (e.g. `v20.3.0-fork.1`), the run
also creates that tag at the built commit and publishes the release — the same result as
pushing the tag, for environments that cannot push tags.

## Signing

Without any configuration the workflow signs with a **throwaway key** generated for the run.
Those APKs install fine, but every release carries a different signature, so Android refuses
to install one over another: uninstall the previous build first (pairing data is lost).

To get updatable releases, create a keystore once and store it as repository secrets:

    keytool -genkeypair -v -keystore release.jks -alias release -keyalg RSA -keysize 2048 \
        -validity 36500 -dname "CN=<your name>"
    base64 -w0 release.jks     # -> KEYSTORE_BASE64

Secrets (Settings → Secrets and variables → Actions):

| Secret              | Value                                   |
|---------------------|-----------------------------------------|
| `KEYSTORE_BASE64`   | the base64 output above                 |
| `KEYSTORE_PASSWORD` | the keystore password you chose         |
| `KEY_ALIAS`         | `release` (or the alias you used)       |
| `KEY_PASSWORD`      | the key password (same as the keystore unless you set one) |

Keep `release.jks` somewhere safe; losing it means users must uninstall to update.

## Obtainium

Add `https://github.com/jlobue10/moonlight-android` as a GitHub source in Obtainium and
choose the arm64-v8a APK when asked. The in-app "Obtainium" link (the `obtainium_app_url`
resource in `app/build.gradle`) preconfigures the same source with an `apkFilterRegEx` of
`nonRoot`.

Note that this fork keeps Artemis's application id (`com.limelight.noir`). A build from this
repository cannot be installed over the official Artemis release (different signature):
uninstall it first.
