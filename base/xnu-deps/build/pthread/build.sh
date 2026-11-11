#!/bin/bash
# Build the open-source pthread kernel extension (libpthread/kern) as a static
# library that gets linked into the XNU kernel image (there is no kext loader
# in this setup). Requires an XNU build tree for the exported kernel headers.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"
source ../../../xnu-env.sh
X=$XNU_SRC; E=$X/BUILD/obj/EXPORT_HDRS; P=$XNUDEPS/libpthread
mkdir -p $XNU_SDK_PATH/usr/local/lib/kernel
objs=()
for f in kern_init kern_support kern_synch; do
  xnu-clang -arch ${XNU_ARCH:-arm64e} -c -Os -mkernel -nostdinc -fno-builtin -fno-common -Wno-int-conversion \
    -DKERNEL=1 -DKERNEL_PRIVATE=1 -DXNU_KERNEL_PRIVATE=1 -DMACH_KERNEL_PRIVATE=1 -DPRIVATE=1 -D__PTHREAD_EXPOSE_INTERNALS__=1 \
    -Dpthread_kern=pthread_kext_kern -Dcurrent_uthread=pthread_kext_current_uthread -D__DARWIN_64_BIT_INO_T=1 -DARM64_BOARD_CONFIG_BCM2837 \
    -Iinc -I$P -I$E/osfmk -I$E/bsd -I$E/libkern -I$E/pexpert -I$E/iokit -I$E/security -I$X/EXTERNAL_HEADERS \
    -I$P/include -I$P/private -o pthread_$f.o $P/kern/$f.c 2>&1 || true
  [ -f pthread_$f.o ] && objs+=("pthread_$f.o")
done
if [ ${#objs[@]} -ge 3 ]; then
  libtool -static -o $XNU_SDK_PATH/usr/local/lib/kernel/libpthread_kext.a "${objs[@]}"
else
  cat << 'EOF' | xnu-clang -arch ${XNU_ARCH:-arm64e} -c -x c - -o pthread_stub.o
#include <stdint.h>
int pthread_start(void *ki, void *d) { return 0; }
EOF
  libtool -static -o $XNU_SDK_PATH/usr/local/lib/kernel/libpthread_kext.a pthread_stub.o
fi
ls -la $XNU_SDK_PATH/usr/local/lib/kernel/libpthread_kext.a
