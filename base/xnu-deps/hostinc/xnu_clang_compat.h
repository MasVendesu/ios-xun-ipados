/*
 * Force-included into every XNU translation unit when building with stock
 * (non-Apple) clang. Apple's clang has builtins that describe the pointer/data
 * layout of a type for kalloc_type; upstream clang lacks them, so every type
 * is conservatively described as "mixed pointers and data".
 */
#ifndef XNU_CLANG_COMPAT_H
#define XNU_CLANG_COMPAT_H
/* Apple's SDK spells availability as bare macros that only Apple's clang expands;
 * stock clang leaves them in the source. They only annotate, so they go away
 * (image4/image4.h neutralises them again after the SDK headers). */
#define API_AVAILABLE(...)
#define __API_AVAILABLE(...)
#define API_DEPRECATED(...)
#define __API_DEPRECATED(...)
#define API_UNAVAILABLE(...)
#define __API_UNAVAILABLE(...)
#define API_DEPRECATED_WITH_REPLACEMENT(...)
#define __API_DEPRECATED_WITH_REPLACEMENT(...)
/* image4 defines these itself before Availability.h is in, so they expand to a bare
 * platform list: define them here, and the image4 definitions are skipped by #ifndef. */
#define IMAGE4_API_AVAILABLE_SPRING_2024
#define IMAGE4_API_AVAILABLE_FALL_2024
#define IMAGE4_API_DEPRECATED_SPRING_2024
/* osfmk/kern/assert.h asks mach_assert_enabled() through a statement expression with
 * an asm goto inside. clang before 17 cannot jump there from an outer expression
 * ("cannot jump from this asm goto statement"), so the enable test becomes a plain
 * constant: the static-if fixups are a patch-time optimisation, not needed here.
 * It is set up here, which is force-included into every unit before assert.h. */
#if !defined(ASSEMBLER) && !defined(__ASSEMBLER__)
#if !__has_builtin(__builtin_clzg)
#define __builtin_clzg(x, fallback) ((x) ? __builtin_clzll((unsigned long long)(x)) : (fallback))
#endif
#if !__has_builtin(__builtin_ctzg)
#define __builtin_ctzg(x, fallback) ((x) ? __builtin_ctzll((unsigned long long)(x)) : (fallback))
#endif
/* ARM builtins stock clang does not know: count leading zeros in a 64-bit value, and
 * the arm64e pointer-auth primitives (blend / sign_unauthenticated). On arm64e targets
 * the compiler has the auth ones; the blend falls back to the logical equivalent. */
#if !__has_builtin(__builtin_arm_clz64)
#define __builtin_arm_clz64(x) __builtin_clzll((unsigned long long)(x))
#endif
#if !__has_builtin(__builtin_ptrauth_blend_discriminator)
#define __builtin_ptrauth_blend_discriminator(ptr, discriminator) \
	((void *)((uintptr_t)(ptr) ^ (uintptr_t)(discriminator)))
#endif
#if !__has_builtin(__builtin_ptrauth_sign_unauthenticated)
#define __builtin_ptrauth_sign_unauthenticated(ptr, key, discriminator) \
	((void *)(ptr))
#endif
#if !__has_builtin(__builtin_xnu_type_signature)
#define XNU_NO_TYPE_BUILTINS                     1
#define __builtin_xnu_type_signature(type)       "12"
#define __builtin_xnu_type_summary(type)         ((1U << 1) | (1U << 2))
#ifdef __cplusplus
#define __builtin_xnu_types_compatible(a, b)     __is_same(a, b)
#else
#define __builtin_xnu_types_compatible(a, b)     __builtin_types_compatible_p(a, b)
#endif
#else
/* Apple's clang (macOS hosts) has the builtins; OSData.h still tests the macro */
#define XNU_NO_TYPE_BUILTINS                     0
#endif

/*
 * osfmk/kern/assert.h builds mach_assert_enabled() on improbable_static_if(), a
 * statement expression whose asm goto does not make it out of an enclosing || in
 * clang before 17 ("cannot jump from this asm goto statement"). Here it becomes a
 * plain constant: the static-if key fixups are a patch-time optimisation only.
 * The switch is set up in the port's kernel patch on osfmk/machine/static_if.h: the
 * header is reached from too many directions for an include guard trick here, and
 * -DSTATIC_IF_PLAIN picks the constant form there.
 */
#endif
#endif
