# Packaging Entune.app

Two ways. The launcher bundle is what every installation uses; the standalone
bundle is a self-contained build for developers.

## The launcher: `entune` and `entune install-app`

Plain `entune` on macOS, and `entune install-app`, write
`/Applications/Entune.app` when that folder is writable, else
`~/Applications/Entune.app` (or `--into DIR`): an Info.plist with Entune's
name, bundle id `dev.elias.dictum` (macOS ties the granted permissions to it),
`LSUIElement`, the microphone usage string and the packaged `assets/Entune.icns`;
and as its executable the native launcher `assets/EntuneLauncher`.

The launcher reads the command to run from its preferences (`LaunchCommand`,
written by `entune`: this installation's Python with `-m entune`), starts it as
its child and stays its parent. macOS charges a child's Microphone, Input
Monitoring and Accessibility use to the app it came from, so the permissions
belong to Entune rather than Python or a terminal. A second open brings the
window forward (`POST /api/window`); quitting the launcher asks Entune to quit
first, so audio is saved.

The bundle is signed ad hoc on every Mac, and none of its files depend on the
Entune version, so every installation and upgrade has the same code identity
and the permissions stay granted without any certificate.

The launcher's source is `packaging/app-launcher/launcher.m`; build it with
`packaging/app-launcher/build.sh` (Xcode command line tools, Apple Silicon and
Intel in one binary). Rebuild only when the source changes: a different binary
is a new identity, and every user would grant the permissions once more.

On Windows, `entune` adds an Entune entry to the Start menu instead, running
this installation with `pythonw.exe` (no console) and `assets/Entune.ico`.

## Signing a standalone build, and keeping the permissions

`install-app --from` signs the standalone bundle it installs. Ad hoc by default,
which ties the grants to the exact binary: after every rebuild macOS forgets
Microphone, Input Monitoring and Accessibility and asks again (seen 2026-09-18,
issue #51).

To keep them across rebuilds, create a certificate once. Four steps in
Keychain Access, as it took on macOS 26 (2026-09-18):

1. Certificate Assistant › Create a Certificate…: name **Entune Developer**
   exactly (one named **Dictum Developer** is also used when there is no Entune
   one), identity type Self Signed Root, certificate type **Code
   Signing** (the popup defaults to S/MIME; that one cannot sign code).
2. My Certificates › double-click it › Trust › Code Signing: **Always
   Trust**. Without this `security find-identity -v -p codesigning` lists
   no valid identity.
3. Keys › the private key "Entune Developer" › double-click › Access
   Control › **Allow all applications to access this item** › Save.
   Without this `codesign` fails with `errSecInternalComponent`, and its
   password dialog rejects the correct password.
4. Trust it system-wide, from a terminal (asks for your password):

   ```sh
   security find-certificate -c "Entune Developer" -p > /tmp/entune-developer.cer
   sudo security add-trusted-cert -d -r trustRoot -p codeSign \
     -k /Library/Keychains/System.keychain /tmp/entune-developer.cer
   ```

   Step 2 trusts the certificate only in your login keychain. Input
   Monitoring and Accessibility are checked by a system daemon that does
   not see it, so it treats the signed app as untrusted and the grants
   never take, however often they are toggled; the Microphone check is
   per user and works. Seen 2026-09-18 on macOS 26.

When that certificate exists, `install-app --from` signs with it instead, every
build has the same identity (`codesign -d -r- Entune.app` shows
`certificate leaf = H"…"` rather than `cdhash`), and the grants stay.
Nothing else changes; this is not Developer ID and does not help other
Macs.

If signing with the certificate fails, `install-app` stops with codesign's
error and leaves the installed app unchanged. It also refuses an update that
does not satisfy the installed app's certificate identity, including when the
certificate is missing or inaccessible and only ad-hoc signing is available.
Restore access to the same certificate and private key in Keychain Access,
then retry. Do not create another certificate with the same name: its identity
would still be different.

Quit Entune before installing an update. Use `install-app --from` rather than
dragging a freshly built bundle over the existing app: the installer signs it
and checks that a certificate-backed permission identity is preserved.
The build output alone is signed ad hoc. Local signing does not provide
Developer ID signing or notarisation for distribution to other Macs.

## A standalone bundle with PyInstaller

macOS attaches the Microphone, Input Monitoring and Accessibility
permissions to an application. Run as a plain Python process, that
application is whichever Python binary launched `entune`, so the permissions
show up as "python3" and are asked again whenever the interpreter changes.
`Entune.app` gives macOS a stable identity, with Entune's icon.

```sh
uv sync --group build
uv run --group build python packaging/build_app.py
```

`packaging/Entune.spec` is the PyInstaller spec: a menu-bar-only bundle
(`LSUIElement`), bundle id `dev.elias.dictum`, version from the package, the
microphone usage string, the web page and assets, the Parakeet helper
script, and the whisper.cpp libraries (collected with the bindings) inside,
`Entune.icns` as the icon. The bundle is about 110 MB. The Parakeet engine
itself (MLX, about 480 MB) is deliberately not bundled; it is installed
with `uv tool install parakeet-mlx` and found at run time. The build writes
`dist/Entune.app`; `entune install-app --from dist/Entune.app` copies and
signs it.

It is the same program as `entune`: same entry point, same data directory.
If another Entune is already running on the port, the new one says so and
quits rather than answering the shortcut twice.

Not done: Developer ID signing and notarisation, which are needed only to
hand the app to other Macs without Gatekeeper warnings.

## Cocoa compatibility checks

`desktop/macos/webview.py` owns Entune's pywebview adaptations. It installs
Objective-C methods once per process and binds them to the active window only
while the shell runs. After teardown, capture is denied and file selection
completes with no selection. Tested dependency versions are pywebview 6.2.1 and
PyObjC 12.2.2; moving these hooks into a module does not remove upstream coupling.

Upgrade checks must cover these specific dependencies:

- `BrowserView.BrowserDelegate` media selector
  `webView:requestMediaCapturePermissionForOrigin:initiatedByFrame:type:decisionHandler:`
  (`v@:@@@q@?`): grant only microphone requests by our known WebView's main frame
  on the configured local origin. Camera, other origins/ports, subframes and
  unknown views are denied. macOS's separate microphone permission still applies.
- `webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:`
  (`v@:@@@@?`): use WebKit's directory flag, the requesting instance in
  `BrowserView.instances`, and `create_file_dialog(..., main_thread=True)`.
  Ordinary files retain upstream's private `_acceptedMIMETypes()` dependency;
  folders do not need it. Selection, cancellation and failure must each complete
  the handler once. Compatibility failures log and notify instead of hanging.
- `BrowserView.AppDelegate.applicationShouldTerminate:` uses the upstream
  `I@:@` signature. Cmd-Q/menu Quit/logout runs the desktop owner's capture
  flush and service cleanup **before** returning `NSTerminateNow`; AppKit can
  terminate without running Python `finally`. A quit exception cancels native
  termination. Tray Quit uses the same owner, then permits window destruction;
  ordinary Close continues to hide. Capture waiting is three seconds, followed
  by the service's bounded cleanup, not a claim that microphone/OS calls have a
  hard total deadline. Unfinished drain is reported, and no late paste is allowed.
- Frameless title-bar layout depends on `standardWindowButton_`, its superview
  and the parent container's frames. Check all three traffic-light controls,
  resize/text size, drag area and full-screen entry/exit. A changed hierarchy
  reports an error; full-screen positioning remains AppKit's responsibility.
- `ALLOW_DOWNLOADS` delegates to pywebview's Cocoa download handling and native
  Save panel. Verify audio ZIP, transcript JSON and individual audio downloads,
  including cancel. No custom download delegate is installed by Entune.

Run the normal gates and `tests/test_webview.py` for selector registration,
origin/type gating, picker callback completion, title-bar failure handling,
close/destroy and capture/cleanup order. `tests/test_app.py` covers native
permission recovery and capture delivery without changing system grants.
Then use a separately launched signed bundle, an unused port and an isolated
`--data` directory for native checks. Never quit the user's running instance,
reset their permissions, or use private recordings/API keys in a smoke test.
Automated callback checks do not establish real permission-dialog or Save-panel
interaction; record which native interactions were actually exercised.

Check that the installed bundle contains the web assets, all prompt resources,
and `entune/providers/local/parakeet_helper.py`. Verify the helper path resolves
inside the bundle while `engine_python()` resolves outside it to the separately
installed engine. Test its protocol without downloading weights or transcribing
private audio; run real engine/inference tests only with an explicit test corpus.

Windows has no native shortcut/paste/indicator/permission/lifecycle implementation
or supported desktop package yet. Its data-directory branch and portable WebView
libraries do not change that; Windows native work is tracked separately in #36.

## Bundle identity

The bundle id is `dev.elias.dictum` and the app is signed with an existing
**Dictum Developer** certificate when there is no **Entune Developer** one.
macOS ties Microphone, Input Monitoring and Accessibility to that identity, so
keeping it keeps the granted permissions; a new identity means granting all
three again.
