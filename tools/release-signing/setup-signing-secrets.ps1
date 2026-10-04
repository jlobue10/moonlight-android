<#
.SYNOPSIS
  Creates (or reuses) the APK release keystore and stores it in the GitHub repository as the
  four Actions secrets that .github/workflows/release.yml signs with:
  KEYSTORE_BASE64, KEYSTORE_PASSWORD, KEY_ALIAS, KEY_PASSWORD.

.DESCRIPTION
  Windows counterpart of setup-signing-secrets.sh. Needs keytool (any JDK 9+) and gh
  (https://cli.github.com, logged in) on PATH. Works in Windows PowerShell 5.1 and PowerShell 7.
  Never commit the keystore or its password. Keep both in a password manager: without them a
  future release cannot be installed over the ones signed with this key.

.PARAMETER Keystore
  Path of the keystore. Default .\release.jks; created if missing, reused if present.
.PARAMETER Repo
  Target repository as owner/name. Default jlobue10/moonlight-android.
.PARAMETER KeyAlias
  Key alias. Default release.
#>
param(
    [string]$Keystore = "release.jks",
    [string]$Repo = "jlobue10/moonlight-android",
    [string]$KeyAlias = "release",
    [string]$DName = "CN=moonlight-android fork release key"
)
# Native commands (keytool, gh) write progress to stderr; under "Stop" Windows PowerShell 5.1
# would turn that into a terminating error, so exit codes are checked by hand instead.
$ErrorActionPreference = "Continue"

function Assert-Exit([string]$what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

function Read-Plain([string]$prompt) {
    $secure = Read-Host -AsSecureString $prompt
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

foreach ($tool in @("keytool", "gh")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool not found on PATH" }
}
& gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "gh is not logged in: run 'gh auth login'" }

if (Test-Path $Keystore) {
    Write-Host "Using the existing keystore $Keystore (alias $KeyAlias)"
    $pw = Read-Plain "Keystore password"
    & keytool -list -keystore $Keystore -storepass $pw -alias $KeyAlias 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "cannot open $Keystore with that password, or alias $KeyAlias is not in it" }
} else {
    Write-Host "Creating the keystore $Keystore (alias $KeyAlias, RSA 4096, valid 100 years)"
    $pw = Read-Plain "Choose a keystore password (it is also the key password)"
    $pw2 = Read-Plain "Repeat it"
    if ($pw -ne $pw2) { throw "the passwords differ" }
    if ($pw.Length -lt 6) { throw "keytool needs at least 6 characters" }
    & keytool -genkeypair -v -keystore $Keystore -storetype PKCS12 -storepass $pw -keypass $pw `
        -alias $KeyAlias -keyalg RSA -keysize 4096 -validity 36500 -dname $DName
    Assert-Exit "keytool -genkeypair"
}

Write-Host "Storing the secrets in $Repo"
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Resolve-Path $Keystore).Path))
& gh secret set KEYSTORE_BASE64   --repo $Repo --body $b64;      Assert-Exit "gh secret set KEYSTORE_BASE64"
& gh secret set KEYSTORE_PASSWORD --repo $Repo --body $pw;       Assert-Exit "gh secret set KEYSTORE_PASSWORD"
& gh secret set KEY_ALIAS         --repo $Repo --body $KeyAlias; Assert-Exit "gh secret set KEY_ALIAS"
& gh secret set KEY_PASSWORD      --repo $Repo --body $pw;       Assert-Exit "gh secret set KEY_PASSWORD"
& gh secret list --repo $Repo
Write-Host ""
Write-Host "Done. Back up $Keystore and its password now. Every release built from here on is signed with it."
& keytool -list -keystore $Keystore -storepass $pw -alias $KeyAlias 2>&1 | Select-String -Pattern "SHA256"
