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

Releases must be signed with one persistent key, otherwise Android refuses to install a new
build over the previous one (and Obtainium updates fail). The workflow reads that key from
four repository secrets; without them it only signs with a **throwaway key** generated for the
run, which is fine for build-only runs but a release is refused unless the
`allow_throwaway_key` input is set (every such release forces users to uninstall first,
losing their pairing data).

Set the secrets once, from a machine with a JDK (`keytool`) and the GitHub CLI (`gh auth login`):

    tools/release-signing/setup-signing-secrets.sh            # Linux/macOS/Git Bash
    tools/release-signing/setup-signing-secrets.ps1           # Windows PowerShell

The script creates `release.jks` in the current directory (or reuses one you point it at),
asks for a password, and stores these secrets (Settings → Secrets and variables → Actions):

| Secret              | Value                                   |
|---------------------|-----------------------------------------|
| `KEYSTORE_BASE64`   | the keystore file, base64-encoded       |
| `KEYSTORE_PASSWORD` | the keystore password                   |
| `KEY_ALIAS`         | `release` (or the alias you chose)      |
| `KEY_PASSWORD`      | the key password (same as the keystore) |

The same four values can be pasted by hand (`base64 -w0 release.jks` for the first one).
**Keep `release.jks` and its password somewhere safe and never commit them**: losing the key
means every user must uninstall to update. The first release signed with the persistent key
still has to be installed after uninstalling the throwaway-signed builds that preceded it.

## Obtainium

Add `https://github.com/jlobue10/moonlight-android` as a GitHub source in Obtainium and
choose the arm64-v8a APK when asked. The in-app "Obtainium" link (the `obtainium_app_url`
resource in `app/build.gradle`) preconfigures the same source with an `apkFilterRegEx` of
`nonRoot`.

Note that this fork keeps Artemis's application id (`com.limelight.noir`). A build from this
repository cannot be installed over the official Artemis release (different signature):
uninstall it first.
