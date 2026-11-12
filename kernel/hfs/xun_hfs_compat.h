/*
 * Compile Apple's HFS kext sources inside this kernel the way the kext is
 * built: with the kext (KPI) view of the kernel headers, and with only HFS's
 * own kext-config.h deciding features. The kernel's -DCONFIG_* options are not
 * visible to a kext build, so the ones HFS tests are cleared first. This
 * kernel has no AppleKeyStore, so content protection stays off.
 */
#ifndef XUN_HFS_COMPAT_H
#define XUN_HFS_COMPAT_H
#undef BSD_KERNEL_PRIVATE
#undef XNU_KERNEL_PRIVATE
#undef CONFIG_PROTECT
#undef CONFIG_SEARCHFS
#undef CONFIG_HFS_DIRLINK
#undef CONFIG_SECLUDED_RENAME
#undef CONFIG_MACF
#undef CONFIG_HFS_RBTREE
#undef QUOTA
#undef NAMEDSTREAMS
#undef FIFO
#undef HFS_COMPRESSION
#include "core/kext-config.h"
/* TargetConditionals.h (pulled in by kext-config.h) says iPhone; the encodings
 * interface is inside `#if !TARGET_OS_IPHONE` and the kernel build needs it. */
#undef TARGET_OS_IPHONE
#define TARGET_OS_IPHONE 0
/* protection is off (no AppleKeyStore); the kext-config.h value is the kext's own */
#undef CONFIG_PROTECT
#define CONFIG_PROTECT 0

/* Built in, not a loadable kext (see xun_hfs_iokit.c). */
#define OSKextGetCurrentLoadTag xun_hfs_current_load_tag
#endif
