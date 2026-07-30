#!/usr/bin/env bash
set -euo pipefail

VERSION=$(python3 -c "import re; print(re.search(r'version=[\"\\x27]([^\"\\x27]+)', open('plugin/setup.py').read()).group(1))")
OUT="hostpanel-storage-${VERSION}.zip"

echo "Building ${OUT}..."
rm -f "$OUT"

python3 - "$OUT" <<'PYEOF'
import os
import sys
import zipfile

out = sys.argv[1]
folders = ["plugin", "bin", "conf", "service", "sudoers", "frontend"]
skip_dirs = {"__pycache__"}

with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for file in files:
                if file.endswith(".pyc") or file.startswith("."):
                    continue
                path = os.path.join(root, file)
                zf.write(path, path.replace(os.sep, "/"))
PYEOF

echo "Done -> ${OUT}"
