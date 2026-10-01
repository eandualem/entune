# Interface design

Entune is used throughout the day for dictation. Keep recording, history,
dictionary review and settings easy to find, with controls grouped by the task
rather than by implementation detail. Provider failures remain visible, and
transcripts should be easy to read, select and copy.

Use a quiet appearance: restrained colour, clear spacing and subtle separation
between surfaces. Prefer short labels, hints and nearby captions to long
instructions in the main view. Keep frequent actions reachable in a narrow
window.

The interface uses plain HTML, CSS and JavaScript without a framework or build
step, system fonts and inline SVG icons. It runs in the macOS webview and in a
browser. Light and dark themes follow the system or the selected setting; text
size is configurable. Shared colours, sizes and depth are defined in
`src/entune/web/tokens.css`.

The native recording indicator remains separate from the page and shows the
current dictation stage without taking focus from the target application.

For current behavior, see the [user guide](../README.md),
[dictionary reference](dictionary.md) and [architecture](architecture.md).
