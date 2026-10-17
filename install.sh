#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v pactl >/dev/null; then
  echo "error: pactl not found (PipeWire/PulseAudio required)" >&2
  exit 1
fi
if ! command -v avahi-browse >/dev/null; then
  echo "warning: avahi not found; install it with: omarchy pkg add avahi" >&2
fi
if ! python3 -c "import gi" 2>/dev/null; then
  echo "error: python-gobject not found" >&2
  exit 1
fi

PYTHONPATH="$DIR" exec python3 -m omarchy_cast install "$@"
