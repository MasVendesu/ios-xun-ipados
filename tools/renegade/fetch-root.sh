#!/bin/bash
# Download and decrypt HakiOS / iOS 26 root dmg + SystemOS + AppOS cryptex dmgs.
# Uses dynamic manifest parsing in tools/renegade/fetch_root.py
set -euo pipefail
cd "$(dirname "$0")/../.."
exec python3 tools/renegade/fetch_root.py "$@"
