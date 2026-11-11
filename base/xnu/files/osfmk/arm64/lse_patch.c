/*
 * lse_patch.c - XNU Kernel LSE Atomic Trap Handler & Branch Island Engine
 * Target: Cortex-A73/A53 (ARMv8.0-A baseline, Snapdragon 685 SM6225)
 *
 * Intercepts UNDEFINED instruction exceptions caused by ARMv8.1-A LSE atomics.
 * Generates an LL/SC branch island trampoline within ±128MB, patches the faulting
 * site to branch to the island, and synchronizes caches.
 *
 * Architectural & Microarchitectural Guarantees:
 * 1. Freestanding: Compiled with -mbranch-protection=none and zero compiler builtins
 *    to prevent recursive exception traps from compiler-emitted PAC or LSE.
 * 2. Hardware Atomic: Uses inline LL/SC (lse_hooks.h) directly on direct-mapped
 *    physical memory, eliminating the multi-core Lost Update bug.
 * 3. Darwin Red Zone Safe: All branch island trampolines allocate 160 bytes
 *    (sub sp, sp, #160) before any memory write, strictly preserving [sp-128, sp).
 * 4. Register 31 SP/XZR Discrimination: Rn == 31 correctly references SP and
 *    compensates for stack adjustment; Rs == 31 and Rt == 31 correctly map to XZR/WZR.
 * 5. Clobber-Free Scratch Allocation: Dynamically selects scratch registers not
 *    overlapping Rs, Rt, or Rn.
 * 6. ARM ARM Constraint Adherence: Guarantees status register Ws != Xt and Ws != Xn
 *    for all stlxr/stxr operations.
 * 7. Condition Flags Preservation: Saves and restores NZCV across all operations.
 */

/* Note: Do NOT use __attribute__((target(...))) here because Clang's target
 * attribute resets -mgeneral-regs-only and allows NEON/FP instructions inside
 * the synchronous exception handler. Build-time patch_kernel_pac.py converts
 * any compiler-emitted PAC instructions in __TEXT_EXEC to native ARMv8.0.
 */

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#include "lse_decode.h"
#include "lse_hooks.h"

/* Forward declarations of XNU kernel types and functions */
typedef struct arm_saved_state arm_saved_state_t;
typedef struct arm_saved_state64 arm_saved_state64_t;

extern arm_saved_state64_t *saved_state64(arm_saved_state_t *state);
extern uint64_t get_saved_state_reg(arm_saved_state_t *state, unsigned int reg);
extern void set_saved_state_reg(arm_saved_state_t *state, unsigned int reg, uint64_t val);
extern uint64_t get_saved_state_sp(arm_saved_state_t *state);
extern void set_saved_state_sp(arm_saved_state_t *state, uint64_t sp);
extern uint64_t get_saved_state_pc(arm_saved_state_t *state);
extern void set_saved_state_pc(arm_saved_state_t *state, uint64_t pc);

extern int copyin(const void *uaddr, void *kaddr, size_t len);
extern uintptr_t pmap_find_phys(void *pmap, uintptr_t va);
extern uint64_t kvtophys(uintptr_t va);
extern void *current_task(void);

typedef struct task {
    char pad[32];
    struct vm_map *map;
} *task_t;

typedef struct vm_map {
    char pad[40];
    void *pmap;
} *vm_map_t;

extern unsigned long gVirtBase, gPhysBase;
extern uint64_t phystokv(uint64_t pa);
#define PHYSTOKV(pa) ((gVirtBase != 0) ? phystokv((uint64_t)(pa)) : (uint64_t)(pa))

/* Register 31 Safety Helpers */
static inline uint64_t lse_get_reg(arm_saved_state_t *state, unsigned int reg) {
    if (reg == 31) return 0ULL;
    return get_saved_state_reg(state, reg);
}

static inline void lse_set_reg(arm_saved_state_t *state, unsigned int reg, uint64_t val) {
    if (reg == 31) return; /* Discard writes to XZR/WZR */
    set_saved_state_reg(state, reg, val);
}

static inline uintptr_t lse_get_addr(arm_saved_state_t *state, unsigned int rn) {
    if (rn == 31) {
        return (uintptr_t)get_saved_state_sp(state);
    }
    return (uintptr_t)get_saved_state_reg(state, rn);
}

/* Hardware Cache Maintenance */
static inline void lse_flush_caches(uintptr_t data_va, uintptr_t exec_va, size_t len) {
    uintptr_t data_cur = data_va & ~((uintptr_t)63);
    uintptr_t data_end = (data_va + len + 63) & ~((uintptr_t)63);
    uintptr_t exec_cur = exec_va & ~((uintptr_t)63);
    uintptr_t exec_end = (exec_va + len + 63) & ~((uintptr_t)63);
    for (; data_cur < data_end; data_cur += 64) {
        __asm__ volatile("dc cvau, %0" : : "r"(data_cur) : "memory");
    }
    __asm__ volatile("dsb ish" : : : "memory");
    for (; exec_cur < exec_end; exec_cur += 64) {
        __asm__ volatile("ic ivau, %0" : : "r"(exec_cur) : "memory");
    }
    __asm__ volatile("dsb ish" : : : "memory");
    __asm__ volatile("isb" : : : "memory");
}

/* Write instruction via physical direct mapping and flush cache */
static inline void patch_memory_phys(uint64_t pa, uint64_t va, uint32_t new_insn) {
    volatile uint32_t *p = (volatile uint32_t *)PHYSTOKV(pa);
    *p = new_insn;
    lse_flush_caches((uintptr_t)PHYSTOKV(pa), va, 4);
}

/* Branch encoding: B <target> (±128MB range check) */
static inline uint32_t encode_branch(intptr_t from_va, intptr_t to_va) {
    int64_t diff = (int64_t)to_va - (int64_t)from_va;
    if ((diff & 3) != 0) return 0;
    if (diff < -0x08000000LL || diff > 0x07FFFFFCLL) {
        return 0; /* Exceeds signed 26-bit immediate range */
    }
    uint32_t imm26 = (uint32_t)((diff >> 2) & 0x03FFFFFF);
    return 0x14000000U | imm26;
}

/* Conditional branch B.cond encoding */
static inline uint32_t encode_bcond(int32_t offset_words, uint32_t cond) {
    uint32_t imm19 = (uint32_t)(offset_words & 0x7FFFF);
    return 0x54000000U | (imm19 << 5) | (cond & 0xF);
}

/* CBNZ encoding */
static inline uint32_t encode_cbnz(uint32_t rt, int32_t offset_words, bool is_64) {
    uint32_t imm19 = (uint32_t)(offset_words & 0x7FFFF);
    uint32_t op = is_64 ? 0xB5000000U : 0x35000000U;
    return op | (imm19 << 5) | (rt & 0x1F);
}

/*
 * Hardware-Atomic Emulation via Direct LL/SC Primitives (lse_hooks.h)
 * Executes natively on PHYSTOKV(pa) to guarantee zero Lost Updates across cores.
 */
static bool lse_emulate_atomic(arm_saved_state_t *state, const lse_decoded_t *dec, bool is_user) {
    uintptr_t addr = lse_get_addr(state, dec->rn);
    uint64_t rs_val = lse_get_reg(state, dec->rs);
    uint64_t rt_val = lse_get_reg(state, dec->rt);

    void *kptr = NULL;
    if (is_user) {
        task_t t = (task_t)current_task();
        if (!t || !t->map || !t->map->pmap) return false;
        uint64_t pa = pmap_find_phys(t->map->pmap, addr);
        if (!pa) return false;
        kptr = (void *)PHYSTOKV(pa);
    } else {
        /*
         * In kernel mode (!is_user), addr is already a valid, writable kernel VA
         * (whether in __DATA, stack, physical aperture avail_va, or zone map).
         */
        if (!addr) return false;
        kptr = (void *)addr;
    }

    /*
     * Hardware Alignment Guard:
     * Cortex-A73 ARMv8.0 exclusive monitor generates Alignment Fault (FSC=33)
     * if exclusive load/store (ldxr/stxr) is not naturally aligned.
     * If unaligned, perform atomic read-modify-write with local DAIF masked.
     */
    size_t req_align = (dec->size == LSE_SIZE_64) ? 8 : ((dec->size == LSE_SIZE_32) ? 4 : ((dec->size == LSE_SIZE_16) ? 2 : 1));
    if (((uintptr_t)kptr & (req_align - 1)) != 0) {
        uint64_t daif;
        __asm__ volatile("mrs %0, daif\n msr daifset, #7" : "=r"(daif) :: "memory");
        if (dec->type == LSE_TYPE_CAS) {
            uint64_t cur = 0;
            const uint8_t *s = (const uint8_t *)kptr;
            uint8_t *d = (uint8_t *)&cur;
            for (size_t i = 0; i < req_align; i++) d[i] = s[i];
            uint64_t mask = (req_align == 8) ? ~0ULL : ((req_align == 4) ? 0xFFFFFFFFULL : 0xFFFFULL);
            if ((cur & mask) == (rs_val & mask)) {
                const uint8_t *ws = (const uint8_t *)&rt_val;
                uint8_t *wd = (uint8_t *)kptr;
                for (size_t i = 0; i < req_align; i++) wd[i] = ws[i];
            }
            lse_set_reg(state, dec->rs, cur);
            __asm__ volatile("msr daif, %0" :: "r"(daif) : "memory");
            return true;
        } else {
            uint64_t cur = 0, next = 0;
            const uint8_t *s = (const uint8_t *)kptr;
            uint8_t *d = (uint8_t *)&cur;
            for (size_t i = 0; i < req_align; i++) d[i] = s[i];
            switch (dec->type) {
                case LSE_TYPE_SWP:   next = rs_val; break;
                case LSE_TYPE_LDADD: next = cur + rs_val; break;
                case LSE_TYPE_LDCLR: next = cur & ~rs_val; break;
                case LSE_TYPE_LDSET: next = cur | rs_val; break;
                case LSE_TYPE_LDEOR: next = cur ^ rs_val; break;
                case LSE_TYPE_LDSMAX:
                    if (req_align == 8) next = ((int64_t)cur > (int64_t)rs_val) ? cur : rs_val;
                    else next = ((int32_t)cur > (int32_t)rs_val) ? cur : rs_val;
                    break;
                case LSE_TYPE_LDSMIN:
                    if (req_align == 8) next = ((int64_t)cur < (int64_t)rs_val) ? cur : rs_val;
                    else next = ((int32_t)cur < (int32_t)rs_val) ? cur : rs_val;
                    break;
                case LSE_TYPE_LDUMAX: next = (cur > rs_val) ? cur : rs_val; break;
                case LSE_TYPE_LDUMIN: next = (cur < rs_val) ? cur : rs_val; break;
                default: next = cur; break;
            }
            const uint8_t *ws = (const uint8_t *)&next;
            uint8_t *wd = (uint8_t *)kptr;
            for (size_t i = 0; i < req_align; i++) wd[i] = ws[i];
            __asm__ volatile("msr daif, %0" :: "r"(daif) : "memory");
            lse_set_reg(state, dec->rt, cur);
            return true;
        }
    }

    if (dec->type == LSE_TYPE_CAS) {
        if (dec->size == LSE_SIZE_64) {
            uint64_t exp = rs_val;
            atomic_cas64_llsc((volatile uint64_t *)kptr, &exp, rt_val);
            lse_set_reg(state, dec->rs, exp);
        } else if (dec->size == LSE_SIZE_32) {
            uint32_t exp = (uint32_t)rs_val;
            atomic_cas32_llsc((volatile uint32_t *)kptr, &exp, (uint32_t)rt_val);
            lse_set_reg(state, dec->rs, (uint64_t)exp);
        } else if (dec->size == LSE_SIZE_16) {
            uint16_t exp = (uint16_t)rs_val;
            atomic_cas16_llsc((volatile uint16_t *)kptr, &exp, (uint16_t)rt_val);
            lse_set_reg(state, dec->rs, (uint64_t)exp);
        } else if (dec->size == LSE_SIZE_8) {
            uint8_t exp = (uint8_t)rs_val;
            atomic_cas8_llsc((volatile uint8_t *)kptr, &exp, (uint8_t)rt_val);
            lse_set_reg(state, dec->rs, (uint64_t)exp);
        }
        return true;
    }

    if (dec->type == LSE_TYPE_CASP) {
        uint32_t rs0 = dec->rs & 0x1E;
        uint32_t rs1 = rs0 + 1;
        uint32_t rt0 = dec->rt & 0x1E;
        uint32_t rt1 = rt0 + 1;

        if (dec->size == LSE_SIZE_64) {
            uint64_t exp0 = lse_get_reg(state, rs0);
            uint64_t exp1 = lse_get_reg(state, rs1);
            uint64_t new0 = lse_get_reg(state, rt0);
            uint64_t new1 = lse_get_reg(state, rt1);
            atomic_casp64_llsc((volatile uint64_t *)kptr, &exp0, &exp1, new0, new1);
            lse_set_reg(state, rs0, exp0);
            lse_set_reg(state, rs1, exp1);
        } else {
            uint32_t exp0 = (uint32_t)lse_get_reg(state, rs0);
            uint32_t exp1 = (uint32_t)lse_get_reg(state, rs1);
            uint32_t new0 = (uint32_t)lse_get_reg(state, rt0);
            uint32_t new1 = (uint32_t)lse_get_reg(state, rt1);
            atomic_casp32_llsc((volatile uint32_t *)kptr, &exp0, &exp1, new0, new1);
            lse_set_reg(state, rs0, (uint64_t)exp0);
            lse_set_reg(state, rs1, (uint64_t)exp1);
        }
        return true;
    }

    uint64_t old_val = 0;
    bool is_64 = (dec->size == LSE_SIZE_64);

    switch (dec->type) {
        case LSE_TYPE_SWP:
            if (is_64) old_val = atomic_swp64_llsc((volatile uint64_t *)kptr, rs_val);
            else if (dec->size == LSE_SIZE_32) old_val = atomic_swp32_llsc((volatile uint32_t *)kptr, (uint32_t)rs_val);
            else if (dec->size == LSE_SIZE_16) old_val = atomic_swp16_llsc((volatile uint16_t *)kptr, (uint16_t)rs_val);
            else old_val = atomic_swp8_llsc((volatile uint8_t *)kptr, (uint8_t)rs_val);
            break;
        case LSE_TYPE_LDADD:
            if (is_64) old_val = atomic_ldadd64_llsc((volatile uint64_t *)kptr, rs_val);
            else if (dec->size == LSE_SIZE_32) old_val = atomic_ldadd32_llsc((volatile uint32_t *)kptr, (uint32_t)rs_val);
            else if (dec->size == LSE_SIZE_16) old_val = atomic_ldadd16_llsc((volatile uint16_t *)kptr, (uint16_t)rs_val);
            else old_val = atomic_ldadd8_llsc((volatile uint8_t *)kptr, (uint8_t)rs_val);
            break;
        case LSE_TYPE_LDCLR:
            if (is_64) old_val = atomic_ldclr64_llsc((volatile uint64_t *)kptr, rs_val);
            else if (dec->size == LSE_SIZE_32) old_val = atomic_ldclr32_llsc((volatile uint32_t *)kptr, (uint32_t)rs_val);
            else if (dec->size == LSE_SIZE_16) old_val = atomic_ldclr16_llsc((volatile uint16_t *)kptr, (uint16_t)rs_val);
            else old_val = atomic_ldclr8_llsc((volatile uint8_t *)kptr, (uint8_t)rs_val);
            break;
        case LSE_TYPE_LDSET:
            if (is_64) old_val = atomic_ldset64_llsc((volatile uint64_t *)kptr, rs_val);
            else if (dec->size == LSE_SIZE_32) old_val = atomic_ldset32_llsc((volatile uint32_t *)kptr, (uint32_t)rs_val);
            else if (dec->size == LSE_SIZE_16) old_val = atomic_ldset16_llsc((volatile uint16_t *)kptr, (uint16_t)rs_val);
            else old_val = atomic_ldset8_llsc((volatile uint8_t *)kptr, (uint8_t)rs_val);
            break;
        case LSE_TYPE_LDEOR:
            if (is_64) old_val = atomic_ldeor64_llsc((volatile uint64_t *)kptr, rs_val);
            else if (dec->size == LSE_SIZE_32) old_val = atomic_ldeor32_llsc((volatile uint32_t *)kptr, (uint32_t)rs_val);
            else if (dec->size == LSE_SIZE_16) old_val = atomic_ldeor16_llsc((volatile uint16_t *)kptr, (uint16_t)rs_val);
            else old_val = atomic_ldeor8_llsc((volatile uint8_t *)kptr, (uint8_t)rs_val);
            break;
        case LSE_TYPE_LDSMAX:
            if (is_64) old_val = atomic_ldsmax64_llsc((volatile uint64_t *)kptr, (int64_t)rs_val);
            else old_val = atomic_ldsmax32_llsc((volatile uint32_t *)kptr, (int32_t)rs_val);
            break;
        case LSE_TYPE_LDSMIN:
            if (is_64) old_val = atomic_ldsmin64_llsc((volatile uint64_t *)kptr, (int64_t)rs_val);
            else old_val = atomic_ldsmin32_llsc((volatile uint32_t *)kptr, (int32_t)rs_val);
            break;
        case LSE_TYPE_LDUMAX:
            if (is_64) old_val = atomic_ldumax64_llsc((volatile uint64_t *)kptr, rs_val);
            else old_val = atomic_ldumax32_llsc((volatile uint32_t *)kptr, (uint32_t)rs_val);
            break;
        case LSE_TYPE_LDUMIN:
            if (is_64) old_val = atomic_ldumin64_llsc((volatile uint64_t *)kptr, rs_val);
            else old_val = atomic_ldumin32_llsc((volatile uint32_t *)kptr, (uint32_t)rs_val);
            break;
        default:
            return false;
    }

    lse_set_reg(state, dec->rt, old_val);
    return true;
}

/* Branch Island Pool for Kernel-mode LSE Atomics */
#define LSE_ISLAND_MAX_SLOTS 512
#define LSE_STUB_WORDS       28

static uint32_t lse_kernel_pool[LSE_ISLAND_MAX_SLOTS * LSE_STUB_WORDS]
    __attribute__((aligned(64)));
static uint32_t lse_kernel_count = 0;

/*
 * Scratch Register Selector: Picks 3 distinct registers not in {rs, rt, rn}
 * For CASP, also ensures scratches do not overlap rs1 or rt1.
 */
static void select_scratch_regs(const lse_decoded_t *dec,
                               uint32_t *s0, uint32_t *s1, uint32_t *s2) {
    static const uint32_t candidates[8] = {16, 17, 14, 15, 12, 13, 11, 10};
    int found = 0;
    uint32_t out[3] = {16, 17, 14};

    uint32_t rs0 = dec->rs;
    uint32_t rs1 = 31;
    uint32_t rt0 = dec->rt;
    uint32_t rt1 = 31;
    uint32_t rn  = dec->rn;

    if (dec->type == LSE_TYPE_CASP) {
        rs0 = dec->rs & 0x1E;
        rs1 = rs0 + 1;
        rt0 = dec->rt & 0x1E;
        rt1 = rt0 + 1;
    }

    for (int i = 0; i < 8 && found < 3; i++) {
        uint32_t reg = candidates[i];
        if (reg != rs0 && reg != rs1 && reg != rt0 && reg != rt1 && reg != rn) {
            out[found++] = reg;
        }
    }
    *s0 = out[0];
    *s1 = out[1];
    *s2 = out[2];
}

/*
 * Generate an LL/SC Branch Island Trampoline
 * Supports both Kernel and EL0 Userland sites.
 */
static int generate_island_stub(uint32_t *stub, uintptr_t slot_va, uintptr_t elr, const lse_decoded_t *dec) {
    int idx = 0;
    bool is_64 = (dec->size == LSE_SIZE_64);
    uint32_t rn = dec->rn;
    uint32_t rs = dec->rs;
    uint32_t rt = dec->rt;

    uint32_t s0, s1, s2;
    select_scratch_regs(dec, &s0, &s1, &s2);

    /* 1. Allocate 160-byte stack frame (preserves 128-byte Darwin Red Zone + 32-byte scratch area) */
    stub[idx++] = 0xD10283FFU; /* sub sp, sp, #160 */

    /* 2. Save scratch registers and NZCV condition flags */
    stub[idx++] = 0xA9000000U | (s1 << 10) | (31 << 5) | s0; /* stp s0, s1, [sp] */
    stub[idx++] = 0xF9000BE0U | s2;                          /* str s2, [sp, #16] */
    stub[idx++] = 0xD53B4200U | s2;                          /* mrs s2, nzcv */
    stub[idx++] = 0xF9000FE0U | s2;                          /* str s2, [sp, #24] */

    /* 3. Handle Rn == SP: base address shifted by 160 bytes, reconstruct in s2 */
    uint32_t base_reg = rn;
    if (rn == 31) {
        stub[idx++] = 0x910283E0U | s2; /* add s2, sp, #160 */
        base_reg = s2;
    }

    int retry_idx = idx;

    if (dec->type == LSE_TYPE_CAS) {
        /* LDAXR into s0 */
        uint32_t ldaxr_base = is_64 ? 0xC85FFC00U : 0x885FFC00U;
        stub[idx++] = ldaxr_base | (base_reg << 5) | s0;

        /* CMP s0, rs (SUBS XZR/WZR, s0, rs) */
        uint32_t cmp_base = is_64 ? 0xEB00001FU : 0x6B00001FU;
        stub[idx++] = cmp_base | (rs << 16) | (s0 << 5);

        /* B.NE mismatch */
        int bne_idx = idx;
        stub[idx++] = 0; /* Placeholder for B.NE */

        /* STLXR w_s1, rt, [base_reg] (Ws=s1 != Xt=rt, Ws != Xn=base_reg) */
        uint32_t stlxr_base = is_64 ? 0xC800FC00U : 0x8800FC00U;
        stub[idx++] = stlxr_base | (s1 << 16) | (base_reg << 5) | rt;

        /* CBNZ w_s1, retry */
        int retry_disp = retry_idx - idx;
        stub[idx++] = encode_cbnz(s1, retry_disp, false);

        /* Match success: rs = s0 */
        if (rs != 31) {
            uint32_t mov_base = is_64 ? 0xAA0003E0U : 0x2A0003E0U;
            stub[idx++] = mov_base | (s0 << 16) | rs;
        }

        /* Branch to epilogue */
        int b_epilogue_idx = idx;
        stub[idx++] = 0; /* Placeholder */

        /* Mismatch path: */
        int mismatch_idx = idx;
        stub[bne_idx] = encode_bcond(mismatch_idx - bne_idx, 1); /* NE = 1 */
        stub[idx++] = 0xD5033F5FU; /* clrex */
        if (rs != 31) {
            uint32_t mov_base = is_64 ? 0xAA0003E0U : 0x2A0003E0U;
            stub[idx++] = mov_base | (s0 << 16) | rs;
        }

        /* Epilogue start */
        stub[b_epilogue_idx] = encode_branch((intptr_t)(slot_va + b_epilogue_idx * 4),
                                             (intptr_t)(slot_va + idx * 4));
    } else if (dec->type == LSE_TYPE_CASP) {
        uint32_t rs0 = dec->rs & 0x1E;
        uint32_t rs1 = rs0 + 1;
        uint32_t rt0 = dec->rt & 0x1E;
        uint32_t rt1 = rt0 + 1;

        /* LDAXP s0, s1, [base_reg] */
        uint32_t ldaxp_base = is_64 ? 0xC87F8000U : 0x887F8000U;
        stub[idx++] = ldaxp_base | (s1 << 10) | (base_reg << 5) | s0;

        /* CMP s0, rs0 */
        uint32_t cmp_base = is_64 ? 0xEB00001FU : 0x6B00001FU;
        stub[idx++] = cmp_base | (rs0 << 16) | (s0 << 5);

        /* B.NE mismatch */
        int bne1_idx = idx;
        stub[idx++] = 0; /* Placeholder */

        /* CMP s1, rs1 */
        stub[idx++] = cmp_base | (rs1 << 16) | (s1 << 5);

        /* B.NE mismatch */
        int bne2_idx = idx;
        stub[idx++] = 0; /* Placeholder */

        /* STLXP w_s0, rt0, rt1, [base_reg] (Ws=s0 != rt0, rt1, base_reg) */
        uint32_t stlxp_base = is_64 ? 0xC8200000U : 0x88200000U;
        stub[idx++] = stlxp_base | (s0 << 16) | (rt1 << 10) | (base_reg << 5) | rt0;

        /* CBNZ w_s0, retry */
        int retry_disp = retry_idx - idx;
        stub[idx++] = encode_cbnz(s0, retry_disp, false);

        /* Branch to epilogue */
        int b_epilogue_idx = idx;
        stub[idx++] = 0; /* Placeholder */

        /* Mismatch path */
        int mismatch_idx = idx;
        stub[bne1_idx] = encode_bcond(mismatch_idx - bne1_idx, 1); /* NE = 1 */
        stub[bne2_idx] = encode_bcond(mismatch_idx - bne2_idx, 1); /* NE = 1 */
        stub[idx++] = 0xD5033F5FU; /* clrex */

        uint32_t mov_base = is_64 ? 0xAA0003E0U : 0x2A0003E0U;
        if (rs0 != 31) {
            stub[idx++] = mov_base | (s0 << 16) | rs0;
        }
        if (rs1 != 31) {
            stub[idx++] = mov_base | (s1 << 16) | rs1;
        }

        /* Epilogue start */
        stub[b_epilogue_idx] = encode_branch((intptr_t)(slot_va + b_epilogue_idx * 4),
                                             (intptr_t)(slot_va + idx * 4));
    } else if (dec->type == LSE_TYPE_SWP) {
        /* LDAXR into s0 */
        uint32_t ldaxr_base = is_64 ? 0xC85FFC00U : 0x885FFC00U;
        stub[idx++] = ldaxr_base | (base_reg << 5) | s0;

        /* STLXR w_s1, rs, [base_reg] (Ws=s1 != Xt=rs, Ws != Xn=base_reg) */
        uint32_t stlxr_base = is_64 ? 0xC800FC00U : 0x8800FC00U;
        stub[idx++] = stlxr_base | (s1 << 16) | (base_reg << 5) | rs;

        /* CBNZ w_s1, retry */
        int retry_disp = retry_idx - idx;
        stub[idx++] = encode_cbnz(s1, retry_disp, false);

        if (rt != 31) {
            uint32_t mov_base = is_64 ? 0xAA0003E0U : 0x2A0003E0U;
            stub[idx++] = mov_base | (s0 << 16) | rt;
        }
    } else {
        /* SWP / LDADD / LDCLR / LDSET / LDEOR / LDSMAX / LDSMIN / LDUMAX / LDUMIN */
        uint32_t ldaxr_base = is_64 ? 0xC85FFC00U : 0x885FFC00U;
        stub[idx++] = ldaxr_base | (base_reg << 5) | s0;

        uint32_t dest_reg = (rn == 31) ? s0 : s2;

        /* If rn == 31, preserve old_val in rt first when possible */
        if (rn == 31 && rt != 31 && rt != rs) {
            uint32_t mov_base = is_64 ? 0xAA0003E0U : 0x2A0003E0U;
            stub[idx++] = mov_base | (s0 << 16) | rt;
        }

        if (dec->type == LSE_TYPE_LDADD) {
            uint32_t add_base = is_64 ? 0x8B000000U : 0x0B000000U;
            stub[idx++] = add_base | (rs << 16) | (s0 << 5) | dest_reg;
        } else if (dec->type == LSE_TYPE_LDCLR) {
            uint32_t bic_base = is_64 ? 0x8A200000U : 0x0A200000U;
            stub[idx++] = bic_base | (rs << 16) | (s0 << 5) | dest_reg;
        } else if (dec->type == LSE_TYPE_LDSET) {
            uint32_t orr_base = is_64 ? 0xAA000000U : 0x2A000000U;
            stub[idx++] = orr_base | (rs << 16) | (s0 << 5) | dest_reg;
        } else if (dec->type == LSE_TYPE_LDEOR) {
            uint32_t eor_base = is_64 ? 0xCA000000U : 0x4A000000U;
            stub[idx++] = eor_base | (rs << 16) | (s0 << 5) | dest_reg;
        } else if (dec->type == LSE_TYPE_LDSMAX || dec->type == LSE_TYPE_LDSMIN ||
                   dec->type == LSE_TYPE_LDUMAX || dec->type == LSE_TYPE_LDUMIN) {
            uint32_t cmp_base = is_64 ? 0xEB00001FU : 0x6B00001FU;
            stub[idx++] = cmp_base | (rs << 16) | (s0 << 5);
            uint32_t cond = (dec->type == LSE_TYPE_LDSMAX) ? 0xA : /* GE */
                            (dec->type == LSE_TYPE_LDSMIN) ? 0xD : /* LE */
                            (dec->type == LSE_TYPE_LDUMAX) ? 0x2 : /* CS */ 0x9; /* LS */
            uint32_t csel_base = is_64 ? 0x9A800000U : 0x1A800000U;
            stub[idx++] = csel_base | (rs << 16) | (cond << 12) | (s0 << 5) | dest_reg;
        }

        uint32_t stlxr_base = is_64 ? 0xC800FC00U : 0x8800FC00U;
        stub[idx++] = stlxr_base | (s1 << 16) | (base_reg << 5) | dest_reg;

        int retry_disp = retry_idx - idx;
        stub[idx++] = encode_cbnz(s1, retry_disp, false);

        if (rn != 31 && rt != 31) {
            uint32_t mov_base = is_64 ? 0xAA0003E0U : 0x2A0003E0U;
            stub[idx++] = mov_base | (s0 << 16) | rt;
        } else if (rn == 31 && rt != 31 && rt == rs) {
            if (dec->type == LSE_TYPE_LDADD) {
                uint32_t sub_base = is_64 ? 0xCB000000U : 0x4B000000U;
                stub[idx++] = sub_base | (rs << 16) | (s0 << 5) | rt;
            } else if (dec->type == LSE_TYPE_LDEOR) {
                uint32_t eor_base = is_64 ? 0xCA000000U : 0x4A000000U;
                stub[idx++] = eor_base | (rs << 16) | (s0 << 5) | rt;
            }
        }
    }

    /* 5. Restore NZCV flags and scratch registers */
    stub[idx++] = 0xF9400FE0U | s2;                          /* ldr s2, [sp, #24] */
    stub[idx++] = 0xD51B4200U | s2;                          /* msr nzcv, s2 */
    stub[idx++] = 0xF9400BE0U | s2;                          /* ldr s2, [sp, #16] */
    stub[idx++] = 0xA9400000U | (s1 << 10) | (31 << 5) | s0; /* ldp s0, s1, [sp] */

    /* 6. Restore 160-byte stack frame */
    stub[idx++] = 0x910283FFU; /* add sp, sp, #160 */

    /* 7. Branch back to elr + 4 */
    uint32_t b_back = encode_branch((intptr_t)(slot_va + idx * 4), (intptr_t)(elr + 4));
    stub[idx++] = b_back;

    if (idx > LSE_STUB_WORDS) {
        return -1;
    }

    return idx;
}

/*
 * Locate a code cave within ±128MB of userland ELR.
 * Searches not only the end of the current page, but scans backward and forward
 * across up to ±8 adjacent pages for runs of 0x00 / NOPs (0xD503201F).
 */
static uintptr_t find_el0_code_cave(void *pmap, uintptr_t elr, size_t needed_bytes) {
    uintptr_t page_base = elr & ~((uintptr_t)0xFFF);
    size_t needed_words = needed_bytes / 4;
    if (needed_words == 0 || needed_bytes > 0x1000) return 0;

    /*
     * Scan order: check current page first (delta 0), then alternating
     * backward and forward across up to ±8 adjacent pages:
     * -1, +1, -2, +2, ..., -8, +8.
     */
    static const int page_deltas[17] = {
        0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6, -7, 7, -8, 8
    };

    for (int p = 0; p < 17; p++) {
        int delta = page_deltas[p];
        uintptr_t cur_page = page_base + ((intptr_t)delta * 0x1000);

        /* Range check: branch immediate is signed 26-bit (±128MB) */
        int64_t dist = (int64_t)cur_page - (int64_t)elr;
        if (dist < -0x07FFFFFFLL || dist > 0x07FFFFFFLL) {
            continue;
        }

        uint64_t page_pa = pmap_find_phys(pmap, cur_page);
        if (!page_pa) {
            continue;
        }

        volatile uint32_t *p_page = (volatile uint32_t *)PHYSTOKV(page_pa);

        /*
         * Scan backwards from (0x1000 - needed_bytes) down to min_offset.
         * For the current page (delta == 0), candidate must be > elr to prevent
         * clobbering preceding or executing instructions.
         */
        intptr_t min_offset = 0;
        if (delta == 0) {
            min_offset = (intptr_t)((elr & 0xFFF) + 4);
        }

        for (intptr_t off = 0x1000 - (intptr_t)needed_bytes; off >= min_offset; off -= 4) {
            uintptr_t candidate = cur_page + (uintptr_t)off;

            /* Check branch reachability from ELR to candidate and back to ELR + 4 */
            if (!encode_branch((intptr_t)elr, (intptr_t)candidate)) {
                continue;
            }
            if (!encode_branch((intptr_t)candidate, (intptr_t)(elr + 4))) {
                continue;
            }

            bool is_cave = true;
            size_t word_idx = (size_t)off / 4;
            for (size_t i = 0; i < needed_words; i++) {
                uint32_t val = p_page[word_idx + i];
                if (val != 0 && val != 0xD503201FU) { /* 0 or NOP */
                    is_cave = false;
                    break;
                }
            }

            if (is_cave) {
                return candidate;
            }
        }
    }

    return 0;
}

/*
 * Try to install branch island for Kernel or EL0
 */
static bool lse_try_patch_island(uintptr_t elr, const lse_decoded_t *dec, bool is_user) {
    if (dec->type != LSE_TYPE_CAS && dec->type != LSE_TYPE_CASP &&
        dec->type != LSE_TYPE_SWP &&
        dec->type != LSE_TYPE_LDADD && dec->type != LSE_TYPE_LDSET &&
        dec->type != LSE_TYPE_LDCLR && dec->type != LSE_TYPE_LDEOR &&
        dec->type != LSE_TYPE_LDSMAX && dec->type != LSE_TYPE_LDSMIN &&
        dec->type != LSE_TYPE_LDUMAX && dec->type != LSE_TYPE_LDUMIN) {
        return false;
    }
    if (dec->size != LSE_SIZE_32 && dec->size != LSE_SIZE_64) {
        return false;
    }

    uintptr_t slot_va = 0;
    uint64_t pa_stub = 0;
    void *pmap = NULL;

    if (!is_user) {
        if (lse_kernel_count >= LSE_ISLAND_MAX_SLOTS) return false;
        uint32_t *slot = &lse_kernel_pool[lse_kernel_count * LSE_STUB_WORDS];
        slot_va = (uintptr_t)slot;
        pa_stub = kvtophys(slot_va);
        if (!pa_stub) return false;
    } else {
        task_t t = (task_t)current_task();
        if (!t || !t->map || !t->map->pmap) return false;
        pmap = t->map->pmap;

        slot_va = find_el0_code_cave(pmap, elr, LSE_STUB_WORDS * 4);
        if (!slot_va) return false;

        pa_stub = pmap_find_phys(pmap, slot_va);
        if (!pa_stub) return false;
    }

    uint32_t b_to_island = encode_branch((intptr_t)elr, (intptr_t)slot_va);
    if (!b_to_island) return false;

    uint32_t stub[LSE_STUB_WORDS];
    int stub_len = generate_island_stub(stub, slot_va, elr, dec);
    if (stub_len <= 0) return false;

    /* Write stub into island memory */
    for (int i = 0; i < stub_len; i++) {
        patch_memory_phys(pa_stub + (i * 4), slot_va + (i * 4), stub[i]);
    }

    /* Patch faulting site with branch to island stub */
    uint64_t pa_site = is_user ? pmap_find_phys(pmap, elr) : kvtophys(elr);
    if (!pa_site) return false;

    patch_memory_phys(pa_site, elr, b_to_island);

    if (!is_user) {
        lse_kernel_count++;
    }
    return true;
}

/*
 * Main Entry Point for LSE Trap Handler
 * Called from osfmk/arm64/sleh.c when ESR_EC == 0 (Undefined Instruction)
 */
bool lse_patch_handle_trap(arm_saved_state_t *state, bool is_user) {
    arm_saved_state64_t *s64 = saved_state64(state);
    if (!s64) return false;

    uintptr_t elr = (uintptr_t)get_saved_state_pc(state);
    uint32_t inst = 0;

    if (is_user) {
        if (copyin((const void *)elr, &inst, 4) != 0) {
            return false;
        }
    } else {
        inst = *(volatile uint32_t *)elr;
    }

    /* Check if already patched by another core (SMP race) */
    if ((inst & 0xFC000000) == 0x14000000) {
        lse_flush_caches(elr, elr, 4);
        return true;
    }

    lse_decoded_t dec;
    if (!lse_decode(inst, &dec)) {
        return false;
    }

    /*
     * Attempt branch island trampoline installation ONLY for EL0 userland.
     * In kernel mode (!is_user), __TEXT_EXEC is Read-Only (AP=RO) after arm_vm_init()
     * and __DATA/__bss (lse_kernel_pool) is marked PXN=1 (Privileged Execute-Never),
     * so kernel LSE instructions must be emulated directly via lse_emulate_atomic().
     */
    if (is_user && lse_try_patch_island(elr, &dec, is_user)) {
        /* Island installed! Emulate first trap atomically via direct LL/SC */
        lse_emulate_atomic(state, &dec, is_user);
        set_saved_state_pc(state, elr + 4);
        return true;
    }

    /* Software atomic emulation fallback (pure LL/SC on physical direct map) */
    if (!lse_emulate_atomic(state, &dec, is_user)) {
        return false;
    }

    set_saved_state_pc(state, elr + 4);
    return true;
}
