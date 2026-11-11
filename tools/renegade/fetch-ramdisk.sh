#!/bin/bash
# Fetch the HakiOS / iOS 26 restore ramdisk of an IPSW and mount it read-only.
#
#   tools/renegade/fetch-ramdisk.sh [-u IPSW_URL] [-d WORKDIR]      (default WORKDIR research/ios26/ramdisk)
set -euo pipefail
cd "$(dirname "$0")/../.."
URL=; WORK=research/ios26/ramdisk
while getopts u:d: o; do case $o in u) URL=$OPTARG;; d) WORK=$OPTARG;; *) exit 2;; esac; done
[ -n "$URL" ] || URL=$(ipsw download ipsw --device iPhone14,7 --version 26.0 --urls | tail -1)
mkdir -p "$WORK"
python3 tools/remotezip.py "$URL" BuildManifest.plist "$WORK" >/dev/null
RD=$(python3 -I - "$WORK/BuildManifest.plist" <<'P'
import plistlib, sys
m = plistlib.load(open(sys.argv[1], 'rb'))['BuildIdentities'][0]['Manifest']
print(m['RestoreRamDisk']['Info']['Path'])
P
)
echo "restore ramdisk member: $RD"
python3 tools/remotezip.py "$URL" "$RD" "$WORK" >/dev/null
ipsw img4 im4p extract "$WORK/$RD" -o "$WORK/ramdisk.apfs" >/dev/null
MNT=$PWD/$WORK/mnt
mkdir -p "$MNT"
hdiutil attach -readonly -nobrowse -noverify -imagekey diskimage-class=CRawDiskImage -mountpoint "$MNT" "$WORK/ramdisk.apfs" >/dev/null
echo "mounted read-only at $MNT"
ls "$MNT/usr/lib/dyld" "$MNT/bin"
