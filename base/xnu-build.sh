#!/bin/bash
# Build XNU for redwood on Linux. Extra make args are passed through.
source "$(dirname "${BASH_SOURCE[0]}")/xnu-env.sh"
cd "$XNU_SRC" || exit 1
# The Wi-Fi driver's object is linked into the kernel when it is there; make does not see it change, so the linked
# kernel is thrown away when it has. redwood's is ath11k (tools/mk-ath11k.sh, WCN6750), dada's is ath12k
# (tools/mk-ath12k.sh, peach); each build has at most one.
LX=$XNUDEPS/build/ath11k/out/ath11k.o
[ -f "$LX" ] || LX=$XNUDEPS/build/ath12k/out/ath12k.o
# No radio driver object on this board: kernel/stubs.c carries the symbols the closed
# kexts would give (firehose, the JOP/ptrauth helpers) -- build it when it is not there.
STUBS=$DARWIN_REDWOOD/../kernel/stubs.o
if [ -f "$DARWIN_REDWOOD/../kernel/stubs.c" ]; then
	[ -f "$STUBS" ] || {
		xnu-clang -c -Os -mkernel -nostdinc -D_KERNEL -DARM64_BOARD_CONFIG_BCM2837 \
			-I"${XNU_SRC:-.}/osfmk" -I"${XNU_SRC:-.}/bsd" -I"${XNU_SRC:-.}/libkern" -I"${XNU_SRC:-.}/iokit" \
			-I"${XNU_SRC:-.}/pexpert" -I"${XNU_SRC:-.}/EXTERNAL_HEADERS" \
			-o "$STUBS" "$DARWIN_REDWOOD/../kernel/stubs.c"
	}
fi
[ -f "$LX" ] || LX=$STUBS
# The objects of arm64 and arm64e builds land in the same directory (the machine configuration, not
# the architecture, names it), and make cannot tell them apart: start over when the architecture
# changes. The kernel's own static libraries (xnu-deps/setup.sh) need the same architecture; see
# xnu-deps/setup.sh --rebuild-libs.
ARCHSTAMP=BUILD/obj/redwood-arch
if [ -d BUILD/obj ] && [ "$(cat $ARCHSTAMP 2>/dev/null)" != "${XNU_ARCH:-arm64}" ]; then
	echo "xnu-build.sh: architecture changed to ${XNU_ARCH:-arm64}: discarding BUILD/obj"
	rm -rf BUILD/obj
fi
mkdir -p BUILD/obj && echo "${XNU_ARCH:-arm64}" > $ARCHSTAMP
STAMP=BUILD/obj/redwood-objects
if [ -f "$LX" ]; then now="$LX $(stat -c %Y "$LX" 2>/dev/null || stat -f %m "$LX")"; else LX= now=; fi
if [ -d BUILD/obj ] && [ "$(cat $STAMP 2>/dev/null)" != "$now" ]; then
	rm -f BUILD/obj/*/mach.*.unstripped
	echo "$now" > $STAMP
fi
# The build tools of SETUP/ are compiled for this machine: on Linux with glibc stand-ins for BSD
# and Mach calls, on macOS against the system's own headers.
if [ -n "$XNU_HOST_DARWIN" ]; then
	HOSTCC="clang -I$XNUDEPS/hostinc-darwin -Wno-unused-command-line-argument"
else
	HOSTCC="clang -include $XNUDEPS/hostinc/hostcompat.h -I$XNUDEPS/hostinc -Wno-unused-command-line-argument -Wno-error=implicit-function-declaration"
	# A Linux host builds with a stock clang: the pointer-authentication paths and the
	# static-if asm goto cannot be used, so both are switched off for the whole build.
	export STATIC_IF_PLAIN=${STATIC_IF_PLAIN:-1}
	export XNU_NO_PAC=${XNU_NO_PAC:-1}
fi
exec make RC_DARWIN_KERNEL_VERSION=${RC_DARWIN_KERNEL_VERSION:-26.0.0} RC_ProjectSourceVersion=${RC_ProjectSourceVersion:-12377.121.6} \
  REDWOOD_OBJECTS="$LX" SDKROOT="${XNU_SDK_PATH:-iphoneos}" ARCH_CONFIGS=ARM64 MACHINE_CONFIGS=${MACHINE:-BCM2837} ${XNU_EXTRA_DEFS:-} KERNEL_CONFIGS=DEVELOPMENT \
  ARCH_STRING_FOR_CURRENT_MACHINE_CONFIG=${XNU_ARCH:-arm64e} BUILD_WERROR=0 BUILD_LTO=0 DO_CTFMERGE=0 BUILD_DSYM=0 \
  HOST_CC="$HOSTCC" HOST_FLEX=flex HOST_BISON=bison HOST_GM4=m4 CC=xnu-clang CXX=xnu-clang++ LOGCOLORS=n "$@"
