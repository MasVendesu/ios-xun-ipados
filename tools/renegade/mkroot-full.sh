#!/bin/bash
# Build the full HakiOS / iOS 26 root image. No sudo.
#   tools/renegade/fetch-root.sh        # (once) download + decrypt the three dmgs
#   tools/renegade/mkroot-full.sh [mkroot-full.py options, e.g. --delete-sources --size 20g --stages copy,macho,...]
# -> out/userdata.simg
set -euo pipefail
cd "$(dirname "$0")/../.."
exec python3 -I tools/renegade/mkroot-full.py "$@"
