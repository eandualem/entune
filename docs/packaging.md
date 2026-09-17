# Packaging Dictum.app

Two ways. The first is what most people want.

## `dictum install-app`

Writes `~/Applications/Dictum.app` (or `--into DIR`): an Info.plist with
Dictum's name, bundle id `dev.elias.dictum`, `LSUIElement`, the microphone
usage string and an icns built from the shipped PNG with `sips` and
`iconutil`; and an executable that is a two-line shell script running the
current Python with `-m dictum`. Nothing is copied, so the app follows the
installation it was created from: upgrade `dictum` and the app is upgraded.
macOS attaches the three permissions to this bundle.

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
