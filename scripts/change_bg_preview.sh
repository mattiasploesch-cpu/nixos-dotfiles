#!/usr/bin/env bash
set -euo pipefail

img="${1:-}"
cols="${FZF_PREVIEW_COLUMNS:-80}"
lines="${FZF_PREVIEW_LINES:-40}"
left="${FZF_PREVIEW_LEFT:-0}"
top="${FZF_PREVIEW_TOP:-0}"
sock="${CHANGE_BG_UEBERZUG_SOCKET:-}"

remove_preview() {
  if [ -n "$sock" ] && [ -S "$sock" ]; then
    ueberzugpp cmd -s "$sock" -a remove -i change_bg_preview >/dev/null 2>&1 || true
  fi
}

if [ -z "$img" ] || [ ! -f "$img" ]; then
  remove_preview
  exit 0
fi

# Echte Bildvorschau über ein X11-Overlay. Das ist kein ASCII-Pixelbrei.
if [ -n "$sock" ] && [ -S "$sock" ]; then
  if ueberzugpp cmd -s "$sock" -a add -i change_bg_preview \
      -f "$img" -x "$left" -y "$top" \
      --max-width "$cols" --max-height "$lines" >/dev/null 2>&1; then
    exit 0
  fi
fi

# Fallback, falls kein X11/ueberzugpp verfügbar ist.
chafa --format symbols --size "${cols}x${lines}" "$img"
