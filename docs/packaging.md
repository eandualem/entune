# Packaging Dictum.app

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
