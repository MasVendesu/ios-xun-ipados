/*
 * lse_decode.h - Freestanding ARMv8.1-A LSE Atomics Instruction Decoder
 * Target: Cortex-A73/A53 (ARMv8.0-A baseline trap-and-emulate / branch-island)
 *
 * Decodes CAS, CASP, SWP, LDADD/STADD, LDCLR/STCLR, LDEOR/STEOR, LDSET/STSET,
 * LDSMAX/STSMAX, LDSMIN/STSMIN, LDUMAX/STUMAX, LDUMIN/STUMIN.
 * Header-only, no libc dependencies.
 */

#ifndef LSE_DECODE_H
#define LSE_DECODE_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    LSE_TYPE_NONE = 0,
    LSE_TYPE_CAS,
    LSE_TYPE_CASP,
    LSE_TYPE_SWP,
    LSE_TYPE_LDADD,
    LSE_TYPE_LDCLR,
    LSE_TYPE_LDEOR,
    LSE_TYPE_LDSET,
    LSE_TYPE_LDSMAX,
    LSE_TYPE_LDSMIN,
    LSE_TYPE_LDUMAX,
    LSE_TYPE_LDUMIN
} lse_type_t;

typedef enum {
    LSE_SIZE_8  = 0,
    LSE_SIZE_16 = 1,
    LSE_SIZE_32 = 2,
    LSE_SIZE_64 = 3
} lse_size_t;

typedef struct {
    lse_type_t type;
    lse_size_t size;
    bool       acquire;    /* 'a' bit */
    bool       release;    /* 'l' or 'r' bit */
    uint8_t    rs;         /* Source/compare reg (or pair reg1) */
    uint8_t    rt;         /* Target/result reg (or pair reg0) */
    uint8_t    rn;         /* Address base register (31 = SP) */
} lse_decoded_t;

static inline bool lse_decode(uint32_t inst, lse_decoded_t *out) {
    if (!out) return false;

    out->type = LSE_TYPE_NONE;
    out->rs = (uint8_t)((inst >> 16) & 0x1F);
    out->rn = (uint8_t)((inst >> 5) & 0x1F);
    out->rt = (uint8_t)(inst & 0x1F);

    uint32_t size_bits = (inst >> 30) & 0x3;
    out->size = (lse_size_t)size_bits;

    /*
     * 1. CAS: compare and swap
     * Bit pattern: size[31:30] 001000 1 A 0 Rs[20:16] 1 L 11111 Rn[9:5] Rt[4:0]
     * Mask [29:21] == 001000 1 0 0 => 0x3FE00000 & inst == 0x08A00000 (with A=bit 22)
     * Bits [14:10] == 0b11111 (0x1F)
     * Bit 15 = L (Release)
     */
    if ((inst & 0x3FA07C00) == 0x08A07C00) {
        out->type = LSE_TYPE_CAS;
        out->acquire = (inst & (1U << 22)) != 0;
        out->release = (inst & (1U << 15)) != 0;
        return true;
    }

    /*
     * 2. CASP: compare and swap pair
     * Bit pattern: size[31:30] 001000 0 A 0 Rs[20:16] 1 L 11111 Rn[9:5] Rt[4:0]
     * size_bits: 00 = 32-bit pair, 01 = 64-bit pair
     */
    if ((inst & 0x3FA07C00) == 0x08207C00) {
        if (size_bits == 0 || size_bits == 1) {
            out->type = LSE_TYPE_CASP;
            out->size = (size_bits == 0) ? LSE_SIZE_32 : LSE_SIZE_64;
            out->acquire = (inst & (1U << 22)) != 0;
            out->release = (inst & (1U << 15)) != 0;
            return true;
        }
    }

    /*
     * 3. Atomic memory operations (LDADD, LDCLR, LDEOR, LDSET, SWP, LDSMAX, LDSMIN, LDUMAX, LDUMIN)
     * Pattern: size[31:30] 111000 A[23] R[22] 1[21] Rs[20:16] o3[15] opc[14:12] 00 Rn[9:5] Rt[4:0]
     * Bits [29:24] == 0b111000 (0x38 << 24), Bit 21 == 1 (0x00200000)
     * Bits [11:10] == 0b00
     */
    if ((inst & 0x3F200C00) == 0x38200000) {
        out->acquire = (inst & (1U << 23)) != 0;
        out->release = (inst & (1U << 22)) != 0;

        uint32_t o3 = (inst >> 15) & 0x1;
        uint32_t opc = (inst >> 12) & 0x7;

        if (o3 == 1 && opc == 0) {
            out->type = LSE_TYPE_SWP;
            return true;
        } else if (o3 == 0) {
            switch (opc) {
                case 0: out->type = LSE_TYPE_LDADD; return true;
                case 1: out->type = LSE_TYPE_LDCLR; return true;
                case 2: out->type = LSE_TYPE_LDEOR; return true;
                case 3: out->type = LSE_TYPE_LDSET; return true;
                case 4: out->type = LSE_TYPE_LDSMAX; return true;
                case 5: out->type = LSE_TYPE_LDSMIN; return true;
                case 6: out->type = LSE_TYPE_LDUMAX; return true;
                case 7: out->type = LSE_TYPE_LDUMIN; return true;
                default: break;
            }
        }
    }

    return false;
}

static inline int lse_decode_selftest(void) {
    lse_decoded_t d;

    /* CAS W0, W1, [X2] -> 0x88a17c40 */
    /* size=2 (32-bit), A=0, L=0, Rs=1, Rn=2, Rt=0 */
    uint32_t cas_w = 0x88A17C40;
    if (!lse_decode(cas_w, &d) || d.type != LSE_TYPE_CAS || d.size != LSE_SIZE_32 ||
        d.rs != 1 || d.rn != 2 || d.rt != 0 || d.acquire || d.release) {
        return 1;
    }

    /* CASAL X3, X4, [X5] */
    /* size=3 (64-bit), A=1 (bit 22), L=1 (bit 15), Rs=4, Rn=5, Rt=3 */
    uint32_t casal_x = 0xC8E4FC83;
    if (!lse_decode(casal_x, &d) || d.type != LSE_TYPE_CAS || d.size != LSE_SIZE_64 ||
        d.rs != 4 || d.rn != 5 || d.rt != 3 || !d.acquire || !d.release) {
        return 2;
    }

    /* SWP X0, X1, [X2] -> size=3, A=0, R=0, Rs=0, Rn=2, Rt=1, o3=1, opc=0 */
    uint32_t swp_x = 0xF8208041;
    if (!lse_decode(swp_x, &d) || d.type != LSE_TYPE_SWP || d.size != LSE_SIZE_64 ||
        d.rs != 0 || d.rn != 2 || d.rt != 1) {
        return 3;
    }

    /* LDADD W6, W7, [X8] -> size=2, A=0, R=0, Rs=6, o3=0, opc=0, Rn=8, Rt=7 */
    uint32_t ldadd_w = 0xB8260107;
    if (!lse_decode(ldadd_w, &d) || d.type != LSE_TYPE_LDADD || d.size != LSE_SIZE_32 ||
        d.rs != 6 || d.rn != 8 || d.rt != 7) {
        return 4;
    }

    /* STADD W6, [X8] -> LDADD with Rt=31 */
    uint32_t stadd_w = 0xB826011F;
    if (!lse_decode(stadd_w, &d) || d.type != LSE_TYPE_LDADD || d.size != LSE_SIZE_32 ||
        d.rs != 6 || d.rn != 8 || d.rt != 31) {
        return 5;
    }

    /* LDCLR X1, X2, [X3] -> opc=1 */
    uint32_t ldclr_x = 0xF8211062;
    if (!lse_decode(ldclr_x, &d) || d.type != LSE_TYPE_LDCLR || d.size != LSE_SIZE_64 ||
        d.rs != 1 || d.rn != 3 || d.rt != 2) {
        return 6;
    }

    /* LDSET X4, X5, [X6] -> opc=3 */
    uint32_t ldset_x = 0xF82430C5;
    if (!lse_decode(ldset_x, &d) || d.type != LSE_TYPE_LDSET || d.size != LSE_SIZE_64 ||
        d.rs != 4 || d.rn != 6 || d.rt != 5) {
        return 7;
    }

    /* CASP X0, X1, X2, X3, [X4] -> size=1 (64-bit pair) */
    uint32_t casp_x = 0x48207C82;
    if (!lse_decode(casp_x, &d) || d.type != LSE_TYPE_CASP || d.size != LSE_SIZE_64) {
        return 8;
    }

    /* Non-LSE instruction (e.g. NOP: 0xd503201f, ADD: 0x8b010000) */
    if (lse_decode(0xD503201F, &d)) return 9;
    if (lse_decode(0x8B010000, &d)) return 10;

    return 0;
}

#ifdef __cplusplus
}
#endif

#endif /* LSE_DECODE_H */
