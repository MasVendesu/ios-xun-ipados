#!/bin/bash
# Mount the iPadOS volumes of an IPSW for the root image build, on Linux.
#
#   tools/xun-mount-ipsw.sh IPSW MOUNTROOT        (needs the `ipsw` CLI, blacktop)
#
# OS and Cryptex1,SystemOS are AEA1 (encrypted) in iPadOS 26: `ipsw mount` asks the
# WKMS for the FCS key and mounts the decrypted volume. `ipsw mount` needs the whole
# IPSW file, not range reads, which is why this takes the file and not a URL.
#
# MOUNTROOT gets two subdirectories, os/ and sys/; AppOS is a plain DMG and is taken
# with the ordinary DMG path of tools/mkiosroot.sh. Nothing is written to the IPSW.
set -euo pipefail
ipsw=${1:?usage: $0 IPSW MOUNTROOT}
out=${2:?usage: $0 IPSW MOUNTROOT}
[ -f "$ipsw" ] || { echo "no such IPSW: $ipsw" >&2; exit 2; }
command -v ipsw >/dev/null || { echo "the ipsw CLI (blacktop) is not on PATH" >&2; exit 2; }

mkdir -p "$out/os" "$out/sys"
# --device picks the identity when the IPSW carries several (it is one device family)
ipsw mount fs --device 'iPad8,1' --mount-point "$out/os" "$ipsw"
ipsw mount sys --device 'iPad8,1' --mount-point "$out/sys" "$ipsw"
echo "mounted; unmount with: ipsw unmount --all"
