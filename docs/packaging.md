# Packaging Dictum.app

Two ways. The standalone bundle is the sure one; the launcher bundle is
lighter but macOS's permission panels may refuse to list it.

## `dictum install-app`

Writes `/Applications/Dictum.app` when that folder is writable, else
`~/Applications/Dictum.app` (or `--into DIR`): an Info.plist with
Dictum's name, bundle id `dev.elias.dictum`, `LSUIElement`, the microphone
usage string and an icns built from the shipped PNG with `sips` and
`iconutil`; and an executable that is a two-line shell script running the
current Python with `-m dictum`. Nothing is copied, so the app follows the
installation it was created from: upgrade `dictum` and the app is upgraded.
Observed on macOS 26: the unsigned first version was not accepted by the
Input Monitoring and Accessibility panels; if that happens, use the
standalone bundle below and `dictum install-app --from dist/Dictum.app` to
put it in place.

## Signing, and keeping the permissions

`install-app` signs whatever it installs. Ad hoc by default, which is
enough for the permission panels but ties the grants to the exact binary:
after every rebuild macOS forgets Microphone, Input Monitoring and
Accessibility and asks again (seen 2026-09-18, issue #51).

To keep them across rebuilds, create a certificate once. Three steps in
Keychain Access, as it took on macOS 26 (2026-09-18):

1. Certificate Assistant › Create a Certificate…: name **Dictum Developer**
   exactly, identity type Self Signed Root, certificate type **Code
   Signing** (the popup defaults to S/MIME; that one cannot sign code).
2. My Certificates › double-click it › Trust › Code Signing: **Always
   Trust**. Without this `security find-identity -v -p codesigning` lists
   no valid identity.
3. Keys › the private key "Dictum Developer" › double-click › Access
   Control › **Allow all applications to access this item** › Save.
   Without this `codesign` fails with `errSecInternalComponent`, and its
   password dialog rejects the correct password.

When that certificate exists, `install-app` signs with it instead, every
build has the same identity (`codesign -d -r- Dictum.app` shows
`certificate leaf = H"…"` rather than `cdhash`), and the grants stay.
Nothing else changes; this is not Developer ID and does not help other
Macs.

Two things the assistant did not do on this machine (macOS 26) and that
`install-app` reports when they bite: the certificate was not trusted for
code signing (open it in Keychain Access, expand Trust, set Code Signing to
Always Trust), and the first `codesign` with it must run from a terminal
you are looking at, so macOS can ask whether codesign may use the key;
choose Always Allow. `install-app` falls back to ad hoc and prints the
error when signing with the certificate fails, rather than leave a
half-signed bundle.

## A standalone bundle with PyInstaller

macOS attaches the Microphone, Input Monitoring and Accessibility
permissions to an application. Run as a plain Python process, that
application is whichever Python binary launched `dictum`, so the permissions
show up as "python3" and are asked again whenever the interpreter changes.
`Dictum.app` gives macOS a stable identity, with Dictum's icon.

```sh
uv sync --group build
uv run --group build python packaging/build_app.py
```

`packaging/Dictum.spec` is the PyInstaller spec: a menu-bar-only bundle
(`LSUIElement`), bundle id `dev.elias.dictum`, version from the package, the
microphone usage string, the web page and assets inside, `Dictum.icns` as
the icon. The build writes `dist/Dictum.app`; drag it to /Applications.

It is the same program as `dictum`: same entry point, same data directory.
If another Dictum is already running on the port, the new one says so and
quits rather than answering the shortcut twice.

Not done: Developer ID signing and notarisation, which are needed only to
hand the app to other Macs without Gatekeeper warnings.
