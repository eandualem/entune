#!/bin/sh
# Build the Entune.app launcher (Apple Silicon and Intel) into the package's assets.
# The committed binary is what every installation copies; rebuild only when launcher.m
# changes, because a different binary means macOS asks every user for permissions again.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out="$here/../../src/entune/assets/EntuneLauncher"
clang -fobjc-arc -Os -Wall -Wextra -Werror \
  -arch arm64 -arch x86_64 -mmacosx-version-min=11.0 \
  -framework AppKit "$here/launcher.m" -o "$out"
strip -x "$out"
echo "wrote $out"
