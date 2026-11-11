#!/bin/bash
# Boot image for the FULL HakiOS / iOS 26 root on the userdata partition (rd=ufsroot0), no RAM disk.
#   tools/renegade/mkboot-full.sh [KERNEL] [ADT] [OUT.img]
# defaults: out/mach.development.bcm2837, sapphire.adt, out/enxerto-boot.img
# Test it with `fastboot boot OUT.img` (the phone must hold the image from tools/renegade/mkroot-full.sh
# flashed to userdata). Nothing is flashed or booted here.
set -euo pipefail
cd "$(dirname "$0")/../.."
KERNEL=${1:-out/mach.development.bcm2837}
ADT=${2:-sapphire.adt}
OUT=${3:-out/enxerto-boot.img}
CMDLINE=${CMDLINE:-"debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam sweetearlyfb=1 sweethidtest=3 rd=ufsroot0 ufsrw=1 rootrw=1 unentitled_ios_sim_launch=1 ignition_level=1 launchd_unsecure_cache=1 wdt=-1"}
mkdir -p enxerto/out
rm -f enxerto/applelogo.h
if [ "${APPLE_LOGO:-1}" != 0 ]; then
	ROOT_SIMG=${ROOT_SIMG:-out/userdata.simg}
	if [ -e "$ROOT_SIMG" ]; then
		if [ -n "${APPLE_LOGO_PNG:-}" ]; then LOGO_SRC=(--png "$APPLE_LOGO_PNG"); else LOGO_SRC=(--root "$ROOT_SIMG"); fi
		python3 tools/mkapplelogo.py "${LOGO_SRC[@]}" || echo "warning: no Apple logo (Bergamota screen stays)" >&2
	fi
fi
python3 tools/mkpayload.py "$KERNEL" "$ADT" enxerto/out/payload.bin --cmdline "$CMDLINE"
BOARD=sapphire enxerto/build.sh "$PWD/enxerto/out/payload.bin"
cp enxerto/out/enxerto-boot.img "$OUT"
ls -l "$OUT"
echo "cmdline: $CMDLINE"
