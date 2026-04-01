#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADDON_DIR="$ROOT_DIR/unreal_vat_bridge"
DIST_DIR="$ROOT_DIR/dist"
ZIP_PATH="$DIST_DIR/unreal_vat_bridge.zip"

if ! command -v zip >/dev/null 2>&1; then
  echo "Error: 'zip' command was not found." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: 'python3' command was not found." >&2
  exit 1
fi

if [[ ! -f "$ADDON_DIR/__init__.py" ]]; then
  echo "Error: addon entry point was not found at '$ADDON_DIR/__init__.py'." >&2
  exit 1
fi

python3 - <<'PY' "$ADDON_DIR"
from pathlib import Path
import sys

addon_dir = Path(sys.argv[1])
bad_files = []

for path in sorted(addon_dir.glob("*.py")):
    if path.read_bytes().startswith(b"\xef\xbb\xbf"):
        bad_files.append(path.name)

if bad_files:
    print("Error: UTF-8 BOM found in:", file=sys.stderr)
    for name in bad_files:
        print(f"  - {name}", file=sys.stderr)
    sys.exit(1)
PY

mkdir -p "$DIST_DIR"
rm -f "$ZIP_PATH"

(
  cd "$ROOT_DIR"
  COPYFILE_DISABLE=1 zip -r "$ZIP_PATH" "unreal_vat_bridge" \
    -x '*/__pycache__/*' \
       '*/.DS_Store' \
       '*/workflow.py.bak' \
       '*/assets/*.blend1' \
       '*/__MACOSX/*' \
       '*.pyc'
)

echo "Created: $ZIP_PATH"
