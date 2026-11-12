#!/bin/bash
# Build the fastboot boot image for the Redmi Pad SE (xun): kernel + xun ADT + cmdline.
#
#   tools/xun-mkboot.sh KERNEL OUT.img [--md0]     (default: eMMC root)
#
# --md0 puts a mockfs RAM disk on the root instead, for a storage-free smoke test.
# Nothing is flashed or booted here.
set -euo pipefail
kernel=${1:?usage: $0 KERNEL OUT.img [--md0]}
out=${2:?usage: $0 KERNEL OUT.img [--md0]}
here=$(cd "$(dirname "$0")/.." && pwd)
adt=$here/xun.adt
[ -f "$adt" ] || { echo "missing $adt" >&2; exit 2; }
payload=$(mktemp)
trap 'rm -f "$payload"' EXIT

common="-v debug=0xc serial=3 cpus=4 keepsyms=1 -enable_kprintf_spam sweetearlyfb=1 ipc_legacy=1 cs_enforcement_disable=1 amfi_get_out_of_my_way=1 rootless=0"
if [ "${3:-}" = --md0 ]; then
	rd=$(mktemp)
	python3 "$here/tools/mkramdisk.py" --format mockfs --arch arm64e "$rd"
	python3 "$here/tools/patch_userland_pac.py" "$rd"
	python3 "$here/tools/mkpayload.py" --compress --ramdisk "$rd" \
		--cmdline "$common rd=md0" "$kernel" "$adt" "$payload"
	rm -f "$rd"
else
	python3 "$here/tools/mkpayload.py" --compress \
		--cmdline "$common ignition_level=1 unentitled_ios_sim_launch=1 launchd_unsecure_cache=1 wdt=-1 rd=emmcroot0 emmcroot=userdata rootrw=1" \
		"$kernel" "$adt" "$payload"
fi
BOARD=xun python3 "$here/enxerto/build.py" --board xun --payload "$payload" --skip-host-tests
cp "$here/enxerto/out/enxerto-boot.img" "$out"
ls -l "$out"
