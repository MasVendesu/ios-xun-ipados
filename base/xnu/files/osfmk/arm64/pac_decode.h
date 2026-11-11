/*
 * pac_decode.h  (HakiOS v2) - bo giai ma lenh PAC cho CPU ARMv8.0 (khong FEAT_PAuth)
 *
 * Header-only, freestanding (chi can <stdint.h>, <stdbool.h>) -> dung duoc trong EL1 handler.
 *
 * Quy uoc thiet ke: "khong bao gio ky con tro" => moi lenh pac-*, aut-*, xpac-* la identity.
 *   pac-*, aut-*, xpac-* -> NOP                   (Xd giu nguyen)
 *   retaa, retab         -> ret                   ; eretaa, eretab -> eret
 *   braa, brab, blraa... -> br / blr Xn
 *   pacga                -> mov Xd, xzr           (gia tri on dinh, khong phai rac)
 *   ldraa, ldrab         -> ldr / ldur (1 lenh) neu encode duoc, nguoc lai PAC_EMULATE_LDRA
 *   msr, mrs AP?Key?_EL1 -> nop / mov Xt, xzr
 *
 * CAC LENH HINT (paciasp, autiasp, pacibsp, xpaclri, pacia1716, bti...) KHONG BAO GIO TRAP
 * tren v8.0 vi chung nam trong HINT space va chay nhu NOP -> khong can xu ly.
 *
 * Cach dung trong handler (EC==0x00):
 *     pac_result_t r;
 *     switch (pac_decode(insn, &r)) {
 *     case PAC_PATCH:         self_patch(elr, r.new_insn); eret_retry; break;
 *     case PAC_EMULATE_LDRA:  emulate_ldra(regs, &r); regs->pc += 4; break;
 *     default: // khong phai PAC: chuyen cho luong SIGILL chuan
 *     }
 * LUU Y: neu lenh tai ELR da bi core khac va (khong con la UNDEFINED) thi handler phai
 * eret de chay lai chu khong duoc coi la loi (xem tai lieu race SMP).
 */
#ifndef PAC_DECODE_H
#define PAC_DECODE_H

#include <stdint.h>
#include <stdbool.h>

#define A64_NOP        0xd503201fu
#define A64_RET        0xd65f03c0u   /* ret (x30) */
#define A64_ERET       0xd69f03e0u
#define A64_BR_BASE    0xd61f0000u   /* | Rn << 5 */
#define A64_BLR_BASE   0xd63f0000u   /* | Rn << 5 */
#define A64_MOV_X_XZR  0xaa1f03e0u   /* | Rd      */
#define A64_LDR_UIMM   0xf9400000u   /* ldr  Xt,[Xn,#imm12*8]  */
#define A64_LDUR       0xf8400000u   /* ldur Xt,[Xn,#simm9]    */
#define A64_LDR_PRE    0xf8400c00u   /* ldr  Xt,[Xn,#simm9]!   */

typedef enum {
    PAC_NOT_PAC = 0,        /* khong phai lenh PAC: de nguyen cho luong UNDEF chuan   */
    PAC_PATCH,              /* thay bang r->new_insn (1 lenh, cung kich thuoc)        */
    PAC_EMULATE_LDRA        /* ldraa/ldrab khong encode duoc 1 lenh: phai emulate     */
} pac_action_t;

typedef struct {
    pac_action_t action;
    uint32_t     new_insn;   /* hop le khi PAC_PATCH                              */
    uint8_t      rt, rn;     /* hop le khi PAC_EMULATE_LDRA (31 = SP cho rn)      */
    int16_t      off;        /*   byte offset da nhan 8                           */
    bool         writeback;  /*   true neu pre-index (Xn += off truoc khi load)   */
    const char  *name;       /* ten lenh goc (de log)                             */
} pac_result_t;

static inline pac_action_t pac__patch(pac_result_t *r, const char *nm, uint32_t w)
{
    r->action = PAC_PATCH; r->new_insn = w; r->name = nm;
    return PAC_PATCH;
}

static inline pac_action_t pac_decode(uint32_t i, pac_result_t *r)
{
    static const char *const pac1_names[18] = {
        "pacia", "pacib", "pacda", "pacdb", "autia", "autib", "autda", "autdb",
        "paciza", "pacizb", "pacdza", "pacdzb", "autiza", "autizb", "autdza", "autdzb",
        "xpaci", "xpacd"
    };

    r->action = PAC_NOT_PAC; r->new_insn = 0; r->rt = r->rn = 0;
    r->off = 0; r->writeback = false; r->name = 0;

    /* ---- 1. Lenh co dinh hoan toan ------------------------------------------------ */
    switch (i) {
    case 0xd65f0bffu: return pac__patch(r, "retaa",  A64_RET);
    case 0xd65f0fffu: return pac__patch(r, "retab",  A64_RET);
    case 0xd69f0bffu: return pac__patch(r, "eretaa", A64_ERET);
    case 0xd69f0fffu: return pac__patch(r, "eretab", A64_ERET);
    default: break;
    }

    /* ---- 2. BRAAZ/BRABZ/BLRAAZ/BLRABZ  (Rm phai = 11111, Rn bat ky) ---------------- */
    switch (i & 0xfffffc1fu) {
    case 0xd61f081fu: return pac__patch(r, "braaz",  A64_BR_BASE  | (i & 0x3e0u));
    case 0xd61f0c1fu: return pac__patch(r, "brabz",  A64_BR_BASE  | (i & 0x3e0u));
    case 0xd63f081fu: return pac__patch(r, "blraaz", A64_BLR_BASE | (i & 0x3e0u));
    case 0xd63f0c1fu: return pac__patch(r, "blrabz", A64_BLR_BASE | (i & 0x3e0u));
    default: break;
    }

    /* ---- 3. BRAA/BRAB/BLRAA/BLRAB (Rm = modifier, bo qua) -------------------------- */
    switch (i & 0xfffffc00u) {
    case 0xd71f0800u: return pac__patch(r, "braa",  A64_BR_BASE  | (i & 0x3e0u));
    case 0xd71f0c00u: return pac__patch(r, "brab",  A64_BR_BASE  | (i & 0x3e0u));
    case 0xd73f0800u: return pac__patch(r, "blraa", A64_BLR_BASE | (i & 0x3e0u));
    case 0xd73f0c00u: return pac__patch(r, "blrab", A64_BLR_BASE | (i & 0x3e0u));
    default: break;
    }

    /* ---- 4. Nhom data-processing 1-source PAC: 0xdac1xxxx -------------------------- */
    if ((i & 0xffff0000u) == 0xdac10000u) {
        unsigned opc = (i >> 10) & 0x3f, rn = (i >> 5) & 31;
        if (opc < 8)                          /* pac/aut x {ia,ib,da,db}: Rn = modifier */
            return pac__patch(r, pac1_names[opc], A64_NOP);
        if (opc <= 17 && rn == 31)            /* 8..15: dang Z; 16/17: xpaci, xpacd -> Rn bat buoc = 31 */
            return pac__patch(r, pac1_names[opc], A64_NOP);
        return PAC_NOT_PAC;                   /* opcode khac / Rn sai = unallocated */
    }

    /* ---- 5. PACGA Xd, Xn, Xm|SP ----------------------------------------------------- */
    if ((i & 0xffe0fc00u) == 0x9ac03000u)
        return pac__patch(r, "pacga", A64_MOV_X_XZR | (i & 31u));

    /* ---- 6. LDRAA / LDRAB (offset & pre-index) -------------------------------------- */
    if ((i & 0xff200400u) == 0xf8200400u) {
        unsigned m   = (i >> 23) & 1;
        unsigned imm10 = ((i >> 22) & 1) << 9 | ((i >> 12) & 0x1ff);   /* S:imm9 */
        int32_t  off = (int32_t)((imm10 ^ 0x200u) - 0x200u) * 8;         /* sext10 * 8 */
        unsigned wb  = (i >> 11) & 1, rn = (i >> 5) & 31, rt = i & 31;
        const char *nm = m ? "ldrab" : "ldraa";

        if (!wb) {
            if (off >= 0)                      /* 0..4088 luon vua imm12*8 */
                return pac__patch(r, nm, A64_LDR_UIMM | ((uint32_t)(off >> 3) << 10) | rn << 5 | rt);
            if (off >= -256)                   /* ldur: -256..-8 (boi so cua 8) */
                return pac__patch(r, nm, A64_LDUR | (((uint32_t)off & 0x1ffu) << 12) | rn << 5 | rt);
        } else {
            if (off >= -256 && off <= 255)
                return pac__patch(r, nm, A64_LDR_PRE | (((uint32_t)off & 0x1ffu) << 12) | rn << 5 | rt);
        }
        r->action = PAC_EMULATE_LDRA; r->name = nm;
        r->rt = (uint8_t)rt; r->rn = (uint8_t)rn; r->off = (int16_t)off; r->writeback = wb;
        return PAC_EMULATE_LDRA;
    }

    /* ---- 7. MSR/MRS khoa PAC: S3_0_C2_C{1..3}_op2 ------------------------------------ */
    if ((i & 0xffdff000u) == 0xd5182000u) {
        unsigned crm = (i >> 8) & 15, op2 = (i >> 5) & 7;
        bool ok = ((crm == 1 || crm == 2) && op2 < 4) || (crm == 3 && op2 < 2);
        if (ok) {
            if ((i >> 21) & 1)  return pac__patch(r, "mrs APxKey", A64_MOV_X_XZR | (i & 31u));
            else                return pac__patch(r, "msr APxKey", A64_NOP);
        }
    }

    /* ---- 8. LDAPR / LDAPRB / LDAPRH (ARMv8.3-A RCpc) -> LDAR / LDARB / LDARH (ARMv8.0) ---- */
    if ((i & 0x3ffffc00u) == 0x38bfc000u) {
        unsigned sz = (i >> 30) & 3, rn = (i >> 5) & 31, rt = i & 31;
        uint32_t ldar = ((uint32_t)sz << 30) | 0x08dffc00u | ((uint32_t)rn << 5) | (uint32_t)rt;
        return pac__patch(r, "ldapr", ldar);
    }

    /* ---- 9. LDAPUR (ARMv8.4-A RCpc2) -> LDUR (ARMv8.0) ----------------------------------- */
    if ((i & 0x3fc00c00u) == 0x19400000u) {
        uint32_t ldur = (i & ~0x3fc00000u) | 0x38400000u;
        return pac__patch(r, "ldapur", ldur);
    }

    /* ---- 10. STLUR (ARMv8.4-A RCpc2) -> STUR (ARMv8.0) ---------------------------------- */
    if ((i & 0x3fc00c00u) == 0x19000000u) {
        uint32_t stur = (i & ~0x3fc00000u) | 0x38000000u;
        return pac__patch(r, "stlur", stur);
    }

    return PAC_NOT_PAC;
}

#endif /* PAC_DECODE_H */
