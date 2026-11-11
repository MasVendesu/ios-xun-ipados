#!/usr/bin/env python3
"""
patch_kernel_pac.py - Pre-patch ARMv8.3 PAC instructions to native ARMv8.0 on Cortex-A73.

Cortex-A73 (Bengal SM6225) does not implement ARMv8.3 pointer authentication (FEAT_PAuth).
All non-hint PAC instructions (retab, retaa, pacia, pacda, autia, autda, blraa, braa, etc.)
cause Undefined Instruction exceptions.

This script implements the freestanding decoder from osfmk/arm64/pac_decode.h directly
on the compiled kernel Mach-O binary before packaging:
  - retab / retaa   -> ret (0xd65f03c0)
  - eretab / eretaa -> eret (0xd69f03e0)
  - braa / brab     -> br Xn (0xd61f0000 | (Rn << 5))
  - blraa / blrab   -> blr Xn (0xd63f0000 | (Rn << 5))
  - pac-* / aut-*   -> NOP (0xd503201f)
  - pacga           -> mov Xd, xzr (0xaa1f03e0 | Rd)
  - ldraa / ldrab   -> ldr / ldur / ldr_pre
"""

import sys, os, struct

A64_NOP        = 0xd503201f
A64_RET        = 0xd65f03c0
A64_ERET       = 0xd69f03e0
A64_BR_BASE    = 0xd61f0000
A64_BLR_BASE   = 0xd63f0000
A64_MOV_X_XZR  = 0xaa1f03e0
A64_LDR_UIMM   = 0xf9400000
A64_LDUR       = 0xf8400000
A64_LDR_PRE    = 0xf8400c00

def decode_and_patch(i):
    # 1. Fixed
    if i in (0xd65f0bff, 0xd65f0fff): return A64_RET, 'ret'
    if i in (0xd69f0bff, 0xd69f0fff): return A64_ERET, 'eret'
    
    # 2. braaz, brabz, blraaz, blrabz
    if (i & 0xfffffc1f) in (0xd61f081f, 0xd61f0c1f): return A64_BR_BASE | (i & 0x3e0), 'br'
    if (i & 0xfffffc1f) in (0xd63f081f, 0xd63f0c1f): return A64_BLR_BASE | (i & 0x3e0), 'blr'

    # 3. braa, brab, blraa, blrab
    if (i & 0xfffffc00) in (0xd71f0800, 0xd71f0c00): return A64_BR_BASE | (i & 0x3e0), 'br'
    if (i & 0xfffffc00) in (0xd73f0800, 0xd73f0c00): return A64_BLR_BASE | (i & 0x3e0), 'blr'

    # 4. Data-processing 1-source PAC: 0xdac1xxxx
    if (i & 0xffff0000) == 0xdac10000:
        opc = (i >> 10) & 0x3f
        rn = (i >> 5) & 31
        if opc < 8: return A64_NOP, 'nop'
        if opc <= 17 and rn == 31: return A64_NOP, 'nop'

    # 5. PACGA
    if (i & 0xffe0fc00) == 0x9ac03000:
        return A64_MOV_X_XZR | (i & 31), 'mov_xzr'

    # 6. LDRAA / LDRAB
    if (i & 0xff200400) == 0xf8200400:
        imm10 = (((i >> 22) & 1) << 9) | ((i >> 12) & 0x1ff)
        if imm10 & 0x200:
            imm10 -= 0x400
        off = imm10 * 8
        wb = (i >> 11) & 1
        rn = (i >> 5) & 31
        rt = i & 31
        if not wb:
            if 0 <= off <= 4088:
                return A64_LDR_UIMM | ((off >> 3) << 10) | (rn << 5) | rt, 'ldr'
            if -256 <= off < 0:
                return A64_LDUR | ((off & 0x1ff) << 12) | (rn << 5) | rt, 'ldur'
        else:
            if -256 <= off <= 255:
                return A64_LDR_PRE | ((off & 0x1ff) << 12) | (rn << 5) | rt, 'ldr_pre'

    # 7. MSR/MRS AP*Key*_EL1 (S3_0_C2_C{1..3}_op2)
    if (i & 0xffdff000) == 0xd5182000:
        crm = (i >> 8) & 15
        op2 = (i >> 5) & 7
        if ((crm in (1, 2)) and op2 < 4) or (crm == 3 and op2 < 2):
            if (i >> 21) & 1:
                return A64_MOV_X_XZR | (i & 31), 'mrs_apkey'
            else:
                return A64_NOP, 'msr_apkey'

    # 8. ARMv8.1 FEAT_PAN (MSR PAN, #imm / MSR PAN, Xt / MRS Xt, PAN)
    if (i & 0xfffff0ff) == 0xd500409f:
        return A64_NOP, 'msr_pan_imm'
    if (i & 0xffffffe0) == 0xd5184260:
        return A64_NOP, 'msr_pan_reg'
    if (i & 0xffffffe0) == 0xd5384260:
        return 0xd2a00800 | (i & 31), 'mrs_pan'

    # 9. ARMv8.3-A RCpc (LDAPR / LDAPRB / LDAPRH) -> LDAR / LDARB / LDARH (ARMv8.0)
    if (i & 0x3ffffc00) == 0x38bfc000:
        sz = (i >> 30) & 3
        rn = (i >> 5) & 31
        rt = i & 31
        ldar = (sz << 30) | 0x08dffc00 | (rn << 5) | rt
        return ldar, 'ldapr'

    # 10. ARMv8.4-A RCpc2 (LDAPUR / LDAPURB / LDAPURH / LDAPURSW) -> LDUR (ARMv8.0)
    if (i & 0x3fc00c00) == 0x19400000:
        ldur = (i & ~0x3fc00000) | 0x38400000
        return ldur, 'ldapur'

    # 11. ARMv8.4-A RCpc2 (STLUR / STLURB / STLURH) -> STUR (ARMv8.0)
    if (i & 0x3fc00c00) == 0x19000000:
        stur = (i & ~0x3fc00000) | 0x38000000
        return stur, 'stlur'

    # 12. Apple PAC failure assertion trap: BRK #0xc470 .. #0xc47f (e.g. 0xd4388e20 = brk #0xc471)
    # Clang emits software PAC checks (eor x16, x30, x30, lsl #1; tbz x16, #62; brk #0xc471).
    # Since Cortex-A73 has no PAC keys, neutralizing BRK #0xc47x to NOP prevents software PAC traps.
    if (i & 0xffe0001f) == 0xd4200000:
        imm16 = (i >> 5) & 0xffff
        if 0xc470 <= imm16 <= 0xc47f:
            return A64_NOP, 'brk_pac_fail'

    return None, None

def patch_kernel(kernel_path):
    print(f"Patching ARMv8.3 PAC instructions in {kernel_path} for Cortex-A73...")
    with open(kernel_path, 'rb') as f:
        data = bytearray(f.read())

    f_in = open(kernel_path, 'rb')
    f_in.seek(16)
    ncmds, sizeofcmds = struct.unpack('<II', f_in.read(8))
    f_in.seek(32)

    total_patched = 0
    stats = {}

    for _ in range(ncmds):
        cmd_start = f_in.tell()
        cmd, cmdsize = struct.unpack('<II', f_in.read(8))
        if cmd == 0x19: # LC_SEGMENT_64
            segname = f_in.read(16).decode('latin1').rstrip('\x00')
            vmaddr, vmsize, fileoff, filesize = struct.unpack('<QQQQ', f_in.read(32))
            maxprot, initprot, nsects, flags = struct.unpack('<IIII', f_in.read(16))
            if (initprot & 4) != 0 and filesize > 0:
                print(f"  Scanning executable segment '{segname}' (offset {hex(fileoff)}..{hex(fileoff+filesize)})...")
                seg_patched = 0
                for i in range(fileoff, fileoff + filesize, 4):
                    word = struct.unpack('<I', data[i:i+4])[0]
                    new_insn, name = decode_and_patch(word)
                    if new_insn is not None:
                        data[i:i+4] = struct.pack('<I', new_insn)
                        stats[name] = stats.get(name, 0) + 1
                        total_patched += 1
                        seg_patched += 1
                print(f"    -> patched {seg_patched} instructions in '{segname}'")
        f_in.seek(cmd_start + cmdsize)

    f_in.close()

    with open(kernel_path, 'wb') as f:
        f.write(data)

    print(f"Successfully patched {total_patched} total PAC instructions:")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"    {k:10}: {v}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: patch_kernel_pac.py <kernel_mach_o>")
        sys.exit(1)
    patch_kernel(sys.argv[1])
