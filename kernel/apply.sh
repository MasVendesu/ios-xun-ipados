#!/bin/bash
# Make the dada kernel tree: Apple's xnu-12377.121.6 with xnu-12377.121.6-dada.patch applied.
#   kernel/apply.sh DIR      (DIR is created; then build it the way base/xnu-build.sh does)
set -euo pipefail
K=$(cd "$(dirname "$0")" && pwd)
D=${1:?usage: apply.sh DIR}
git clone --depth 1 --branch xnu-12377.121.6 https://github.com/apple-oss-distributions/xnu.git "$D"
git -C "$D" apply --whitespace=nowarn "$K/xnu-12377.121.6-dada.patch"
if [ -f "$K/0001-pac-trap-and-self-patch.patch" ]; then
	patch -d "$D" -p1 -s --batch --forward < "$K/0001-pac-trap-and-self-patch.patch" || true
	echo "applied 0001-pac-trap-and-self-patch.patch (ARMv8.0 PAC Trap-and-Self-Patch engine)"
fi
echo "dada kernel tree in $D"

# Synchronize base/xnu/files overlay if present
if [ -d "$K/../base/xnu/files" ]; then
	cp -a "$K/../base/xnu/files/." "$D/"
	echo "synchronized base/xnu/files overlay into $D"
fi
if [ ! -f "$D/bsd/dev/redwood_focaltech_fw.h" ]; then
	cat << 'EOF' > "$D/bsd/dev/redwood_focaltech_fw.h"
#ifndef _REDWOOD_FOCALTECH_FW_H
#define _REDWOOD_FOCALTECH_FW_H
#define FOCALTECH_FW_LENGTH 0
static const unsigned char focaltech_fw[1] = { 0 };
#endif
EOF
	echo "created stub redwood_focaltech_fw.h for non-redwood build"
fi

# Part 3b: the port's own devices. Apple's HFS brings up the root on the pad's eMMC
# (bsd/dev/xun_emmc.c): without a filesystem for the root volume there is nothing to
# mount. mockfs gets a Mach-O check so it leaves a real filesystem to HFS.
if [ -f "$K/0105-xun-emmc-hfs-root.patch" ]; then
	# --forward: hunks an earlier patch already brought are skipped, not prompted for
	patch -d "$D" -p1 -s --forward --batch < "$K/0105-xun-emmc-hfs-root.patch" || true
	echo "applied 0105-xun-emmc-hfs-root.patch (eMMC root device, built-in HFS, mockfs guard)"
fi

# Stage Apple's open source HFS (bsd/xun_hfs): the sources are fetched, not carried in git.
if [ -x "$K/hfs/stage-hfs.sh" ]; then
	(cd "$D" && MACHINE=BCM2837 "$K/hfs/stage-hfs.sh")
	cp "$K/hfs/xun_hfs_compat.h" "$K/hfs/xun_hfs_iokit.c" "$D/bsd/xun_hfs/"
	echo "staged Apple HFS sources and the built-in registration shim"
fi

# Apply Part 2: LSE Branch Islands and Emulation
if [ -f "$K/0002-lse-branch-islands.patch" ]; then
	patch -d "$D" -p1 -s --batch --forward < "$K/0002-lse-branch-islands.patch" || true
	echo "applied 0002-lse-branch-islands.patch (ARMv8.0 LSE Branch Islands & Emulation)"
fi

# Apply Part 3: SLEH Trap Hooks (ARMv8.0 PAC & LSE Atomics)
if [ -f "$K/0003-sleh-hook.patch" ]; then
	patch -d "$D" -p1 -s --batch --forward < "$K/0003-sleh-hook.patch" || true
	echo "applied 0003-sleh-hook.patch (ARMv8.0 PAC & LSE Trap Hooks in SLEH)"
fi

# Apply Part 4: 4KB Quad-PTE Emulation Engine (SM6225 4KB Hardware Granule / 16KB Mach VM)
if [ -f "$K/0004-pmap-4kb-quad-pte.patch" ]; then
	patch -d "$D" -p1 -s --batch --forward < "$K/0004-pmap-4kb-quad-pte.patch" || true
	echo "applied 0004-pmap-4kb-quad-pte.patch (XNU 4KB Quad-PTE Emulation Engine)"
fi


# Apply Part 5a: Cortex-A73 PAC sysreg stub and early FB markers
if [ -f "$K/patch_cortex_a73.py" ]; then
	python3 "$K/patch_cortex_a73.py" "$D"
fi

# Apply Part 5a-bis: VM7 Hang Bisect & Local Barrier Patcher (Claude Code)
if [ -f "$K/patch_vm7_hang.py" ]; then
	HK_NO_TLBI=1 python3 "$K/patch_vm7_hang.py" "$D"
fi

# Apply Part 5b: 16KB RAM Alignment & Range Fix
if [ -f "$K/patch_ram_alignment.py" ]; then
	python3 "$K/patch_ram_alignment.py" "$D"
fi

# Apply Part 5c: Early Breadcrumb Visual Instrumentation
if [ -f "$K/patch_breadcrumbs.py" ]; then
	python3 "$K/patch_breadcrumbs.py" "$D"
fi

# Apply Part 5d: SM6225 Timer & Clock Subsystem Patch
if [ -f "$K/patch_timer_sm6225.py" ]; then
	python3 "$K/patch_timer_sm6225.py" "$D"
fi

# Apply Part 5e: Aperture-Based Early Memory Steal
if [ -f "$K/patch_aperture_steal.py" ]; then
	python3 "$K/patch_aperture_steal.py" "$D"
fi

# Apply Part 5f: BSD Subsystem Lifecycle Ladder
if [ -f "$K/patch_bsd_init.py" ]; then
	python3 "$K/patch_bsd_init.py" "$D"
fi

# Apply Part 5g: IOKit Root Device Resolver
if [ -f "$K/patch_iokit_root.py" ]; then
	python3 "$K/patch_iokit_root.py" "$D"
fi

# Apply Part 5h: VM10 Bisect & Extended Exception Register Dump
if [ -f "$K/patch_vm10_bisect.py" ]; then
	python3 "$K/patch_vm10_bisect.py" "$D"
fi

# Apply Part 5i: VM12 Diagnostics for PAR and boot_args (Claude Code)
if [ -f "$K/patch_vm12_diag.py" ]; then
	python3 "$K/patch_vm12_diag.py" "$D"
fi

# Apply Part 5j: VM14 page-table walk diagnostics (va_l1 range + GIC L1/L2/L3)
if [ -f "$K/patch_vm14_ptwalk_diag.py" ]; then
	python3 "$K/patch_vm14_ptwalk_diag.py" "$D/osfmk/arm64/gic_v3_irq.c" "$D"
fi

# Apply Part 5k: dump instruction word at ELR for UNDEF decoding
if [ -f "$K/patch_vm15_insn.py" ]; then
	python3 "$K/patch_vm15_insn.py" "$D"
fi

# Apply Part 5l: print panic() string on console
if [ -f "$K/patch_vm16_panicmsg.py" ]; then
	python3 "$K/patch_vm16_panicmsg.py" "$D"
fi

# Fix kasan-denylist regex for older/host Clang parser
if [ -f "$D/san/memory/kasan-denylist" ]; then
	sed -i '' 's/\[\*\]/\[\.\*\]/g' "$D/san/memory/kasan-denylist" 2>/dev/null || sed -i 's/\[\*\]/\[\.\*\]/g' "$D/san/memory/kasan-denylist"
	echo "fixed regex in $D/san/memory/kasan-denylist"
fi



# Fix C23 generic builtins (__builtin_clzg / __builtin_ctzg) for compilers < Clang 19
if [ -f "$D/osfmk/kern/bits.h" ]; then
	sed -i '' 's/__builtin_clzg(bitmap, 64)/(bitmap ? __builtin_clzll(bitmap) : 64)/g' "$D/osfmk/kern/bits.h" 2>/dev/null || sed -i 's/__builtin_clzg(bitmap, 64)/(bitmap ? __builtin_clzll(bitmap) : 64)/g' "$D/osfmk/kern/bits.h"
	sed -i '' 's/__builtin_ctzg(bitmap, -1)/(bitmap ? __builtin_ctzll(bitmap) : -1)/g' "$D/osfmk/kern/bits.h" 2>/dev/null || sed -i 's/__builtin_ctzg(bitmap, -1)/(bitmap ? __builtin_ctzll(bitmap) : -1)/g' "$D/osfmk/kern/bits.h"
	echo "patched clzg/ctzg compatibility in $D/osfmk/kern/bits.h"
fi

# Part 3c: what the scripts above rewrite. They generate their own versions of the same
# files from pristine sources, so the port's own edits to them are re-applied here.
if [ -f "$K/0106-xun-post.patch" ]; then
	# The scripts rewrote these files from pristine sources, so every hunk is new again.
	# Applied one file at a time: a hunk that still does not fit costs only its file.
	for f in "$K"/post/*.patch; do
		patch -d "$D" -p1 -s --batch --force < "$f" ||
			echo "warning: 0106 hunk did not fit $(basename "$f")" >&2
	done
	echo "applied 0106-xun-post.patch (edits the helper scripts would otherwise overwrite)"
fi
