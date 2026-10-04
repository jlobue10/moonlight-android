# Security audit — jlobue10/moonlight-android (Artemis, branch `moonlight-noir`, versionName 20.2.6)

Scope: `/home/user/jlobue10/moonlight-android` @ `c5cf27f4` (identical to ClassicOldSong/moonlight-android @ c5cf27f). Compared against `og/master` = moonlight-stream/moonlight-android (v12.2) and, for the native submodule, `og/master` of moonlight-stream/moonlight-common-c (merge-base with Artemis: `5f22801`). All line numbers below are from the files in this checkout. "CONFIRMED" = established by reading the code path end-to-end; "SUSPECTED" = the code path exists but the practical impact needs an on-device test.

---

## (A) Executive summary

The app's core trust model is sound and unchanged from upstream Moonlight: the pairing protocol is implemented correctly, the paired server certificate is pinned by DER-equality for all HTTPS traffic, there is **no** accept-all trust path, the client identity is an RSA-2048 key in app-private storage, SQLite access is parameterized, and nothing the host sends is ever executed, used as a path, URL, intent, or shell command on the client. The fork-specific risk is concentrated in a handful of Artemis additions (deep links, accessibility service, clipboard sync, OTP pairing) and in lagging third-party/native code.

Top 5 issues:

1. **[Medium] `KeyboardAccessibilityService` captures and forwards *all* hardware key events system-wide while a stream is connected, with no focus/foreground check** (`KeyboardAccessibilityService.java:27-46`). Because `Game` stays connected while paused (PiP / split-screen — `Game.java:1746-1758`, `1373-1397`), keys typed into *other* apps (including password fields) are swallowed locally and sent to the host. Artemis-only. CONFIRMED (code); SUSPECTED (exact PiP/split-screen behavior on a given OEM build).
2. **[Medium] BROWSABLE `art://` deep links can (a) start a stream to a paired PC and launch any app with no confirmation, and (b) auto-pair the device to an attacker-chosen host with an attacker-supplied PIN+passphrase after a single "Proceed" tap** (`AddComputerManually.java:288-313, 370-392, 205-216`; `PcView.java:257-267, 303-318`). Any installed app can do the same through the exported `ShortcutTrampoline`/`AddComputerManually`. Artemis-only (commits `a4f6bd63`, `e329f09b`). CONFIRMED.
3. **[Medium] Unauthenticated remote crash from any LAN host**: integer parsing of untrusted `serverinfo` fields (`status_code`, `HttpsPort`, `currentgame`, `appversion`) throws `NumberFormatException`/`IllegalArgumentException` that escapes `tryPollIp`'s `catch (XmlPullParserException|IOException)` on a bare `Thread` → process death. Reachable via an mDNS advertisement before any pairing (`NvHTTP.java:329, 654, 822-835`; `ComputerManagerService.java:573-578`). Shared with upstream (identical code in og/master). CONFIRMED (code); SUSPECTED (process death — standard Android behavior for an uncaught exception on any thread).
4. **[Medium] Native stream parser is 44 upstream commits behind and lacks upstream's RTSP hardening** (`7b026e7`: NULL-deref on a malformed `Session:` header and unbounded RTSP response buffer growth). Vulnerable lines present in this checkout: `moonlight-common-c/src/RtspConnection.c:1212, 334, 440`. Malicious/compromised host → crash or memory exhaustion at handshake. CONFIRMED.
5. **[Low] Prebuilt OpenSSL is 1.1.1q (Jul 2022, EOL since Sep 2023; upstream ships 4.0.2) and `libssl.a` is linked for no reason.** Exposure is small because the native code only uses `EVP_aes_128_gcm/cbc` + `RAND_bytes` (`PlatformCrypto.c:17-18`) and TLS is done by the platform (Conscrypt via OkHttp), so none of the 1.1.1r–w CVEs reach this code — but it is unmaintained crypto on the packet path. CONFIRMED (version); CVE applicability assessed from memory (see §D).

Also notable: pairing identity (`files/client.key`, `files/client.crt`) and all paired server certs (`databases/computers4.db`) are **included** in Auto Backup / device-to-device transfer (only `sharedpref` is excluded) — a restored backup streams without re-pairing. This is identical to upstream and arguably intended, but is worth a deliberate decision (§B, L1).

---

## (B) Findings by severity

### Critical
None found.

### High
None found. (Finding M1 below is the closest; it was rated Medium because it requires the user to opt in to the accessibility service and to multitask with a hardware keyboard while a stream is running.)

### Medium

#### M1. Accessibility service forwards system-wide hardware key events to the host whenever a stream is connected (Artemis-only)
- Files: `app/src/main/java/com/limelight/KeyboardAccessibilityService.java:21-49`, `:51-61`; `app/src/main/res/xml/keyboard_accessibility_service.xml` (`flagRequestFilterKeyEvents`, `canRequestFilterKeyEvents="true"`); `Game.java:190` (`public boolean connected`), `Game.java:3676-3688` (set true in `connectionStarted`), `Game.java:3454-3456` (cleared only in `stopConnection`), `Game.java:1746-1758` (`onPause` does **not** stop the stream), `Game.java:1373-1397` (auto-enters PiP on `onUserLeaveHint`), `AndroidManifest.xml` (`android:supportsPictureInPicture="true"`, `android:resizeableActivity="true"`).
- Code:
  ```java
  public boolean onKeyEvent(KeyEvent event) {
      ...
      if (Game.instance != null && Game.instance.connected && !BLACKLIST_KEYS.contains(keyCode)) {
          if (action == KeyEvent.ACTION_DOWN) { ... Game.instance.handleKeyDown(event); return true; }
          else if (action == KeyEvent.ACTION_UP) { ... Game.instance.handleKeyUp(event); return true; }
  ```
  `onServiceConnected()` sets `info.packageNames = {APPLICATION_ID}`, but that only filters *accessibility events*; `FLAG_REQUEST_FILTER_KEY_EVENTS` delivers every hardware key event in the system to `onKeyEvent` regardless of the focused package.
- Scenario: user enables the "keyboard" accessibility service (it is advertised for Xiaomi-pad ESC/Home fixes), starts a stream, then switches to another app with the stream in PiP (automatic on leave) or split-screen. Every key typed on a hardware/Bluetooth keyboard into the other app — password manager, banking app, lock-screen PIN on some devices — is consumed (`return true`, so the other app never sees it) and sent to the host via `handleKeyDown/Up`. The host is the user's own paired PC, so this is primarily an integrity/privacy defect rather than exfiltration to a third party, but a compromised host receives the keystrokes.
- Status: CONFIRMED (no focus check anywhere on the path; `handleKeyDown` at `Game.java:2045` checks only `FLAG_VIRTUAL_HARD_KEY`/`ignoreSynthEvents`). SUSPECTED only as to which OEM builds keep `Game` resumed vs paused in multi-window.
- Upstream: not present (upstream has no accessibility service; it requests `CAPTURE_KEYBOARD` instead).
- Fix: gate on focus, e.g.
  ```java
  Game g = Game.instance;
  if (g == null || !g.connected || !g.hasWindowFocus() ||
      (Build.VERSION.SDK_INT >= 26 && g.isInPictureInPictureMode())) return super.onKeyEvent(event);
  ```
  (accept `ExternalDisplayControlActivity.instance.hasWindowFocus()` as an alternative focus owner for the dual-display mode), and additionally drop the service's `canRetrieveWindowContent`/`flagRetrieveInteractiveWindows` from `keyboard_accessibility_service.xml` — nothing in `onAccessibilityEvent` uses window content.

#### M2. `art://` deep links / exported activities allow third parties to start streams and to drive the auto-pair flow (Artemis-only)
- Files: `AndroidManifest.xml` (`AddComputerManually` `exported="true"` + `<data android:scheme="art"/>` BROWSABLE; `ShortcutTrampoline` `exported="true"` + BROWSABLE `content`/`file` `.*\.art`); `preferences/AddComputerManually.java:288-313` (`launch` host), `:364-392` (confirmation dialog), `:195-216` (forwards `pin`/`passphrase` to `PcView`); `PcView.java:255-267`, `:303-318` (auto-`doPair` with the supplied PIN/passphrase once the host polls ONLINE); `ShortcutTrampoline.java:311-351` (`.art` parser), `:373-383`, `:144-204`.
- Code (`AddComputerManually.java:293-310`):
  ```java
  if (Objects.equals(urlAction, "launch")) {
      String hostUUID = data.getQueryParameter("host_uuid"); ... String appID = data.getQueryParameter("app_id");
      Intent intent = new Intent(AddComputerManually.this, ShortcutTrampoline.class);
      intent.setFlags(Intent.FLAG_ACTIVITY_CLEAR_TASK | Intent.FLAG_ACTIVITY_NEW_TASK);
      ... startActivity(intent);
  ```
  and (`:205-215`):
  ```java
  String pin = uri.getQueryParameter("pin"); String passphrase = uri.getQueryParameter("passphrase");
  if (pin != null && passphrase != null) { Intent intent = new Intent(AddComputerManually.this, PcView.class); ... intent.putExtra("pin", pin); intent.putExtra("passphrase", passphrase); startActivity(intent); }
  ```
- Scenarios:
  1. `art://launch?host_name=DESKTOP-ABC&app_id=1` from a web page or any app: `ShortcutTrampoline` looks the host up in the local DB (by name or UUID — both are advertised in cleartext `serverinfo` to anyone on the LAN), and if it is ONLINE+PAIRED immediately calls `startActivities(createStartIntent(...))` with **no user confirmation** (`ShortcutTrampoline.java:147-155`); a confirmation only appears if a *different* game is already running (`:156-179`). Result: a stream to the user's PC launching an attacker-chosen app id (e.g. "Desktop"), surfacing the host screen and sending the device's input to it.
  2. `art://1.2.3.4:47989?name=My%20PC&pin=1234&passphrase=abcd` : one dialog ("Proceed" vs "Cancel", title = attacker-chosen `name` + address), then the app contacts the host, adds it, and silently completes OTP pairing with the attacker's PIN/passphrase. The device is now paired to an attacker host that (once the user connects) can use every host→client channel (clipboard write if sync is on, app list spoofing, crafted stream data — see M3/M4). Pairing a host the user never typed a PIN into undermines the only out-of-band step of the protocol.
  3. Functional bug on the same path: without an explicit port, `uri.getPort()` is `-1`, which is stored as the `port` extra and makes `PcView.onCreate` throw `IllegalArgumentException("Invalid port")` in `ComputerDetails.AddressTuple` (`ComputerDetails.java:23`) → crash of the launcher activity. CONFIRMED by reading, untested.
- Status: CONFIRMED. (Whether Chrome will hand `content:`-scheme `intent://` URIs to `ShortcutTrampoline` from a web page is SUSPECTED — Chrome blocks `content`/`file` schemes from web-initiated intents; the `art:` scheme on `AddComputerManually` has no such restriction.)
- Upstream: `ShortcutTrampoline` is exported with the same extras upstream (scenario 1 via a local app is shared); the `art://` scheme, the `.art` file filter, and PIN/passphrase auto-pair are Artemis additions (`a4f6bd63` "Implement OTP pairing", `e329f09b` "Add art:// Deep Link support", `6e31164b` "Add support for opening .art files").
- Fix: (i) show a confirmation dialog in `ShortcutTrampoline` when the launch did not originate from the app's own launcher shortcuts (check `getCallingPackage()`/`getReferrer()` or require a signature-level permission for the extras path; for `.art`/`art://` always confirm host + app name); (ii) never accept `pin`/`passphrase` from a URI — at most prefill the OTP dialog (`doOTPPair`) and require the user to press Pair; (iii) handle `port == -1` → default port; (iv) escape the manifest pattern (`android:pathPattern=".*\\.art"`).

#### M3. Unauthenticated LAN host can crash the app through unchecked integer parsing of `serverinfo` (shared with upstream)
- Files: `nvstream/http/NvHTTP.java:324-340` (`verifyResponseStatus`: `(int)Long.parseLong(xpp.getAttributeValue(..., "status_code"))` — `null` or non-numeric → `NumberFormatException`), `:652-662` (`getHttpsPort` catches only `XmlPullParserException`/`IOException` around `Integer.parseInt`), `:640-650` (`getCurrentGame` → `Integer.parseInt`), `:822-836` (`getServerAppVersionQuad` → `IllegalArgumentException`/`NumberFormatException`); `computers/ComputerManagerService.java:544-579` (`tryPollIp` catches only `XmlPullParserException | IOException`), `:601-625` (runs on `new Thread()` "Parallel Poll"), `:394-428` (mDNS `notifyComputerAdded` → `addComputerBlocking` with no pairing), `:402` (`new AddressTuple(..., computer.getPort())` throws for SRV port 0 — `ComputerDetails.java:23`).
- Scenario: an attacker on the LAN advertises `_nvstream._tcp` (or answers at a known host address) and serves `<root status_code="x">...` or `<HttpsPort>abc</HttpsPort>`. The discovery/poll thread throws an unchecked exception that no frame catches → Android's default uncaught-exception handler kills the process. No pairing or user action needed beyond having the PC list open; it repeats on every relaunch while the attacker is present (persistent DoS of the client on that network).
- Status: CONFIRMED (exception propagation); SUSPECTED (process kill — standard Android behavior, no `setDefaultUncaughtExceptionHandler` is installed).
- Upstream: identical code in og/master (`verifyResponseStatus`/`getHttpsPort` byte-identical; `tryPollIp` has the same two catch clauses). Not fixed upstream as of v12.2.
- Fix: in `tryPollIp` add `catch (RuntimeException e) { LimeLog.warning(...); return null; }`; in `verifyResponseStatus` treat a missing/invalid `status_code` as a malformed response (`throw new XmlPullParserException`); wrap `Integer.parseInt` in `getHttpsPort`/`getCurrentGame`/`getServerAppVersionQuad` with `NumberFormatException` handling; validate mDNS port `1..65535` before constructing `AddressTuple`.

#### M4. moonlight-common-c submodule lags upstream by 44 commits and lacks the RTSP hardening commit
- Files: submodule at `c999436` (merge-base with upstream `5f22801`; upstream `og/master` = `874ac95`). Vulnerable lines in this checkout: `moonlight-common-c/src/RtspConnection.c:1212`
  ```c
  sessionIdString = strdup(strtok_r(sessionId, ";", &strtokCtx));   // NULL if header is ";" → strdup(NULL)
  ```
  and the unbounded `extendBuffer(responseBuffer, ...)` growth at `:334` (ENet path) and `:440` (TCP path) with no `MAX_RTSP_RESPONSE_SIZE`.
- Scenario: a malicious or compromised host (one the user is connecting to) replies to `SETUP streamid=audio` with `Session: ;` → NULL dereference → crash; or streams an arbitrarily large RTSP response → unbounded `realloc` → OOM kill. Both are crash/DoS, not code execution.
- Status: CONFIRMED (upstream commit `7b026e7` "Harden RTSP handling for malformed Session headers and oversized responses", 2026-03-24, is absent; its hunks apply cleanly to these lines).
- Other upstream commits missing that touch parsers/stability: `be43885` "Fix handling of a partially dropped IDR frame", `d85371c` "Fix RFI after a multi-block frame loss", `62e0663`, enet bumps `6268780`/`703a069` (Artemis enet pin `115a10b` 2025-07-04 vs upstream `aca8784`), nanors switch (`de364b6`, `1f76427`) replacing `reedsolomon/rs.c`.
- Artemis-only native hunks (reviewed, `git diff 5f22801..HEAD -- src/`): adds packet types `0x3000/0x3001/0x3002` (exec server cmd / set clipboard / file-transfer nonce) to the Gen7Enc table, marks `0x3001`/`0x3002` as `needsAsyncCallback`, but `queueAsyncCallback()` has **no handler** for them → `LC_ASSERT(false); free(queuedCb); return;` (`ControlStream.c:1089-1094`). In release builds `LC_ASSERT` is a no-op (`Platform.h:85-96`, `-DLC_DEBUG` only with `NDK_DEBUG=1`, `moonlight-core/Android.mk:49-51`), so a host sending `0x3001` is silently dropped; in debug builds it aborts the process. `LiSendExecServerCmd` sends a 4-byte `{cmdId,0,0,0}` payload on a new reliable channel `0x08`; `LiSendEmptyPayload` sends 4 constant bytes. No parsing of host data was added. The `AP_SERVER_CMD_PACKET`/`AP_SERVER_CMD_MAGIC` definitions in `Input.h` are unused. Nothing unsafe found in the fork-specific hunks.
- Fix: merge `moonlight-stream/moonlight-common-c` master (at minimum cherry-pick `7b026e7`, `be43885`, `d85371c`) and bump enet; remove the dead `IDX_SET_CLIPBOARD`/`IDX_FILE_TRANSFER_NONCE_REQUEST` entries from `needsAsyncCallback()` so the debug-build assert cannot be triggered by a host.

### Low

#### L1. Pairing identity and paired server certificates are backed up to the cloud / transferred device-to-device (shared with upstream)
- Files: `AndroidManifest.xml` (`android:allowBackup="true"`, `fullBackupContent="@xml/backup_rules"`, `dataExtractionRules="@xml/backup_rules_s"`); `res/xml/backup_rules.xml` and `backup_rules_s.xml` exclude only `<exclude domain="sharedpref" path="."/>`; identity lives in `files/client.crt` + `files/client.key` (`AndroidCryptoProvider.java:58-63`, PKCS#8 private key written raw at `:181`), paired server certs + addresses + MACs in `databases/computers4.db` (`ComputerDatabaseManager.java:26, 129`), `files/uniqueid` (`IdentityManager.java:15`).
- Scenario: whoever can restore the Google backup (or a D2D transfer) onto another device obtains a client that is already paired to every host in the list — no PIN needed. Cloud backup is end-to-end encrypted with the lock screen on Android 9+ and `disableIfNoEncryptionCapabilities="true"` enforces that, so this is a conscious usability trade-off rather than a leak; but the host side cannot distinguish the clone from the original.
- Status: CONFIRMED. Upstream: identical rules (og/master `backup_rules*.xml` byte-identical).
- Fix (if the project wants "one device = one identity"): add `<exclude domain="file" path="client.key"/>` (and `client.crt`, `uniqueid`) to both rule files; a restored install then re-pairs.

#### L2. Clipboard sync (opt-in) lets the paired host read and overwrite the device clipboard without per-event consent
- Files: `Game.java:2248-2257` (`handleFocusChange`: focus gained → `sendClipboard(false)`, focus lost → `getClipboard(0)`), `:3708` (on `connectionStarted`), `:4230-4231` (on disconnect), `:2319-2351` (device→host POST of the whole clip text), `:2354-2404` (host→device: `httpConn.getClipboard()` is an unbounded `ResponseBody.string()` — `NvHTTP.java:922-926` — written to the clipboard with `IS_SENSITIVE` only when `hideClipboardContent` is set); `PreferenceConfiguration.java:201-203` (`DEFAULT_SMART_CLIPBOARD_SYNC = false`, `DEFAULT_HIDE_CLIPBOARD_CONTENT = true`); `GameMenu.java:295-299` (manual upload/fetch).
- Scenario: with "smart clipboard sync" enabled, (a) every time the user returns to the stream, whatever they last copied in another app (e.g. a password from a manager) is POSTed to the host; (b) every time the user leaves the stream, the host's clipboard replaces the device clipboard — a compromised host can plant a different crypto address/URL exactly when the user is about to paste, and can push multi-megabyte payloads (a `TransactionTooLargeException` is caught at `:2394`).
- Status: CONFIRMED. Upstream: feature does not exist. Only the paired host can trigger it (pinned HTTPS), so this is inherent to the feature; rated Low because it is off by default.
- Fix: cap `getClipboard()` to e.g. 64 KiB via `ResponseBody.source().readUtf8(limit)`; skip host→device sync on focus loss (keep explicit "Fetch clipboard"); consider a one-time per-session consent toast with an "undo".

#### L3. OTP pairing hash is sent over cleartext HTTP and is offline brute-forceable (Artemis-only)
- Files: `nvstream/http/PairingManager.java:212-227`:
  ```java
  String plainText = pin + saltStr + passphrase;  byte[] hash = digest.digest(plainText.getBytes());
  pairingArguments += "&otpauth=" + hexString;
  ```
  sent by `executePairingCommand(...)` which always uses `baseUrlHttp` (`NvHTTP.java:799-802`, port 47989, plaintext); `PcView.java:620` enforces `passphrase.length() >= 4` only in the dialog; the `art://` path (`AddComputerManually.java:205-216`) enforces nothing.
- Scenario: a passive attacker on the same Wi-Fi captures `salt` and `otpauth`, brute-forces 10⁴ PINs × a passphrase dictionary offline (seconds for 4-char passphrases), then races the legitimate client to complete pairing with its own certificate while the OTP is still valid.
- Status: SUSPECTED (depends on Apollo's OTP validity window and single-use semantics, which are host-side). Upstream: no OTP feature.
- Fix: require a longer passphrase (≥ 12 chars) in the dialog and in the deep-link path, or move the OTP exchange to HTTPS (the server cert is already known after `getservercert`).

#### L4. mDNS/HTTP spoofing can rewrite a paired host's stored address/name/MAC and fake its "paired/online" status (shared with upstream)
- Files: `NvHTTP.java:355-383` (`getServerInfo`: on pinned-cert mismatch → `HostHttpResponseException(401)` → falls back to **plain HTTP** `serverinfo` and returns it as authoritative), `:439` (`PairStatus` read from that HTTP body), `ComputerManagerService.java:119-137` (`existingComputer.update(details); dbManager.updateComputer(...)`), `ComputerDetails.java:123-168` (`update()` copies `name`, `localAddress`, `ipv6Address`, `macAddress`, `permission`, `serverCommands`, `pairState` from the polled host). Identity is the host-asserted `<uniqueid>` (`NvHTTP.java:410`).
- Scenario: an attacker on the LAN advertises the victim PC's UUID. The client picks up the pinned cert for that UUID (`:476-483`), HTTPS fails on cert mismatch, the HTTP fallback is accepted, and the DB entry's local address/name/MAC are overwritten with the attacker's; the UI shows it ONLINE and PAIRED (`PairStatus=1` over HTTP) with attacker-chosen `ServerCommand` labels. Streaming/launch still fails (those go over pinned HTTPS), so impact is DoS/confusion, not access. WoL packets go to the attacker's MAC.
- Status: CONFIRMED. Upstream: same code (`update()`/`getServerInfo` identical apart from Artemis fields).
- Fix: when the HTTPS poll fails with a certificate mismatch for a host that has a pinned cert, do not merge the HTTP-sourced details into the DB (treat as "cert mismatch / needs re-pair" state) and never take `PairStatus` from an HTTP response for a host with a pinned cert.

#### L5. Root flavor: evdev reader socket is bound on all interfaces; first connector wins (shared with upstream)
- Files: `app/src/root/java/com.limelight/binding/input/evdev/EvdevCaptureProvider.java:46` (`servSock = new ServerSocket(0, 1);` — wildcard bind), `:52-64` (`su -c "<nativeLibraryDir>/libevdev_reader.so <port>"`), `:93` (single `accept()`); `jni/evdev_reader/evdev_reader.c:287-318` (connects to `127.0.0.1:<port>`), `:331` (`atoi(argv[1])` with no `argc` check), `:216, :232` (`sprintf`/`strcpy` of `/dev/input/<d_name>` into 256/128-byte buffers — kernel-controlled names, not attacker-controlled).
- Scenario: a local app (or a LAN host guessing the ephemeral port) that connects before the root helper injects fabricated evdev packets that become mouse/keyboard input to the host (`:116-196`). Window is the few ms between `bind()` and the root process connecting; a local port scanner can win it.
- Status: CONFIRMED (bind semantics). Upstream: byte-identical. Root flavor is `maxSdk 25`, so exposure is legacy devices.
- Fix: `new ServerSocket(0, 1, InetAddress.getLoopbackAddress())`, and verify `evdevSock.getInetAddress().isLoopbackAddress()` after `accept()`; add `if (argc < 2) return 1;` in `main()`.

#### L6. Exported `PosterContentProvider`: unvalidated UUID path segment (limited traversal), non-numeric app id surfaces as a caller-side exception (shared with upstream)
- Files: `PosterContentProvider.java:45-61`; `DiskAssetLoader.java:146-148`; `utils/CacheHelper.java:16-31`.
  ```java
  String appId = segments.get(APP_ID_PATH_INDEX); String uuid = segments.get(COMPUTER_UUID_PATH_INDEX);
  File file = mDiskAssetLoader.getFile(uuid, Integer.parseInt(appId));   // cacheDir/boxart/<uuid>/<int>.png
  ```
- Scenario: any app can open `content://poster.com.limelight.noir/boxart/<uuid>/<id>` (purpose: Android TV channel posters via `TvChannelHelper`, `TvContract` logo URIs — legitimately needs to be exported). `uuid` is not validated, so `..%2F..` traversal is possible, but the final component is forced to `<int>.png`, so only files literally named like `123.png` under app-readable paths can be read — effectively just the app's own box art (which is host-provided, non-secret). A non-numeric id yields `NumberFormatException` (an `IllegalArgumentException`, which Binder propagates to the caller rather than crashing this process). `query()`/`insert()`/`update()`/`delete()` throw `UnsupportedOperationException`.
- Status: CONFIRMED. Upstream: byte-identical.
- Fix: validate `uuid` with `UUID.fromString` and reject anything whose canonical path is outside `cacheDir/boxart`; `getType()` could return the real MIME type.

#### L7. OpenSSL 1.1.1q prebuilt (EOL) and unnecessary `libssl` link
- Files: `jni/moonlight-core/openssl/include/openssl/opensslv.h:42-43` (`0x1010111fL`, "OpenSSL 1.1.1q  5 Jul 2022"; same string in `openssl/arm64-v8a/libcrypto.a`); `openssl/Android.mk` (exports `libssl` + `libcrypto`); `moonlight-core/Android.mk:56` (`LOCAL_STATIC_LIBRARIES := libopus libssl libcrypto cpufeatures`). Upstream og/master: OpenSSL **4.0.2** (`opensslv.h` `OPENSSL_VERSION_TEXT "OpenSSL 4.0.2 25 Aug 2026"`), `libcrypto` only, no `libssl.a` in the tree.
- Usage scope (`moonlight-common-c/src/PlatformCrypto.c:17-18` and symbol grep): only `EVP_aes_128_gcm`, `EVP_aes_128_cbc`, `EVP_{Encrypt,Decrypt}{Init,Update,Final}_ex`, `EVP_CIPHER_CTX_ctrl(GCM IV/TAG)`, `RAND_bytes`. No ASN.1/X.509/PEM/TLS/DH/RSA in native code. OpenSSL is not used for TLS (that is Conscrypt via OkHttp 4.12).
- CVE applicability (from memory — verify before quoting): 1.1.1r–1.1.1w fixed CVE-2023-0286 (X.400 GeneralName type confusion), CVE-2022-4304 (RSA timing oracle), CVE-2023-0215 (BIO_new_NDEF UAF), CVE-2022-4450 (PEM_read_bio_ex double free), CVE-2023-0464/0465/0466 (X.509 policy), CVE-2023-2650 (OBJ_obj2txt DoS), CVE-2023-3446/3817 (DH checks), CVE-2023-4807 (Windows POLY1305). Post-EOL premium fixes: CVE-2023-5678, CVE-2024-0727, CVE-2024-2511, CVE-2024-4741, CVE-2024-5535, CVE-2024-9143, CVE-2024-13176. **None of these are in the EVP AES-GCM/CBC or RAND code paths this binary executes**, so I assess no currently known exploitable CVE; the finding is unmaintained crypto on the attacker-facing packet path (every control/input/audio packet is decrypted here) plus ~1.7 MB of dead `libssl` code per ABI.
- Status: CONFIRMED (version/usage); CVE list SUSPECTED/unverified.
- Fix: take the `openssl/` prebuilts from og/master (built from cgutman/moonlight-mobile-deps per `Build.txt`), drop `libssl` from both `Android.mk` files.

#### L8. Debug-only: pairing secrets and the stream AES key are logged in debug builds
- Files: `NvHTTP.java:76` (`verbose = BuildConfig.DEBUG`), `:530-538` (logs full URL + response body for every non-`serverinfo` request — this includes `pair?...clientchallenge=`, `serverchallengeresp=`, `clientpairingsecret=` and `launch?...rikey=<AES key>&rikeyid=`). `IdentityManager.java:26` logs the device unique id (not secret).
- Status: CONFIRMED; release builds (`verbose=false`) do not log these. Upstream: identical. Fix: redact `rikey`/pairing parameters even in debug.

### Info (observations, no action strictly required)

- **TLS is correct and strictly pinned (positive).** `NvHTTP.java:131-158` tries the system trust store first, then accepts a single-cert chain only if `certs[0].equals(serverCert)` (`X509Certificate.equals` compares DER encodings) — there is no accept-all path; with no pinned cert, HTTPS simply fails, so `launch`/`applist`/`cancel`/clipboard can only ever reach the paired host. `HostnameVerifier` (`:160-175`) accepts any name only for the pinned cert. Pairing's `getservercert`/`clientchallenge`/`serverchallengeresp`/`clientpairingsecret` run over HTTP by protocol design (`:799-802`); the server's signature over its secret is verified with the cert received in the same exchange (`PairingManager.java:282-288`) and the PIN-bound challenge check (`:291-298`) is what authenticates it — correct per the GameStream protocol and identical to upstream apart from the `otpauth` addition. `SecureRandom` is used for PIN, salt, challenge and client secret (`:90-95, 176-181`). Minor: `hexToBytes` throws `IllegalArgumentException` on odd-length hex and `Arrays.copyOfRange(...,16,len)` throws on a short `pairingsecret` (`:56-68, :277-279`) — a malicious host can crash the pairing thread (user-initiated; `doPair` catches only IO/XML exceptions, `PcView.java:559-566`). Shared with upstream.
- **Network security config** (`res/xml/network_security_config.xml`): `cleartextTrafficPermitted="true"` globally (needed for port 47989), system trust anchors only, no user CAs, no debug overrides. Identical to upstream. Could be narrowed with `<domain-config>` but hosts are arbitrary IPs, so global cleartext is protocol-inherent.
- **Key storage**: RSA-2048, `SHA256withRSA`, 20-year self-signed cert, generated with BouncyCastle, written with `FileOutputStream` into `getFilesDir()` (`AndroidCryptoProvider.java:120-122, 160-188`) — app-private (0600 under `/data/data/<pkg>/files`). Not in Android Keystore (impossible: the protocol needs the raw PEM/PKCS#8 and BC-side signing — but `signData` only uses `java.security.Signature`, so a Keystore-backed `PrivateKey` for *signing* would actually be feasible if the cert stayed on disk). Identical to upstream.
- **Host-provided data is never executed/used as a path/URL/intent**: `ServerCommand` strings are only menu labels; execution sends an index (`GameMenu.java:274-286`, `simplejni.c:19-22`, `ControlStream.c:2053-2063`). App names/UUIDs from the host go only into intents to the app's own non-exported activities and into query strings to the same host (`NvHTTP.java:878-893`, `appuuid` unescaped but only ever sent back to the host that produced it). Imported `.json` shortcut files resolve `VK_*` names via `KeyMapper.class.getDeclaredField(code)` (`GameMenu.java:219-226`) — reflection is confined to one class and exceptions are caught.
- **Manifest hardening**: no `android:debuggable`; `gwpAsanMode="always"` (hardening, perf cost); `extractNativeLibs="true"` only in the root flavor (needed to exec `libevdev_reader.so`); `installLocation="auto"`; `StartExternalDisplayControlReceiver` has no intent-filter and no `exported` attribute → not exported on API 31+ (targetSdk 34 would otherwise fail install); `KeyboardAccessibilityService` is `exported="false"` + `BIND_ACCESSIBILITY_SERVICE` (system can still bind); `ExternalDisplayControlActivity` is `exported="false"` — important because it blindly `startActivity()`s the nested `EXTRA_LAUNCH_INTENT` (`ExternalDisplayControlActivity.java:121-141`), which would be an intent-redirection primitive if it were exported. `DebugInfoActivity`, `AppView`, `Game`, `StreamSettings`, `ProfilesActivity`, `EditProfileActivity`, `HelpActivity` are not exported. `HelpActivity` enables JavaScript and loads `getIntent().getData()` (`HelpActivity.java:53, 78`) but only the app launches it, with fixed GitHub URLs (`HelpLauncher.java:48-58`).
- **PendingIntents**: notification intent is explicit + `FLAG_IMMUTABLE` (`ExternalDisplayControlActivity.java:534-535`); the USB permission intent is `FLAG_MUTABLE` by necessity but explicit (`setPackage`) (`UsbDriverService.java:159-176`); dynamic receiver uses `RECEIVER_NOT_EXPORTED` on 33+ (`:308-313`).
- **FileProvider** (`provider_file_paths.xml`): very broad roots (`external-path .`, `files-path .`, `cache-path .`), but `grantUriPermissions` + `exported="false"` and the only grants are for `cache/artemistics_logs.txt` (performance stats: device model/OS/codec/bitrate — no host data) and the exported keyboard-layout JSON in `getExternalCacheDir()/export_settings` (`StreamSettings.java:778-842`). Narrowing `files-path` to a subfolder would be cleaner (`files/` holds `client.key`).
- **Local file inputs**: `.art` (`ShortcutTrampoline.java:311-351`, line-based, validated UUID/int), keyboard layout JSON → `JSONObject` → SharedPreferences (`StreamSettings.java:1001-1023`, `prefEditor.clear()` wipes the current layout prefs — functional risk only), special-button JSON → stored string → Gson `KeyConfigHelper.ShortcutFile` (plain POJOs, no polymorphic typing), profiles → Gson `ProfilesData`/`SettingsProfile` from app-private `files/profiles/profiles.json` (`ProfilesManager.java:67-97`; no import from user files). No `ObjectInputStream`/`Serializable`, no `DexClassLoader`, no `Runtime.exec` with user data (`DeviceUtils.java:382-386` runs the fixed `/system/bin/cat /proc/cpuinfo`).
- **Server/host-controlled strings reaching native**: `address`, `appversion`, `GfeVersion`, `rtspSessionUrl` go to `LiStartConnection` (`callbacks.c:465-471`); version parsing uses `strtol` (`Misc.c:87-93`) and the RTSP URL port uses `strtol` (`Connection.c:199`) — no `sscanf`/fixed buffers. `callbacks.c` resizes the decode buffer to `fullLength` (`:151-154`), Opus output buffer is `channelCount*samplesPerFrame` shorts matching the `opus_multistream_decode` frame size (`:226, :259-264`), `riAesKey/Iv` are `memcpy`'d as fixed 16 bytes from app-supplied arrays (`:487-493`). Fork-only native deltas vs og/master are the `receiveTimeMs/enqueueTimeMs` field names and a smaller `minisdl.c` (VID/PID tables) — nothing security-relevant.
- **Per-device `uniqueid` + `devicename`** (`NvHTTP.java:204-207, 474-481`): Artemis sends a stable per-install id (`java.util.Random`-generated, `IdentityManager.java:58`, identical class upstream but upstream overrides it with the constant `0123456789ABCDEF`) and the device model in cleartext to every host it polls — including unpaired mDNS-discovered hosts. Mild LAN trackability; required by Apollo's per-client features.
- **Tapjacking**: pairing dialogs (`utils/Dialog.java:66-100`, `PcView.java:584-626`) do not set `filterTouchesWhenObscured`. The normal flow only *displays* the PIN (typed on the PC), and the OTP dialog's EditTexts cannot be read by an overlay, so the practical exposure is nil; optional hardening.
- **Dead/unused dependencies**: `com.github.PhilJay:MPAndroidChart` has no references in `app/src/main/java` (only `proguard-rules.pro:46-48`); removing it shrinks the APK and attack surface. `org.opencv` is used only by `ReflectivePaddingInt8Minimal`/`Stereo3DRenderer`; the TFLite model is bundled (`assets/midas-midas-v2-w8a8.tflite`, loaded via `AssetFileDescriptor`, `Stereo3DRenderer.java:746-747`) — nothing is downloaded. `jcodec 0.2.5` parses host-provided H.264 SPS (`MediaCodecDecoderRenderer.java:1909-1911`, unchecked exceptions from a crafted SPS would crash via the "We will crash here" path in `callbacks.c:172-176`) — same as upstream.
- **ProGuard**: `-dontobfuscate` (same upstream); the added `-keep class org.opencv.** { *; }` / `org.tensorflow.lite.gpu.**` / `com.github.mikephil.charting.**` keep rules disable shrinking of those libraries (size, not security). `-keep class com.limelight.utils.KeyMapper {*;}` is required by the reflection in `GameMenu`.
- **GUI thread/unbounded reads**: `ResponseBody.string()` on `serverinfo`/`applist`/clipboard is unbounded (`NvHTTP.java:525-528`) — a host can push a very large body (OOM); box art is capped at 5 MB (`DiskAssetLoader.java:20, 165`). Shared with upstream except clipboard.
- **Functional**: `Game.serverCommands` can be `null` when started from `ShortcutTrampoline`'s running-game path (`ShortcutTrampoline.java:198-199` → `createStartIntent` with `computer.serverCommands` possibly null) → NPE at `GameMenu.java:304` when the user opens "Server commands".

---

## (C) What is done well

- Server-certificate pinning by DER equality on every HTTPS request, with no accept-all trust manager and HTTPS mandatory for all post-pairing operations (`NvHTTP.java:131-175`).
- Pairing protocol implemented faithfully to upstream: SHA-256 (Gen ≥ 7) salted-PIN key derivation, random 16-byte salt/challenge/secret from `SecureRandom`, server-signature verification, PIN-bound challenge check before releasing the client secret (`PairingManager.java:187-316`).
- 2048-bit RSA identity in app-private storage; no secrets in SharedPreferences; nothing sensitive logged in release builds.
- All SQLite access is parameterized (`ComputerDatabaseManager.java:85, 203-205, 223-225`).
- Host-supplied strings are never executed, used as file paths, URLs, intents or shell commands; server commands run by index only.
- Explicit intents and `FLAG_IMMUTABLE` PendingIntents; non-exported `Game`/`AppView`/`ExternalDisplayControlActivity` (the latter carries a nested launch intent); `RECEIVER_NOT_EXPORTED` where applicable.
- ML model shipped in assets (no download), no WebView exposed to external input, no dynamic code loading, no reflection into hidden APIs beyond the upstream Shield/Samsung shims.
- Clipboard sync is off by default and marks host-provided clips `IS_SENSITIVE` by default.
- Native JNI bridge sizes buffers from actual lengths (`callbacks.c:151-154, 226`), and the Artemis-specific native additions add no new parsing of host data.

---

## (D) Dependency / version table

| Component | This fork | og/master (moonlight-stream) | Used for | Known-vuln notes (from memory; verify) |
|---|---|---|---|---|
| OpenSSL (prebuilt static) | **1.1.1q** (2022-07-05), `libcrypto.a` + `libssl.a` linked | 4.0.2, `libcrypto.a` only | AES-128-GCM/CBC + `RAND_bytes` in moonlight-common-c | EOL since 2023-09-11; 1.1.1r–w fixed CVE-2023-0286, -0215, -0464/5/6, -2650, -3446, -3817, CVE-2022-4304, -4450 — none in the code paths used here (see L7). Flagging as unmaintained rather than exploitable. |
| libopus (prebuilt static) | 1.5.2 (string in `libopus.a`) | version string not extractable from og blob (binary differs) | Opus multistream decode of host audio (`callbacks.c:259`) | No decoder CVE known to me for 1.5.2 (unsure; check opus-codec.org security page). |
| moonlight-common-c | `c999436` (ClassicOldSong fork; merge-base `5f22801`) | `874ac95` | Entire stream stack | 44 upstream commits behind, incl. RTSP hardening `7b026e7` (M4). |
| enet (submodule of common-c) | `115a10b` (2025-07-04) | `aca8784` | Control stream | No CVE known; two upstream bumps missing. |
| reedsolomon `rs.c` | present (upstream replaced by nanors) | nanors `b1e3c22` | FEC decode of host packets | Older RS implementation; upstream's replacement also fixed RFI/IDR handling bugs (`be43885`, `d85371c`). |
| bcprov / bcpkix | 1.81 / 1.81 | 1.85.2 / 1.85 | Generating/loading own cert+key, AES-ECB for pairing | BC ASN.1 parsers only see the app's own files (server cert is parsed by the platform `CertificateFactory`, `PairingManager.java:78`). CVE-2025-8885 (ASN.1 allocation DoS) affected bc-java ≤ 1.79 per my recollection — unsure whether 1.81 is clean; upgrade anyway. |
| okhttp | 4.12.0 | 5.5.0 | All HTTP(S) to hosts | No applicable client CVE known; TLS trust is fully custom here. |
| jmdns | 3.6.2 | 3.6.3 | mDNS on API < 34 | None known. |
| gson | 2.13.1 | n/a upstream | Profiles, shortcut JSON (POJOs only) | Clean (CVE-2022-25647 fixed in 2.8.9). |
| jcodec | 0.2.5 | 0.2.5 | H.264 SPS parsing/patching of host CSD | Unmaintained; crash-only risk on crafted SPS (same as upstream). |
| opencv | 4.12.0 | n/a | Stereo-3D padding of local frames | None applicable; input is local decoded frames. |
| litert / litert-gpu | 1.4.0 | n/a | Depth model (bundled asset) | None applicable. |
| MPAndroidChart | v3.1.0 | n/a | **Unused** (no Java references) | Remove. |
| SearchPreference | v2.5.1 | n/a | Settings search | None known. |
| ShieldControllerExtensions | 1.0.1 | 1.0.1 | Shield input | — |
| AGP / NDK / SDK | AGP 8.13, NDK 27.0.12077973, compileSdk 36, targetSdk 34, minSdk 21 | NDK 29.0.14206865, compileSdk 37, targetSdk 36 | — | targetSdk 34 is below Play's 2026 requirement (upstream is on 36); targetSdk ≥ 35 would also enforce 16 KB page alignment checks (`APP_SUPPORT_FLEXIBLE_PAGE_SIZES` is already set). |
