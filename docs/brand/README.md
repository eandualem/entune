# Entune brand assets

The mark is a speech-bubble ring with a folded tail at the lower right, around five
rounded bars (short, medium, tall, medium, short). Every file here, and the app's
icons, come from one generator:

```sh
uv run python tools/brand.py
```

Edit `tools/brand.py`, not the exported files, then run it again.

| File | Use |
|---|---|
| `entune-app-icon.svg` | App icon: white artwork on a flat lavender `#a99cf2` tile |
| `entune-mark-light.svg` | Mark alone, violet `#6c5cd8`, for light backgrounds |
| `entune-mark-dark.svg` | Mark alone, lavender `#a99cf2`, for dark backgrounds |
| `entune-logo-light.svg` | Mark and "Entune" wordmark for light backgrounds |
| `entune-logo-dark.svg` | Mark and wordmark for dark backgrounds |

The generator also writes the app's own copies: `packaging/Entune.icns`,
`src/entune/assets/icon.png` and `icon-512.png` (Dock and launcher),
`src/entune/assets/menubar-template.png` (a black template image that macOS tints for a
light or dark menu bar), `src/entune/web/favicon.svg` and `docs/demo/icon-256.png`.

Rules: no gradients, glows or shadows; the mark alone is violet on light backgrounds
and lavender or white on dark ones, never white on white.

The wordmark is outlined from Inter Bold (SIL Open Font License 1.1, by Rasmus
Andersson), so no font is needed to display it.
