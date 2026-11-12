#!/bin/bash
# Build the iPadOS 26 root image for the Redmi Pad SE (xun) from an iPad8,1 IPSW.
#
#   tools/xun-build-ipados-root.sh IPSW OUT.simg [SIZE]      (SIZE default 22G, raw image)
#
# Why not tools/mkiosroot.sh: iPadOS 26 ships OS and SystemOS as AEA1 (encrypted);
# only `ipsw mount` can decrypt and mount them, and it needs the whole IPSW file.
# The layout mkiosroot.sh produces is reproduced here (cryptexes first under
# /private/preboot/Cryptexes, OS root on top), with the same SWEET additions.
#
# Host needs: the ipsw CLI (blacktop), apfs-fuse, hfsprogs, rsync, a kernel with
# the hfsplus module, losetup and /dev/loop*. Run as root.
set -euo pipefail
ipsw=${1:?usage: $0 IPSW OUT.simg [SIZE]}
out=${2:?usage: $0 IPSW OUT.simg [SIZE]}
size=${3:-22G}
raw=${out%.simg}.img
work=$(mktemp -d)
mnt=$work/root
mounts=()
cleanup() {
	sync
	for m in "${mounts[@]}"; do umount "$m" 2>/dev/null || true; done
	rm -rf "$work"
}
trap cleanup EXIT

for t in ipsw apfs-fuse mkfs.hfsplus rsync losetup; do
	command -v "$t" >/dev/null || { echo "missing tool: $t" >&2; exit 2; }
done
grep -q hfsplus /proc/filesystems || { echo "kernel has no hfsplus" >&2; exit 2; }

mkdir -p "$work/os" "$work/sys" "$work/app"

# AEA decryption fails now and then with a bad segment HMAC (the key comes from the
# WKMS per request); a second or third attempt gets through.
mount_retry() {
	local what=$1 at=$2 i pid
	# ipsw does not always return after a successful mount, so it runs in the
	# background and success is judged by the mount point filling up.
	for i in 1 2 3 4 5; do
		ipsw mount "$what" --device iPad8,1 --mount-point "$at" "$ipsw" >/dev/null 2>&1 &
		pid=$!
		for _ in $(seq 1 900); do
			if [ -n "$(ls -A "$at" 2>/dev/null)" ]; then
				mounts+=("$at")
				return 0
			fi
			kill -0 $pid 2>/dev/null || break
			sleep 1
		done
		kill $pid 2>/dev/null || true
		echo "  mount $what attempt $i failed, retrying" >&2
		rm -rf /tmp/090-* 2>/dev/null || true
		sleep 3
	done
	echo "could not mount $what from the IPSW" >&2
	return 1
}

mount_retry fs  "$work/os"
mount_retry sys "$work/sys"
# AppOS is a plain DMG inside the IPSW; the OS .dmg is already extracted by ipsw
app_dmg=$(python3 - "$ipsw" <<'PY'
import plistlib, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    m = plistlib.loads(z.read('BuildManifest.plist'))
ident = next(i for i in m['BuildIdentities'] if i['Info'].get('RestoreBehavior') == 'Erase')
print(next(v['Info']['Path'] for k, v in ident['Manifest'].items() if k == 'Cryptex1,AppOS'))
PY
)
unzip -o -q "$ipsw" "$app_dmg" -d "$work/appdmg"
apfs-fuse "$work/appdmg/$app_dmg" "$work/app"
mounts+=("$work/app")

truncate -s "$size" "$raw"
mkfs.hfsplus -s -J -v iOSRoot "$raw"
mkdir -p "$mnt"
mount -o loop,force "$raw" "$mnt"
mounts+=("$mnt")

# cryptexes first, the OS root on top
mkdir -p "$mnt/private/preboot/Cryptexes/OS" "$mnt/private/preboot/Cryptexes/App" \
         "$mnt/private/preboot/Cryptexes/Supplemental"
rsync -aH "$work/sys/root/" "$mnt/private/preboot/Cryptexes/OS/"
rsync -aH "$work/app/root/" "$mnt/private/preboot/Cryptexes/App/"
rsync -aH "$work/os/root/"  "$mnt/"

# the shared cache where ignition_level=1 looks, hard links (no extra space)
dsc=$mnt/private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld
mkdir -p "$mnt/System/Library/Caches/com.apple.dyld"
for f in "$dsc"/*; do [ -f "$f" ] && ln "$f" "$mnt/System/Library/Caches/com.apple.dyld/${f##*/}" 2>/dev/null || true; done
mkdir -p "$mnt/System/Cryptexes"
ln -sfn /private/preboot/Cryptexes/OS "$mnt/System/Cryptexes/OS"
ln -sfn /private/preboot/Cryptexes/App "$mnt/System/Cryptexes/App"

# plain-plist system keybag (keybagd reads it with /defaults/no-effaceable-storage)
mkdir -p "$mnt/private/var/keybags"
printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' \
	'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
	'<plist version="1.0"><dict><key>KeyBagKeys</key><data>U1dFRVQ=</data></dict></plist>' \
	> "$mnt/private/var/keybags/systembag.kb"
chmod 600 "$mnt/private/var/keybags/systembag.kb"

# first-boot skips: Setup, migration, the daemons that need hardware we do not have
python3 "$(dirname "$0")/sweet-root-tweaks.py" "$mnt"
python3 - "$mnt" <<'PY'
import plistlib, sys, pathlib
p = pathlib.Path(sys.argv[1], 'private/var/mobile/Library/Preferences/com.apple.migration.plist')
d = plistlib.loads(p.read_bytes())
d.update(LastSystemVersion='23A341',
         DMUserDataDisposition={'SyntheticDidUpgrade': True, 'buildVersion': '23A341'})
p.write_bytes(plistlib.dumps(d))
PY
chmod 755 "$mnt/private/var"; chmod 700 "$mnt/private/var/mobile"

sync
umount "$mnt"
fsck.hfsplus -f -n "$raw"
img2simg "$raw" "$out"
rm -f "$raw"
sha256sum "$out" | tee "$out.sha256"
echo "built $out"
