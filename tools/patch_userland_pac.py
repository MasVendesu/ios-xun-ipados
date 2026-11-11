#!/usr/bin/env python3
"""
tools/patch_userland_pac.py

Industrial-Grade Offline Static Sanitizer for iOS Userland Mach-O Binaries & Dynamic Libraries.
Converts ARMv8.3-A PAC and ARMv8.3/v8.4 RCpc instructions to native ARMv8.0 equivalents
so that iOS userland binaries (launchd, dyld, system daemons, dylibs, frameworks, test suites)
run natively on Cortex-A73 Gold cores (Qualcomm Snapdragon 685 SM6225 Bengal) without
triggering Undefined Instruction exceptions (SIGILL) or EL1 trap storms.

Conversions:
  - retab / retaa                 -> ret (0xd65f03c0)
  - retab register variants (0xd65f0800..0xd65f0fff) -> standard ret (0xd65f03c0 or ret xN)
  - eretab / eretaa               -> eret (0xd69f03e0)
  - braa / brab / braaz / brabz   -> br Xn
  - blraa / blrab / blraaz / blrabz -> blr Xn
  - pac-* / aut-* / xpac-*        -> NOP (0xd503201f)
  - pacga                         -> mov Xd, xzr (0xaa1f03e0 | Rd)
  - ldraa / ldrab                 -> ldr / ldur / ldr_pre
  - msr/mrs AP*Key*_EL1           -> NOP / mov Xd, xzr
  - msr/mrs PAN                   -> NOP / mrs_pan
  - ldapr / ldaprb / ldaprh       -> ldar / ldarb / ldarh
  - ldapur / ldapurb / ldapurh    -> ldur / ldurb / ldurh
  - stlur / stlurb / stlurh       -> stur / sturb / sturh
  - brk #0xc47x (PAC assertions)  -> NOP (0xd503201f)

Ad-hoc CodeDirectory SHA-256 Slot Recalculation:
  When LC_CODE_SIGNATURE is present, modified 4KB code pages are re-hashed (SHA-256 / SHA-1)
  and their slots in each CS_CodeDirectory are updated in-place so XNU mach_loader and AMFI
  page validation accept the binary without SIGKILL.
"""

import sys
import os
import struct
import hashlib
import argparse
import unittest
import tempfile
import shutil

# ARM64 Standard Opcode Encodings
A64_NOP        = 0xd503201f
A64_RET        = 0xd65f03c0
A64_RET_BASE   = 0xd65f0000
A64_ERET       = 0xd69f03e0
A64_BR_BASE    = 0xd61f0000
A64_BLR_BASE   = 0xd63f0000
A64_MOV_X_XZR  = 0xaa1f03e0
A64_LDR_UIMM   = 0xf9400000
A64_LDUR       = 0xf8400000
A64_LDR_PRE    = 0xf8400c00

# Mach-O & FAT Magic Numbers
MH_MAGIC_64       = 0xfeedfacf  # Little-endian 64-bit Mach-O
MH_CIGAM_64       = 0xcffaedfe  # Big-endian 64-bit Mach-O
FAT_MAGIC         = 0xcafebabe  # Big-endian Universal FAT
FAT_CIGAM         = 0xbebafeca  # Little-endian Universal FAT
FAT_MAGIC_64      = 0xcafebabf  # Big-endian 64-bit FAT
FAT_CIGAM_64      = 0xbfbafeca  # Little-endian 64-bit FAT

# CPU Types
CPU_TYPE_ARM64    = 0x0100000c
CPU_TYPE_ARM64_32 = 0x0200000c

# Load Command Types
LC_SEGMENT_64     = 0x19
LC_CODE_SIGNATURE = 0x1d

# Mach-O Target File Types
MH_OBJECT         = 0x1
MH_EXECUTE        = 0x2
MH_DYLIB          = 0x6
MH_DYLINKER       = 0x7
MH_BUNDLE         = 0x8
TARGET_FILETYPES  = {MH_EXECUTE: "MH_EXECUTE", MH_DYLIB: "MH_DYLIB",
                     MH_DYLINKER: "MH_DYLINKER", MH_BUNDLE: "MH_BUNDLE"}

# Code Signing Blob Magics & Slot Types
CSMAGIC_CODEDIRECTORY      = 0xfade0c02
CSMAGIC_EMBEDDED_SIGNATURE = 0xfade0cc0
CSSLOT_CODEDIRECTORY       = 0
CSSLOT_ALTERNATE_CODEDIRS  = 0x1000

# Hash Types
CS_HASHTYPE_SHA1               = 1
CS_HASHTYPE_SHA256             = 2
CS_HASHTYPE_SHA256_TRUNCATED   = 3
CS_HASHTYPE_SHA384             = 4


def decode_and_patch(i):
    """
    Decodes an ARM64 instruction word and converts ARMv8.3 PAC and RCpc/RCpc2
    instructions to ARMv8.0 equivalents executable on Cortex-A73.
    Returns (replacement_insn, name) or (None, None) if unchanged.
    """
    # 1. RETAA / RETAB and register variants (0xd65f0800..0xd65f0fff)
    if 0xd65f0800 <= i <= 0xd65f0fff:
        rn = (i >> 5) & 31
        if rn in (30, 31):
            return A64_RET, 'ret'
        else:
            return A64_RET_BASE | (rn << 5), f'ret_x{rn}'

    # 2. ERETAA / ERETAB and variants (0xd69f0800..0xd69f0fff)
    if 0xd69f0800 <= i <= 0xd69f0fff:
        return A64_ERET, 'eret'

    # 3. braaz, brabz, blraaz, blrabz
    if (i & 0xfffffc1f) in (0xd61f081f, 0xd61f0c1f):
        return A64_BR_BASE | (i & 0x3e0), 'br'
    if (i & 0xfffffc1f) in (0xd63f081f, 0xd63f0c1f):
        return A64_BLR_BASE | (i & 0x3e0), 'blr'

    # 4. braa, brab, blraa, blrab
    if (i & 0xfffffc00) in (0xd71f0800, 0xd71f0c00):
        return A64_BR_BASE | (i & 0x3e0), 'br'
    if (i & 0xfffffc00) in (0xd73f0800, 0xd73f0c00):
        return A64_BLR_BASE | (i & 0x3e0), 'blr'

    # 5. Data-processing 1-source PAC: 0xdac1xxxx (pacia, pacda, autia, autda, paciza, etc.)
    if (i & 0xffff0000) == 0xdac10000:
        opc = (i >> 10) & 0x3f
        rn = (i >> 5) & 31
        rd = i & 31
        # hakios/claude: autia/autib/autda/autdb (opc 4..7) and autiza.. (opc 12..15) and xpaci/xpacd (opc 16..17)
        # Replacing with NOP leaves dirty upper PAC bits. On ARMv8.0 Cortex-A73, mask off bits [63:48]
        # using A64 logical immediate: and Xd, Xd, #0x0000ffffffffffff (0x9240bc00 | (rd << 5) | rd).
        if (4 <= opc <= 7) or (12 <= opc <= 17):
            return (0x9240bc00 | (rd << 5) | rd), f'strip_pac_x{rd}'
        elif opc < 4 or (opc <= 11 and rn == 31):
            return A64_NOP, 'nop_pac'

    # 6. PACGA (Pointer Authentication Code Generic)
    if (i & 0xffe0fc00) == 0x9ac03000:
        return A64_MOV_X_XZR | (i & 31), 'mov_xzr'

    # 7. LDRAA / LDRAB (Load authenticated pointer)
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

    # 8. MSR/MRS AP*Key*_EL1 (Key register access)
    if (i & 0xffdff000) == 0xd5182000:
        crm = (i >> 8) & 15
        op2 = (i >> 5) & 7
        if ((crm in (1, 2)) and op2 < 4) or (crm == 3 and op2 < 2):
            if (i >> 21) & 1:
                return A64_MOV_X_XZR | (i & 31), 'mrs_apkey'
            else:
                return A64_NOP, 'msr_apkey'

    # 9. ARMv8.1 FEAT_PAN (Privileged Access Never)
    if (i & 0xfffff0ff) == 0xd500409f:
        return A64_NOP, 'msr_pan_imm'
    if (i & 0xffffffe0) == 0xd5184260:
        return A64_NOP, 'msr_pan_reg'
    if (i & 0xffffffe0) == 0xd5384260:
        return 0xd2a00800 | (i & 31), 'mrs_pan'

    # 10. ARMv8.3-A RCpc (LDAPR / LDAPRB / LDAPRH) -> LDAR / LDARB / LDARH (ARMv8.0)
    if (i & 0x3ffffc00) == 0x38bfc000:
        sz = (i >> 30) & 3
        rn = (i >> 5) & 31
        rt = i & 31
        ldar = (sz << 30) | 0x08dffc00 | (rn << 5) | rt
        return ldar, 'ldapr'

    # 11. ARMv8.4-A RCpc2 (LDAPUR / LDAPURB / LDAPURH / LDAPURSW) -> LDUR (ARMv8.0)
    if (i & 0x3fc00c00) == 0x19400000:
        ldur = (i & ~0x3fc00000) | 0x38400000
        return ldur, 'ldapur'

    # 12. ARMv8.4-A RCpc2 (STLUR / STLURB / STLURH) -> STUR (ARMv8.0)
    if (i & 0x3fc00c00) == 0x19000000:
        stur = (i & ~0x3fc00000) | 0x38000000
        return stur, 'stlur'

    # 13. Clang Software PAC Failure Assertion Traps: BRK #0xc470 .. #0xc47f
    if (i & 0xffe0001f) == 0xd4200000:
        imm16 = (i >> 5) & 0xffff
        if 0xc470 <= imm16 <= 0xc47f:
            return A64_NOP, 'brk_pac_fail'

    return None, None


def check_macho_type(buf):
    """
    Determines whether a byte buffer starts with a Mach-O 64-bit or FAT binary header.
    Returns (is_macho, 'thin_64' | 'fat' | None).
    """
    if len(buf) < 4:
        return False, None
    magic_le, = struct.unpack_from('<I', buf, 0)
    magic_be, = struct.unpack_from('>I', buf, 0)

    if magic_le == MH_MAGIC_64:
        return True, 'thin_64'
    if magic_be in (FAT_MAGIC, FAT_CIGAM, FAT_MAGIC_64, FAT_CIGAM_64) or \
       magic_le in (FAT_MAGIC, FAT_CIGAM, FAT_MAGIC_64, FAT_CIGAM_64):
        return True, 'fat'
    return False, None


def parse_fat_slices(data):
    """
    Parses a Universal FAT binary and returns a list of (cputype, cpusubtype, offset, size) tuples.
    Supports FAT_MAGIC, FAT_CIGAM, FAT_MAGIC_64, and FAT_CIGAM_64.
    """
    if len(data) < 8:
        return []
    magic_be, = struct.unpack_from('>I', data, 0)
    magic_le, = struct.unpack_from('<I', data, 0)
    slices = []

    if magic_be == FAT_MAGIC:
        nfat_arch, = struct.unpack_from('>I', data, 4)
        off = 8
        for _ in range(nfat_arch):
            if off + 20 > len(data): break
            cputype, cpusubtype, offset, size, align = struct.unpack_from('>5I', data, off)
            slices.append((cputype, cpusubtype, offset, size))
            off += 20
    elif magic_le == FAT_MAGIC:
        nfat_arch, = struct.unpack_from('<I', data, 4)
        off = 8
        for _ in range(nfat_arch):
            if off + 20 > len(data): break
            cputype, cpusubtype, offset, size, align = struct.unpack_from('<5I', data, off)
            slices.append((cputype, cpusubtype, offset, size))
            off += 20
    elif magic_be == FAT_MAGIC_64:
        nfat_arch, = struct.unpack_from('>I', data, 4)
        off = 8
        for _ in range(nfat_arch):
            if off + 32 > len(data): break
            cputype, cpusubtype, offset, size, align, reserved = struct.unpack_from('>IIQQII', data, off)
            slices.append((cputype, cpusubtype, offset, size))
            off += 32
    elif magic_le == FAT_MAGIC_64:
        nfat_arch, = struct.unpack_from('<I', data, 4)
        off = 8
        for _ in range(nfat_arch):
            if off + 32 > len(data): break
            cputype, cpusubtype, offset, size, align, reserved = struct.unpack_from('<IIQQII', data, off)
            slices.append((cputype, cpusubtype, offset, size))
            off += 32

    return slices


def update_code_directory(data, slice_offset, slice_size, sig_dataoff, sig_datasize, dry_run=False, verbose=False):
    """
    Recalculates code page hashes for all CS_CodeDirectory blobs inside LC_CODE_SIGNATURE.
    Ensures XNU mach_loader and AMFI validate the binary pages without signature failure.
    Returns the number of hash slots updated.
    """
    sig_offset = slice_offset + sig_dataoff
    if sig_offset + 8 > len(data):
        return 0

    magic, = struct.unpack_from('>I', data, sig_offset)
    cd_blobs = []

    if magic == CSMAGIC_EMBEDDED_SIGNATURE:
        length, count = struct.unpack_from('>II', data, sig_offset + 4)
        for i in range(count):
            entry_offset = sig_offset + 12 + i * 8
            if entry_offset + 8 > len(data):
                break
            slot_type, blob_sub_off = struct.unpack_from('>II', data, entry_offset)
            blob_off = sig_offset + blob_sub_off
            if blob_off + 8 <= len(data):
                b_magic, = struct.unpack_from('>I', data, blob_off)
                if b_magic == CSMAGIC_CODEDIRECTORY:
                    cd_blobs.append(blob_off)
    elif magic == CSMAGIC_CODEDIRECTORY:
        cd_blobs.append(sig_offset)

    total_slots_updated = 0

    for cd_off in cd_blobs:
        if cd_off + 40 > len(data):
            continue
        (b_magic, length, version, flags, hashOffset,
         identOffset, nSpecialSlots, nCodeSlots, codeLimit) = struct.unpack_from('>9I', data, cd_off)
        hashSize, hashType, platform, pageSize = struct.unpack_from('>4B', data, cd_off + 36)

        limit = codeLimit
        if version >= 0x20300 and limit == 0 and cd_off + 64 <= len(data):
            codeLimit64, = struct.unpack_from('>Q', data, cd_off + 56)
            if codeLimit64 > 0:
                limit = codeLimit64
        if limit == 0 or limit > slice_size:
            limit = slice_size

        page_size = (1 << pageSize) if pageSize > 0 else 4096

        hasher_factory = None
        truncate_len = None
        if hashType == CS_HASHTYPE_SHA1:
            hasher_factory = hashlib.sha1
        elif hashType == CS_HASHTYPE_SHA256:
            hasher_factory = hashlib.sha256
        elif hashType == CS_HASHTYPE_SHA256_TRUNCATED:
            hasher_factory = hashlib.sha256
            truncate_len = 20
        elif hashType == CS_HASHTYPE_SHA384:
            hasher_factory = hashlib.sha384

        if not hasher_factory:
            if verbose:
                print(f"    [CodeDirectory] Skipping unsupported hashType {hashType}")
            continue

        blob_slots_updated = 0
        for slot in range(nCodeSlots):
            p_start = slot * page_size
            p_end = min(p_start + page_size, limit)
            if p_start >= limit or (slice_offset + p_start) >= len(data):
                break
            page_slice = data[slice_offset + p_start : slice_offset + p_end]
            h = hasher_factory(page_slice).digest()
            if truncate_len:
                h = h[:truncate_len]
            digest = h[:hashSize]

            slot_loc = cd_off + hashOffset + slot * hashSize
            if slot_loc + hashSize > len(data):
                break

            old_slot = bytes(data[slot_loc : slot_loc + hashSize])
            if old_slot != digest:
                if verbose:
                    print(f"    [CodeDirectory] Page {slot} (0x{p_start:x}..0x{p_end:x}): slot updated ({old_slot.hex()[:8]}... -> {digest.hex()[:8]}...)")
                if not dry_run:
                    data[slot_loc : slot_loc + hashSize] = digest
                blob_slots_updated += 1

        total_slots_updated += blob_slots_updated
        if verbose and blob_slots_updated > 0:
            print(f"    [CodeDirectory] Blob at 0x{cd_off:x}: {blob_slots_updated} slots updated (hashType={hashType}, pageSize={page_size})")

    return total_slots_updated


def patch_macho_slice(data, slice_offset, slice_size, dry_run=False, verbose=False):
    """
    Sanitizes an individual 64-bit Mach-O slice (standalone or inside a FAT binary).
    Returns (total_patched, slots_updated, stats_dict).
    """
    if slice_offset + 32 > len(data):
        return 0, 0, {}

    # If dry_run is requested, operate on a copy so calculations are 100% accurate
    # without mutating the caller's buffer.
    buf = bytearray(data) if dry_run else data

    magic, = struct.unpack_from('<I', buf, slice_offset)
    if magic != MH_MAGIC_64:
        return 0, 0, {}

    cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved = struct.unpack_from('<7I', buf, slice_offset + 4)

    cmd_off = slice_offset + 32
    total_patched = 0
    stats = {}
    sig_info = None

    for _ in range(ncmds):
        if cmd_off + 8 > slice_offset + slice_size or cmd_off + 8 > len(buf):
            break
        cmd, cmdsize = struct.unpack_from('<II', buf, cmd_off)
        if cmdsize < 8 or cmd_off + cmdsize > len(buf):
            break

        if cmd == LC_SEGMENT_64:
            segname = buf[cmd_off + 8 : cmd_off + 24].split(b'\0')[0].decode('latin1', errors='replace')
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<4Q', buf, cmd_off + 24)
            maxprot, initprot, nsects, s_flags = struct.unpack_from('<4I', buf, cmd_off + 56)

            if (initprot & 4) != 0 and filesize > 0:
                scan_ranges = []
                if nsects > 0:
                    sect_ptr = cmd_off + 72
                    for _ in range(nsects):
                        if sect_ptr + 80 > cmd_off + cmdsize:
                            break
                        s_name = buf[sect_ptr : sect_ptr + 16].split(b'\0')[0].decode('latin1', errors='replace')
                        s_addr, s_size = struct.unpack_from('<2Q', buf, sect_ptr + 32)
                        s_off, s_align, s_reloff, s_nreloc, s_flags_val = struct.unpack_from('<5I', buf, sect_ptr + 48)

                        is_code = ((s_flags_val & 0x80000400) != 0 or
                                   s_name.startswith('__text') or
                                   s_name in ('__stubs', '__auth_stubs', '__stub_helper'))
                        if is_code and s_size > 0:
                            scan_ranges.append((s_off, s_size, f"{segname}:{s_name}"))
                        sect_ptr += 80

                if not scan_ranges:
                    scan_ranges.append((fileoff, filesize, segname))

                seg_patched = 0
                for r_off, r_sz, r_label in scan_ranges:
                    r_start = slice_offset + r_off
                    r_end = min(r_start + r_sz, slice_offset + slice_size, len(buf))
                    for pos in range(r_start, r_end - 3, 4):
                        word, = struct.unpack_from('<I', buf, pos)
                        new_insn, name = decode_and_patch(word)
                        if new_insn is not None:
                            if verbose:
                                rel_pos = pos - slice_offset
                                print(f"    [{r_label}] 0x{rel_pos:08x}: 0x{word:08x} -> 0x{new_insn:08x} ({name})")
                            struct.pack_into('<I', buf, pos, new_insn)
                            stats[name] = stats.get(name, 0) + 1
                            total_patched += 1
                            seg_patched += 1

                if seg_patched > 0 and verbose:
                    print(f"  [{segname}] Patched {seg_patched} PAC/RCpc instructions")

        elif cmd == LC_CODE_SIGNATURE:
            dataoff, datasize = struct.unpack_from('<II', buf, cmd_off + 8)
            sig_info = (dataoff, datasize)

        cmd_off += cmdsize

    slots_updated = 0
    if sig_info:
        dataoff, datasize = sig_info
        if dataoff > 0 and datasize > 0:
            slots_updated = update_code_directory(buf, slice_offset, slice_size, dataoff, datasize,
                                                  dry_run=False, verbose=verbose)

    return total_patched, slots_updated, stats


def patch_file(path, dry_run=False, verbose=False):
    """
    Sanitizes a thin or Universal FAT Mach-O binary file.
    Returns (success, total_patched, slots_updated).
    """
    if not os.path.isfile(path):
        print(f"[-] File not found: {path}")
        return False, 0, 0

    try:
        with open(path, 'rb') as f:
            data = bytearray(f.read())
    except Exception as e:
        print(f"[-] Cannot read {path}: {e}")
        return False, 0, 0

    if len(data) < 32:
        return False, 0, 0

    is_macho, m_type = check_macho_type(data)
    if not is_macho:
        if verbose:
            print(f"[-] Not a recognized Mach-O or FAT binary: {path}")
        return False, 0, 0

    total_patched = 0
    total_slots_updated = 0
    combined_stats = {}

    if m_type == 'thin_64':
        p_cnt, s_cnt, stats = patch_macho_slice(data, 0, len(data), dry_run=dry_run, verbose=verbose)
        total_patched += p_cnt
        total_slots_updated += s_cnt
        for k, v in stats.items():
            combined_stats[k] = combined_stats.get(k, 0) + v
    elif m_type == 'fat':
        slices = parse_fat_slices(data)
        if verbose:
            print(f"  FAT binary with {len(slices)} slice(s)")
        for cputype, cpusubtype, offset, size in slices:
            if cputype in (CPU_TYPE_ARM64, CPU_TYPE_ARM64_32):
                if verbose:
                    print(f"  Sanitizing ARM64 FAT slice at offset 0x{offset:x} (size {size} bytes)")
                p_cnt, s_cnt, stats = patch_macho_slice(data, offset, size, dry_run=dry_run, verbose=verbose)
                total_patched += p_cnt
                total_slots_updated += s_cnt
                for k, v in stats.items():
                    combined_stats[k] = combined_stats.get(k, 0) + v
            else:
                if verbose:
                    print(f"  Skipping non-ARM64 slice (cputype 0x{cputype:08x}) at offset 0x{offset:x}")

    if total_patched > 0 or total_slots_updated > 0:
        if not dry_run:
            try:
                with open(path, 'wb') as f:
                    f.write(data)
            except Exception as e:
                print(f"[-] Failed to write back changes to {path}: {e}")
                return False, total_patched, total_slots_updated
        status_tag = "[DRY-RUN]" if dry_run else "[OK]"
        print(f"{status_tag} {os.path.basename(path)}: Total {total_patched} instructions neutralized, {total_slots_updated} CodeDirectory slots updated:")
        for k, v in sorted(combined_stats.items(), key=lambda x: -x[1]):
            print(f"       {k:14}: {v}")
        return True, total_patched, total_slots_updated
    else:
        if verbose:
            print(f"[-] {os.path.basename(path)}: No ARMv8.3 PAC instructions found.")
        return False, 0, 0


def patch_binary(path, dry_run=False, verbose=False):
    """
    Backward-compatible alias for patch_file.
    """
    success, _, _ = patch_file(path, dry_run=dry_run, verbose=verbose)
    return success


def scan_and_patch_directory(dir_path, dry_run=False, verbose=False):
    """
    Recursively scans a directory for 64-bit Mach-O binaries (MH_EXECUTE, MH_DYLIB,
    MH_DYLINKER, MH_BUNDLE) and Universal FAT binaries, sanitizing each one.
    """
    print(f"Scanning directory for userland Mach-O binaries: {dir_path}")
    total_files_scanned = 0
    macho_found = 0
    binaries_patched = 0
    total_instructions = 0
    total_slots = 0
    visited_inodes = set()

    for root, dirs, files in os.walk(dir_path):
        for file in sorted(files):
            total_files_scanned += 1
            file_path = os.path.join(root, file)

            try:
                st = os.stat(file_path)
                inode_key = (st.st_dev, st.st_ino)
                if inode_key in visited_inodes:
                    continue
                visited_inodes.add(inode_key)
            except Exception:
                pass

            try:
                with open(file_path, 'rb') as f:
                    magic_buf = f.read(4)
                if len(magic_buf) < 4:
                    continue
            except Exception:
                continue

            is_macho, m_type = check_macho_type(magic_buf)
            if not is_macho:
                continue

            is_target = False
            try:
                with open(file_path, 'rb') as f:
                    header = f.read(64)
                if m_type == 'thin_64':
                    if len(header) >= 16:
                        ft, = struct.unpack_from('<I', header, 12)
                        if ft in TARGET_FILETYPES:
                            is_target = True
                elif m_type == 'fat':
                    with open(file_path, 'rb') as f:
                        fat_full = f.read(4096)
                    slices = parse_fat_slices(fat_full)
                    for cputype, cpusubtype, offset, size in slices:
                        if cputype in (CPU_TYPE_ARM64, CPU_TYPE_ARM64_32):
                            if offset + 16 <= len(fat_full):
                                s_ft, = struct.unpack_from('<I', fat_full, offset + 12)
                                if s_ft in TARGET_FILETYPES:
                                    is_target = True
                                    break
                            else:
                                is_target = True
                                break
            except Exception:
                continue

            if not is_target:
                continue

            macho_found += 1
            success, p_cnt, s_cnt = patch_file(file_path, dry_run=dry_run, verbose=verbose)
            if success:
                binaries_patched += 1
                total_instructions += p_cnt
                total_slots += s_cnt

    action_str = "Simulated sanitization" if dry_run else "Sanitization"
    print("\n" + "=" * 60)
    print(f"{action_str} Summary:")
    print(f"  Scanned files:                     {total_files_scanned}")
    print(f"  Target 64-bit Mach-O binaries:     {macho_found}")
    print(f"  Patched binaries:                  {binaries_patched}")
    print(f"  Total instructions neutralized:    {total_instructions}")
    print(f"  Total CodeDirectory slots updated: {total_slots}")
    print("=" * 60)
    return binaries_patched, total_instructions, total_slots


# ==============================================================================
# Self-Contained Unit Test Suite
# ==============================================================================

class TestPacSanitizer(unittest.TestCase):
    """
    Self-contained unit test suite testing instruction decoding, Mach-O slice
    patching, Universal FAT parsing, and CodeDirectory SHA-256 recalculation.
    """

    def test_decode_retab_and_register_variants(self):
        # Standard RETAA / RETAB
        new_i, name = decode_and_patch(0xd65f0bff)
        self.assertEqual(new_i, A64_RET)
        self.assertEqual(name, 'ret')

        new_i, name = decode_and_patch(0xd65f0fff)
        self.assertEqual(new_i, A64_RET)
        self.assertEqual(name, 'ret')

        # Register variants (0xd65f0800..0xd65f0fff)
        # Rn=0 -> ret x0 (0xd65f0000)
        new_i, name = decode_and_patch(0xd65f0800)
        self.assertEqual(new_i, 0xd65f0000)
        self.assertEqual(name, 'ret_x0')

        # Rn=1 -> ret x1 (0xd65f0020)
        new_i, name = decode_and_patch(0xd65f0820)
        self.assertEqual(new_i, 0xd65f0020)
        self.assertEqual(name, 'ret_x1')

        # Rn=16 -> ret x16 (0xd65f0200)
        new_i, name = decode_and_patch(0xd65f0a00)
        self.assertEqual(new_i, 0xd65f0200)
        self.assertEqual(name, 'ret_x16')

        # Rn=30 -> ret x30 (0xd65f03c0)
        new_i, name = decode_and_patch(0xd65f0bc0)
        self.assertEqual(new_i, A64_RET)
        self.assertEqual(name, 'ret')

        # Rn=31 -> ret (0xd65f03c0)
        new_i, name = decode_and_patch(0xd65f0be0)
        self.assertEqual(new_i, A64_RET)
        self.assertEqual(name, 'ret')

    def test_decode_eretab(self):
        new_i, name = decode_and_patch(0xd69f0bff)
        self.assertEqual(new_i, A64_ERET)
        self.assertEqual(name, 'eret')

        new_i, name = decode_and_patch(0xd69f0fff)
        self.assertEqual(new_i, A64_ERET)
        self.assertEqual(name, 'eret')

    def test_decode_rcpc_and_rcpc2(self):
        # LDAPR 32-bit (sz=2, rn=1, rt=0) -> LDAR 32-bit
        # 0xb8bfc020 -> 0x88dffc20
        new_i, name = decode_and_patch(0xb8bfc020)
        self.assertEqual(new_i, 0x88dffc20)
        self.assertEqual(name, 'ldapr')

        # LDAPR 64-bit (sz=3, rn=1, rt=0) -> LDAR 64-bit
        # 0xf8bfc020 -> 0xc8dffc20
        new_i, name = decode_and_patch(0xf8bfc020)
        self.assertEqual(new_i, 0xc8dffc20)
        self.assertEqual(name, 'ldapr')

        # LDAPUR -> LDUR (0x19400000 -> 0x38400000)
        new_i, name = decode_and_patch(0x19400000)
        self.assertEqual(new_i, 0x38400000)
        self.assertEqual(name, 'ldapur')

        # STLUR -> STUR (0x19000000 -> 0x38000000)
        new_i, name = decode_and_patch(0x19000000)
        self.assertEqual(new_i, 0x38000000)
        self.assertEqual(name, 'stlur')

    def test_decode_brk_pac_traps(self):
        # BRK #0xc470 -> NOP
        new_i, name = decode_and_patch(0xd4388e00)
        self.assertEqual(new_i, A64_NOP)
        self.assertEqual(name, 'brk_pac_fail')

        # BRK #0xc471 (Apple Clang PAC assertion trap) -> NOP
        new_i, name = decode_and_patch(0xd4388e20)
        self.assertEqual(new_i, A64_NOP)
        self.assertEqual(name, 'brk_pac_fail')

        # BRK #0xc47f -> NOP
        new_i, name = decode_and_patch(0xd4388fe0)
        self.assertEqual(new_i, A64_NOP)
        self.assertEqual(name, 'brk_pac_fail')

        # Non-PAC traps must NOT be touched
        new_i, name = decode_and_patch(0xd4200000) # brk #0
        self.assertIsNone(new_i)

        new_i, name = decode_and_patch(0xd4200020) # brk #1
        self.assertIsNone(new_i)

        new_i, name = decode_and_patch(0xd4388de0) # brk #0xc46f (boundary)
        self.assertIsNone(new_i)

        new_i, name = decode_and_patch(0xd4389000) # brk #0xc480 (boundary)
        self.assertIsNone(new_i)

    def test_decode_branch_and_data_pac(self):
        # braaz x16 (rn=16, rn<<5 = 0x200) -> br x16
        new_i, name = decode_and_patch(0xd61f081f | 0x200)
        self.assertEqual(new_i, 0xd61f0200)
        self.assertEqual(name, 'br')

        # blraaz x16 -> blr x16
        new_i, name = decode_and_patch(0xd63f081f | 0x200)
        self.assertEqual(new_i, 0xd63f0200)
        self.assertEqual(name, 'blr')

        # PACIA / AUTIA (0xdac1xxxx) -> NOP
        new_i, name = decode_and_patch(0xdac10000)
        self.assertEqual(new_i, A64_NOP)
        self.assertEqual(name, 'nop_pac')

        # PACGA -> mov Xd, xzr
        new_i, name = decode_and_patch(0x9ac03000 | 5) # Rd=5
        self.assertEqual(new_i, A64_MOV_X_XZR | 5)
        self.assertEqual(name, 'mov_xzr')

    def test_unaffected_standard_instructions(self):
        # Standard ARMv8.0 NOP
        new_i, name = decode_and_patch(0xd503201f)
        self.assertIsNone(new_i)

        # Standard ARMv8.0 RET
        new_i, name = decode_and_patch(0xd65f03c0)
        self.assertIsNone(new_i)

        # MOV x0, x0
        new_i, name = decode_and_patch(0xaa0003e0)
        self.assertIsNone(new_i)

    def _create_synthetic_macho_with_cs(self):
        """
        Creates a synthetic thin 64-bit ARM64E Mach-O binary buffer with:
        - LC_SEGMENT_64 (__TEXT) containing a __text section at offset 0x200
        - PAC instructions placed inside __text
        - LC_CODE_SIGNATURE with CS_SuperBlob + CS_CodeDirectory (SHA-256)
        """
        data = bytearray(8192)
        # Header (32 bytes)
        struct.pack_into('<8I', data, 0,
            MH_MAGIC_64,
            CPU_TYPE_ARM64,
            0x80000002,     # CPU_SUBTYPE_ARM64E
            MH_EXECUTE,     # filetype
            2,              # ncmds
            152 + 16,       # sizeofcmds (LC_SEGMENT_64 + LC_CODE_SIGNATURE)
            0, 0
        )
        # LC_SEGMENT_64 (72 bytes + 1 section of 80 bytes = 152 bytes)
        struct.pack_into('<II16s4Q4I', data, 32,
            LC_SEGMENT_64, 152, b'__TEXT\0\0\0\0\0\0\0\0\0\0',
            0x100000000, 0x1000, 0, 0x1000, 7, 5, 1, 0
        )
        # Section __text (80 bytes)
        struct.pack_into('<16s16s2Q8I', data, 32 + 72,
            b'__text\0\0\0\0\0\0\0\0\0\0', b'__TEXT\0\0\0\0\0\0\0\0\0\0',
            0x100000200, 64, 0x200, 2, 0, 0, 0x80000400, 0, 0, 0
        )
        # LC_CODE_SIGNATURE (16 bytes)
        sig_off = 0x1000
        sig_size = 0x200
        struct.pack_into('<IIII', data, 32 + 152,
            LC_CODE_SIGNATURE, 16, sig_off, sig_size
        )

        # Place PAC instructions at offset 0x200:
        struct.pack_into('<I', data, 0x200, 0xd65f0bff)  # retab
        struct.pack_into('<I', data, 0x204, 0xd65f0820)  # retab x1
        struct.pack_into('<I', data, 0x208, 0xf8bfc020)  # ldapr x0, [x1]
        struct.pack_into('<I', data, 0x20c, 0x19000000)  # stlur
        struct.pack_into('<I', data, 0x210, 0xd4388e20)  # brk #0xc471
        struct.pack_into('<I', data, 0x214, 0xd2800020)  # mov x0, #1 (standard)

        # Construct CS_SuperBlob + CS_CodeDirectory
        code_limit = 4096
        page_size_log2 = 12
        page_size = 1 << page_size_log2
        n_slots = (code_limit + page_size - 1) // page_size
        hash_size = 32
        hash_type = CS_HASHTYPE_SHA256

        ident = b'synthetic_test\x00'
        ident_off = 64
        raw_hash_off = ident_off + len(ident)
        hash_off = (raw_hash_off + 3) & ~3
        cd_len = hash_off + n_slots * hash_size
        superblob_len = 12 + 8 + cd_len

        # SuperBlob Header
        struct.pack_into('>3I', data, sig_off, CSMAGIC_EMBEDDED_SIGNATURE, superblob_len, 1)
        struct.pack_into('>2I', data, sig_off + 12, CSSLOT_CODEDIRECTORY, 20)

        # CodeDirectory Header
        cd_start = sig_off + 20
        struct.pack_into('>9I4B4IQ', data, cd_start,
            CSMAGIC_CODEDIRECTORY, cd_len, 0x20400, 2, hash_off, ident_off, 0, n_slots, code_limit,
            hash_size, hash_type, 0, page_size_log2,
            0, 0, 0, 0, code_limit
        )
        data[cd_start + ident_off : cd_start + ident_off + len(ident)] = ident

        # Calculate initial page hash slot
        for s in range(n_slots):
            p_start = s * page_size
            p_end = min(p_start + page_size, code_limit)
            h = hashlib.sha256(data[p_start:p_end]).digest()
            slot_pos = cd_start + hash_off + s * hash_size
            data[slot_pos : slot_pos + hash_size] = h

        return data, cd_start + hash_off

    def test_synthetic_thin_macho_patching_and_cd_update(self):
        data, slot_pos = self._create_synthetic_macho_with_cs()

        # Before patch: verify slot 0 matches the unpatched data
        old_hash = hashlib.sha256(data[0:4096]).digest()
        self.assertEqual(bytes(data[slot_pos:slot_pos+32]), old_hash)

        # Patch the slice
        total_patched, slots_updated, stats = patch_macho_slice(data, 0, len(data), dry_run=False, verbose=False)

        # Verify instruction count and stats
        self.assertEqual(total_patched, 5)
        self.assertEqual(stats.get('ret'), 1)
        self.assertEqual(stats.get('ret_x1'), 1)
        self.assertEqual(stats.get('ldapr'), 1)
        self.assertEqual(stats.get('stlur'), 1)
        self.assertEqual(stats.get('brk_pac_fail'), 1)

        # Verify patched instructions
        insn0, = struct.unpack_from('<I', data, 0x200)
        insn1, = struct.unpack_from('<I', data, 0x204)
        insn2, = struct.unpack_from('<I', data, 0x208)
        insn3, = struct.unpack_from('<I', data, 0x20c)
        insn4, = struct.unpack_from('<I', data, 0x210)
        insn5, = struct.unpack_from('<I', data, 0x214)

        self.assertEqual(insn0, A64_RET)        # retab -> ret
        self.assertEqual(insn1, 0xd65f0020)     # retab x1 -> ret x1
        self.assertEqual(insn2, 0xc8dffc20)     # ldapr -> ldar
        self.assertEqual(insn3, 0x38000000)     # stlur -> stur
        self.assertEqual(insn4, A64_NOP)        # brk #0xc471 -> nop
        self.assertEqual(insn5, 0xd2800020)     # normal mov untouched

        # Verify CodeDirectory SHA-256 slot update
        self.assertEqual(slots_updated, 1)
        new_hash = hashlib.sha256(data[0:4096]).digest()
        self.assertNotEqual(new_hash, old_hash)
        self.assertEqual(bytes(data[slot_pos:slot_pos+32]), new_hash)

    def test_synthetic_fat_binary_patching(self):
        macho_slice, _ = self._create_synthetic_macho_with_cs()

        # Build Universal FAT binary with 2 slices: x86_64 at 0x1000, arm64e at 0x4000
        x86_off = 0x1000
        x86_size = 0x1000
        arm_off = 0x4000
        arm_size = len(macho_slice)

        fat_data = bytearray(arm_off + arm_size)
        struct.pack_into('>2I', fat_data, 0, FAT_MAGIC, 2)
        # Slice 0: x86_64 (cputype 0x01000007)
        struct.pack_into('>5I', fat_data, 8, 0x01000007, 3, x86_off, x86_size, 12)
        # Slice 1: arm64e (cputype 0x0100000c)
        struct.pack_into('>5I', fat_data, 28, CPU_TYPE_ARM64, 2, arm_off, arm_size, 12)

        # Place dummy marker in x86 slice
        fat_data[x86_off : x86_off + 4] = b'X86!'
        # Place arm64e Mach-O in arm slice
        fat_data[arm_off : arm_off + arm_size] = macho_slice

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(fat_data)
            tmp_path = tmp.name

        try:
            success, total_patched, slots_updated = patch_file(tmp_path, dry_run=False, verbose=False)
            self.assertTrue(success)
            self.assertEqual(total_patched, 5)
            self.assertEqual(slots_updated, 1)

            with open(tmp_path, 'rb') as f:
                res_data = f.read()

            # x86 slice must be untouched
            self.assertEqual(res_data[x86_off : x86_off + 4], b'X86!')

            # arm64e slice must be patched
            arm_insn0, = struct.unpack_from('<I', res_data, arm_off + 0x200)
            self.assertEqual(arm_insn0, A64_RET)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_dry_run_flag(self):
        macho_slice, _ = self._create_synthetic_macho_with_cs()
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(macho_slice)
            tmp_path = tmp.name

        try:
            success, total_patched, slots_updated = patch_file(tmp_path, dry_run=True, verbose=False)
            self.assertTrue(success)
            self.assertEqual(total_patched, 5)
            self.assertEqual(slots_updated, 1)

            # In dry-run mode, file content on disk must remain unpatched
            with open(tmp_path, 'rb') as f:
                disk_data = f.read()
            self.assertEqual(disk_data, macho_slice)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_recursive_directory_scan(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Subdirectory tree
            bin_dir = os.path.join(tmp_dir, "usr", "bin")
            lib_dir = os.path.join(tmp_dir, "usr", "lib")
            res_dir = os.path.join(tmp_dir, "Library", "Resources")
            os.makedirs(bin_dir)
            os.makedirs(lib_dir)
            os.makedirs(res_dir)

            macho_slice, _ = self._create_synthetic_macho_with_cs()

            # Mach-O executable
            exec_path = os.path.join(bin_dir, "my_tool")
            with open(exec_path, 'wb') as f:
                f.write(macho_slice)

            # Mach-O dylib (change filetype to MH_DYLIB)
            dylib_slice = bytearray(macho_slice)
            struct.pack_into('<I', dylib_slice, 12, MH_DYLIB)
            dylib_path = os.path.join(lib_dir, "libtest.dylib")
            with open(dylib_path, 'wb') as f:
                f.write(dylib_slice)

            # Non-Mach-O file (Info.plist text)
            txt_path = os.path.join(res_dir, "Info.plist")
            with open(txt_path, 'wb') as f:
                f.write(b"<?xml version='1.0'?><dict></dict>")

            binaries_patched, total_insn, total_slots = scan_and_patch_directory(
                tmp_dir, dry_run=False, verbose=False
            )

            self.assertEqual(binaries_patched, 2)
            self.assertEqual(total_insn, 10) # 5 in each
            self.assertEqual(total_slots, 2)  # 1 in each

            # Verify text file untouched
            with open(txt_path, 'rb') as f:
                self.assertEqual(f.read(), b"<?xml version='1.0'?><dict></dict>")

    def test_multi_page_selective_code_directory_update(self):
        """
        Tests that when a Mach-O binary spans multiple 4KB pages, only the pages
        with patched PAC instructions have their CodeDirectory slots updated,
        leaving clean pages untouched.
        """
        # 3 pages: 12288 bytes + signature
        total_code_size = 12288
        sig_off = 12288
        data = bytearray(sig_off + 2048)

        struct.pack_into('<8I', data, 0,
            MH_MAGIC_64, CPU_TYPE_ARM64, 0x80000002, MH_EXECUTE, 2, 152 + 16, 0, 0
        )
        struct.pack_into('<II16s4Q4I', data, 32,
            LC_SEGMENT_64, 152, b'__TEXT\0\0\0\0\0\0\0\0\0\0',
            0x100000000, total_code_size, 0, total_code_size, 7, 5, 1, 0
        )
        struct.pack_into('<16s16s2Q8I', data, 32 + 72,
            b'__text\0\0\0\0\0\0\0\0\0\0', b'__TEXT\0\0\0\0\0\0\0\0\0\0',
            0x100000200, total_code_size - 0x200, 0x200, 2, 0, 0, 0x80000400, 0, 0, 0
        )
        struct.pack_into('<IIII', data, 32 + 152,
            LC_CODE_SIGNATURE, 16, sig_off, 1024
        )

        # Page 0 (offset 0x200): PAC instruction
        struct.pack_into('<I', data, 0x200, 0xd65f0bff) # retab
        # Page 1 (offset 0x1200): Only ARMv8.0 standard instructions
        struct.pack_into('<I', data, 0x1200, 0xd503201f) # nop
        struct.pack_into('<I', data, 0x1204, 0xd65f03c0) # ret
        # Page 2 (offset 0x2200): PAC trap
        struct.pack_into('<I', data, 0x2200, 0xd4388e20) # brk #0xc471

        # Build CodeDirectory for 3 pages
        n_slots = 3
        hash_size = 32
        hash_type = CS_HASHTYPE_SHA256
        page_size_log2 = 12
        page_size = 4096
        ident = b'multipage_test\0'
        ident_off = 64
        hash_off = (ident_off + len(ident) + 3) & ~3
        cd_len = hash_off + n_slots * hash_size
        superblob_len = 12 + 8 + cd_len

        struct.pack_into('>3I', data, sig_off, CSMAGIC_EMBEDDED_SIGNATURE, superblob_len, 1)
        struct.pack_into('>2I', data, sig_off + 12, CSSLOT_CODEDIRECTORY, 20)

        cd_start = sig_off + 20
        struct.pack_into('>9I4B4IQ', data, cd_start,
            CSMAGIC_CODEDIRECTORY, cd_len, 0x20400, 2, hash_off, ident_off, 0, n_slots, total_code_size,
            hash_size, hash_type, 0, page_size_log2,
            0, 0, 0, 0, total_code_size
        )
        data[cd_start + ident_off : cd_start + ident_off + len(ident)] = ident

        # Calculate initial hashes for all 3 pages
        initial_hashes = []
        for s in range(n_slots):
            p_bytes = data[s * page_size : (s + 1) * page_size]
            h = hashlib.sha256(p_bytes).digest()
            initial_hashes.append(h)
            pos = cd_start + hash_off + s * hash_size
            data[pos : pos + hash_size] = h

        # Patch slice
        total_patched, slots_updated, stats = patch_macho_slice(data, 0, len(data), dry_run=False)
        self.assertEqual(total_patched, 2) # retab in page 0, brk in page 2
        self.assertEqual(slots_updated, 2) # slot 0 and slot 2 updated, slot 1 unchanged

        # Verify slot hashes
        slot0_hash = bytes(data[cd_start + hash_off : cd_start + hash_off + 32])
        slot1_hash = bytes(data[cd_start + hash_off + 32 : cd_start + hash_off + 64])
        slot2_hash = bytes(data[cd_start + hash_off + 64 : cd_start + hash_off + 96])

        # Page 0 hash changed and matches new page 0
        self.assertNotEqual(slot0_hash, initial_hashes[0])
        self.assertEqual(slot0_hash, hashlib.sha256(data[0:4096]).digest())

        # Page 1 hash is UNCHANGED
        self.assertEqual(slot1_hash, initial_hashes[1])

        # Page 2 hash changed and matches new page 2
        self.assertNotEqual(slot2_hash, initial_hashes[2])
        self.assertEqual(slot2_hash, hashlib.sha256(data[8192:12288]).digest())

    def test_dual_codedirectory_sha1_and_sha256(self):
        """
        Tests binary with multiple CodeDirectories (e.g. primary SHA-1 and alternate SHA-256).
        """
        data = bytearray(8192)
        sig_off = 4096
        struct.pack_into('<8I', data, 0,
            MH_MAGIC_64, CPU_TYPE_ARM64, 0x80000002, MH_EXECUTE, 2, 152 + 16, 0, 0
        )
        struct.pack_into('<II16s4Q4I', data, 32,
            LC_SEGMENT_64, 152, b'__TEXT\0\0\0\0\0\0\0\0\0\0',
            0x100000000, 4096, 0, 4096, 7, 5, 1, 0
        )
        struct.pack_into('<16s16s2Q8I', data, 32 + 72,
            b'__text\0\0\0\0\0\0\0\0\0\0', b'__TEXT\0\0\0\0\0\0\0\0\0\0',
            0x100000200, 64, 0x200, 2, 0, 0, 0x80000400, 0, 0, 0
        )
        struct.pack_into('<IIII', data, 32 + 152,
            LC_CODE_SIGNATURE, 16, sig_off, 2048
        )
        struct.pack_into('<I', data, 0x200, 0xd65f0bff) # retab

        # Build SuperBlob with 2 Blobs:
        # Blob 0: SHA-1 (hashType=1, hashSize=20)
        # Blob 1: SHA-256 (hashType=2, hashSize=32)
        ident = b'dual_cd\0'
        ident_off = 64
        hash_off = (ident_off + len(ident) + 3) & ~3

        cd1_len = hash_off + 20
        cd2_len = hash_off + 32

        blob0_suboff = 12 + 2 * 8
        blob1_suboff = blob0_suboff + cd1_len
        superblob_len = blob1_suboff + cd2_len

        struct.pack_into('>3I', data, sig_off, CSMAGIC_EMBEDDED_SIGNATURE, superblob_len, 2)
        struct.pack_into('>2I', data, sig_off + 12, CSSLOT_CODEDIRECTORY, blob0_suboff)
        struct.pack_into('>2I', data, sig_off + 20, CSSLOT_ALTERNATE_CODEDIRS, blob1_suboff)

        # Blob 0: SHA-1
        cd0_pos = sig_off + blob0_suboff
        struct.pack_into('>9I4B4IQ', data, cd0_pos,
            CSMAGIC_CODEDIRECTORY, cd1_len, 0x20400, 2, hash_off, ident_off, 0, 1, 4096,
            20, CS_HASHTYPE_SHA1, 0, 12, 0, 0, 0, 0, 4096
        )
        data[cd0_pos + hash_off : cd0_pos + hash_off + 20] = hashlib.sha1(data[0:4096]).digest()

        # Blob 1: SHA-256
        cd1_pos = sig_off + blob1_suboff
        struct.pack_into('>9I4B4IQ', data, cd1_pos,
            CSMAGIC_CODEDIRECTORY, cd2_len, 0x20400, 2, hash_off, ident_off, 0, 1, 4096,
            32, CS_HASHTYPE_SHA256, 0, 12, 0, 0, 0, 0, 4096
        )
        data[cd1_pos + hash_off : cd1_pos + hash_off + 32] = hashlib.sha256(data[0:4096]).digest()

        total_patched, slots_updated, stats = patch_macho_slice(data, 0, len(data))
        self.assertEqual(total_patched, 1)
        self.assertEqual(slots_updated, 2) # Both SHA-1 and SHA-256 updated!

        # Verify new hashes
        new_sha1 = hashlib.sha1(data[0:4096]).digest()
        new_sha256 = hashlib.sha256(data[0:4096]).digest()
        self.assertEqual(bytes(data[cd0_pos + hash_off : cd0_pos + hash_off + 20]), new_sha1)
        self.assertEqual(bytes(data[cd1_pos + hash_off : cd1_pos + hash_off + 32]), new_sha256)

    def test_fat_magic_64(self):
        """
        Tests parsing and patching FAT_MAGIC_64 (64-bit universal binary header).
        """
        macho_slice, _ = self._create_synthetic_macho_with_cs()
        arm_off = 0x2000
        arm_size = len(macho_slice)

        fat_data = bytearray(arm_off + arm_size)
        struct.pack_into('>2I', fat_data, 0, FAT_MAGIC_64, 1)
        # 64-bit FAT arch: >IIQQII (32 bytes)
        struct.pack_into('>IIQQII', fat_data, 8, CPU_TYPE_ARM64, 2, arm_off, arm_size, 12, 0)
        fat_data[arm_off : arm_off + arm_size] = macho_slice

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(fat_data)
            tmp_path = tmp.name

        try:
            success, total_patched, slots_updated = patch_file(tmp_path)
            self.assertTrue(success)
            self.assertEqual(total_patched, 5)
            self.assertEqual(slots_updated, 1)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_corrupt_and_empty_files(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # 0 bytes
            success, p, s = patch_file(tmp_path)
            self.assertFalse(success)

            # Random 16 bytes
            with open(tmp_path, 'wb') as f:
                f.write(b'1234567890123456')
            success, p, s = patch_file(tmp_path)
            self.assertFalse(success)

            # Non-existent file
            success, p, s = patch_file("C:\\non_existent_file_path_12345")
            self.assertFalse(success)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


def run_tests():
    """
    Executes the unit test suite and returns True if all tests pass.
    """
    print("=" * 60)
    print("Running PAC Sanitizer Self-Contained Unit Test Suite...")
    print("=" * 60)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestPacSanitizer)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


# ==============================================================================
# CLI Entrypoint
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Industrial-Grade Offline Static PAC Sanitizer for iOS Userland Mach-O Binaries (Cortex-A73)."
    )
    parser.add_argument(
        "paths", nargs="*", default=[],
        help="Target Mach-O binary file(s) or directory to sanitize"
    )
    parser.add_argument(
        "--scan-dir", dest="scan_dir", default=None,
        help="Recursively scan directory and sanitize all 64-bit Mach-O binaries"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulate patching without modifying files on disk"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose output showing patch details and CodeDirectory slots"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Run built-in self-contained unit test suite"
    )

    args = parser.parse_args()

    if args.test:
        success = run_tests()
        sys.exit(0 if success else 1)

    targets = list(args.paths)
    if args.scan_dir:
        targets.append(args.scan_dir)

    if not targets:
        parser.print_help()
        sys.exit(1)

    for target in targets:
        if os.path.isfile(target):
            patch_file(target, dry_run=args.dry_run, verbose=args.verbose)
        elif os.path.isdir(target):
            scan_and_patch_directory(target, dry_run=args.dry_run, verbose=args.verbose)
        else:
            print(f"[-] Target does not exist: {target}")

    print("Userland PAC offline static sanitization complete.")


if __name__ == '__main__':
    main()
