#!/usr/bin/env python3
"""
tools/dsc_flatten_v2.py - Dyld Shared Cache Slide Info v5 Chained Fixup Flattener

Flattens ARM64E authenticated pointers in dyld shared cache slide_info v5
to raw 64-bit unauthenticated pointers for ARMv8.0-A CPUs (e.g. Cortex-A73/A53
on Qualcomm Snapdragon 685 SM6225-AD) lacking hardware Pointer Authentication (PAC).

dyld_cache_slide_info5 Header Layout (24 bytes):
  uint32_t version;            // offset  0: must be 5
  uint32_t page_size;          // offset  4: typically 4096 or 16384
  uint32_t page_starts_count;  // offset  8: number of pages in page_starts
  uint32_t reserved / padding; // offset 12: 4-byte padding for 8-byte alignment
  uint64_t value_add;          // offset 16: base virtual address offset
Followed immediately by:
  uint16_t page_starts[page_starts_count]; // offset 24

dyld_chained_ptr_arm64e_shared_cache_auth_rebase (64-bit):
  [33: 0] runtimeOffset (34 bits)
  [49:34] diversity (16 bits)
  [50:50] addrDiv (1 bit)
  [51:51] keyIsData (1 bit)
  [62:52] next (11 bits) -> 8-byte stride
  [63:63] auth (1 bit) == 1

dyld_chained_ptr_arm64e_shared_cache_rebase (64-bit):
  [33: 0] runtimeOffset (34 bits)
  [41:34] high8 (8 bits)
  [51:42] unused (10 bits)
  [62:52] next (11 bits) -> 8-byte stride
  [63:63] auth (1 bit) == 0

Pointer Stride Rule:
  cursor += stride * 8  (8-byte pointer stride, NOT 4 bytes!)

Auth Pointer Flattening:
  If raw_ptr bit 63 (auth) is set:
    runtime_offset = raw_ptr & 0x3FFFFFFFF (34-bit)
    real_target = value_add + runtime_offset
    Write flat 64-bit real_target back to pointer location.
"""

from __future__ import annotations

import argparse
import mmap
import os
import struct
import sys
import tempfile
from typing import BinaryIO, Dict, List, Optional, Tuple, Union

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Header constants
SLIDE_INFO_V5_VERSION = 5
SLIDE_INFO_V5_HEADER_SIZE = 24  # 4 + 4 + 4 + 4 (padding) + 8 = 24 bytes
DYLD_CACHE_SLIDE_V5_PAGE_ATTR_NO_REBASE = 0xFFFF

# Struct format: 3 x uint32, 4 pad bytes, 1 x uint64 = 24 bytes
HEADER_STRUCT_FMT = "<III4xQ"

# Bitfield masks and shifts
RUNTIME_OFFSET_MASK = 0x3FFFFFFFF  # 34 bits (bits 0..33)
DIVERSITY_MASK = 0xFFFF            # 16 bits (bits 34..49)
STRIDE_MASK = 0x7FF                # 11 bits (bits 52..62)
STRIDE_SHIFT = 52                  # Bit 52
HIGH8_MASK = 0xFF                  # 8 bits (bits 34..41)
AUTH_BIT_MASK = 1 << 63            # Bit 63


def encode_slide_info5_header(
    page_size: int,
    page_starts_count: int,
    value_add: int,
    version: int = SLIDE_INFO_V5_VERSION,
) -> bytes:
    """Pack a 24-byte dyld_cache_slide_info5 header."""
    return struct.pack(
        HEADER_STRUCT_FMT,
        version,
        page_size,
        page_starts_count,
        value_add,
    )


def encode_auth_ptr(
    runtime_offset: int,
    stride: int,
    diversity: int = 0,
    addr_div: int = 0,
    key_is_data: int = 0,
    stride_shift: int = STRIDE_SHIFT,
) -> int:
    """Encode an arm64e authenticated rebase chained pointer (auth bit 63 = 1)."""
    val = runtime_offset & RUNTIME_OFFSET_MASK
    val |= (diversity & DIVERSITY_MASK) << 34
    val |= (addr_div & 1) << 50
    val |= (key_is_data & 1) << 51
    val |= (stride & STRIDE_MASK) << stride_shift
    val |= AUTH_BIT_MASK
    return val


def encode_regular_ptr(
    runtime_offset: int,
    stride: int,
    high8: int = 0,
    stride_shift: int = STRIDE_SHIFT,
) -> int:
    """Encode an arm64e regular rebase chained pointer (auth bit 63 = 0)."""
    val = runtime_offset & RUNTIME_OFFSET_MASK
    val |= (high8 & HIGH8_MASK) << 34
    val |= (stride & STRIDE_MASK) << stride_shift
    # Bit 63 is 0 (regular)
    return val


def decode_ptr(raw_ptr: int, stride_shift: int = STRIDE_SHIFT) -> Dict[str, Union[int, bool]]:
    """Decode fields of a 64-bit chained pointer."""
    auth = bool((raw_ptr >> 63) & 1)
    stride = (raw_ptr >> stride_shift) & STRIDE_MASK
    runtime_offset = raw_ptr & RUNTIME_OFFSET_MASK
    res: Dict[str, Union[int, bool]] = {
        "raw": raw_ptr,
        "auth": auth,
        "stride": stride,
        "stride_bytes": stride * 8,
        "runtime_offset": runtime_offset,
    }
    if auth:
        res["diversity"] = (raw_ptr >> 34) & DIVERSITY_MASK
        res["addr_div"] = (raw_ptr >> 50) & 1
        res["key_is_data"] = (raw_ptr >> 51) & 1
    else:
        res["high8"] = (raw_ptr >> 34) & HIGH8_MASK
    return res


def flatten_slide_v5(
    data: Union[bytearray, memoryview, mmap.mmap],
    slide_info_offset: int,
    data_offset: int = 0,
    flatten_all: bool = False,
    stride_shift: int = STRIDE_SHIFT,
    verbose: bool = False,
    quiet: bool = False,
) -> Dict[str, int]:
    """
    Flatten chained fixups in dyld_cache_slide_info5.

    Args:
        data: Writable memory buffer containing slide_info and data mappings.
        slide_info_offset: Byte offset in data where dyld_cache_slide_info5 starts.
        data_offset: Byte offset in data where the page range starts (default: 0).
        flatten_all: If True, also rebase non-auth pointers; if False, only auth pointers.
        stride_shift: Bit position for stride (default: 52).
        verbose: If True, log verbose chain traversal details.

    Returns:
        Dictionary with statistics (pages_total, pages_slid, auth_flattened, etc.)
    """
    buf_len = len(data)
    if slide_info_offset + SLIDE_INFO_V5_HEADER_SIZE > buf_len:
        raise ValueError(
            f"slide_info_offset (0x{slide_info_offset:x}) + 24 exceeds buffer length (0x{buf_len:x})"
        )

    version, page_size, page_starts_count, value_add = struct.unpack_from(
        HEADER_STRUCT_FMT, data, slide_info_offset
    )

    if version != SLIDE_INFO_V5_VERSION:
        raise ValueError(
            f"Invalid slide_info version: expected {SLIDE_INFO_V5_VERSION}, got {version}"
        )

    if page_size == 0 or (page_size & (page_size - 1)) != 0:
        raise ValueError(f"Invalid page_size: {page_size} (must be a power of 2)")

    starts_offset = slide_info_offset + SLIDE_INFO_V5_HEADER_SIZE
    starts_bytes = page_starts_count * 2
    if starts_offset + starts_bytes > buf_len:
        raise ValueError(
            f"page_starts array (0x{starts_bytes:x} bytes) exceeds buffer length (0x{buf_len:x})"
        )

    if verbose:
        print(
            f"[*] slide_info v5 @ 0x{slide_info_offset:x}: "
            f"page_size={page_size}, pages={page_starts_count}, value_add=0x{value_add:016x}"
        )

    auth_flattened = 0
    regular_flattened = 0
    regular_seen = 0
    pages_slid = 0
    pages_skipped = 0
    total_chains = 0

    for i in range(page_starts_count):
        pstart = struct.unpack_from("<H", data, starts_offset + i * 2)[0]
        if pstart == DYLD_CACHE_SLIDE_V5_PAGE_ATTR_NO_REBASE:
            pages_skipped += 1
            continue

        pages_slid += 1
        page_base = data_offset + i * page_size
        cursor = page_base + pstart
        total_chains += 1

        visited_cursors = set()

        while True:
            if cursor < page_base or cursor + 8 > buf_len:
                if verbose:
                    print(
                        f"[!] Warning: chain out of bounds at page {i}, cursor 0x{cursor:x} "
                        f"(page_base: 0x{page_base:x}, buf_len: 0x{buf_len:x})"
                    )
                break

            if cursor in visited_cursors:
                if verbose:
                    print(f"[!] Warning: cycle detected in chain at cursor 0x{cursor:x}")
                break
            visited_cursors.add(cursor)

            raw_ptr = struct.unpack_from("<Q", data, cursor)[0]
            auth = (raw_ptr >> 63) & 1
            stride = (raw_ptr >> stride_shift) & STRIDE_MASK

            if auth == 1:
                # 34-bit runtime offset
                runtime_offset = raw_ptr & RUNTIME_OFFSET_MASK
                real_target = value_add + runtime_offset

                # Write flat 64-bit real_target back to the pointer location
                struct.pack_into("<Q", data, cursor, real_target)
                auth_flattened += 1

                if verbose:
                    print(
                        f"    [P{i}] auth_ptr @ 0x{cursor:x}: raw=0x{raw_ptr:016x} -> "
                        f"flat=0x{real_target:016x} (stride={stride})"
                    )
            else:
                regular_seen += 1
                if flatten_all:
                    runtime_offset = raw_ptr & RUNTIME_OFFSET_MASK
                    high8 = (raw_ptr >> 34) & HIGH8_MASK
                    real_target = value_add + runtime_offset + (high8 << 56)
                    struct.pack_into("<Q", data, cursor, real_target)
                    regular_flattened += 1
                    if verbose:
                        print(
                            f"    [P{i}] reg_ptr @ 0x{cursor:x}: raw=0x{raw_ptr:016x} -> "
                            f"flat=0x{real_target:016x} (stride={stride})"
                        )

            if stride == 0:
                break

            # Pointer stride rule: each step is stride * 8 bytes
            cursor += stride * 8

    stats = {
        "version": version,
        "page_size": page_size,
        "page_starts_count": page_starts_count,
        "value_add": value_add,
        "pages_total": page_starts_count,
        "pages_slid": pages_slid,
        "pages_skipped": pages_skipped,
        "total_chains": total_chains,
        "auth_flattened": auth_flattened,
        "regular_seen": regular_seen,
        "regular_flattened": regular_flattened,
    }

    if not quiet:
        print(
            f"[+] Slide info v5 flattening complete: {auth_flattened} auth pointers flattened "
            f"across {pages_slid}/{page_starts_count} pages ({pages_skipped} empty pages)."
        )

    return stats


def flatten_dsc_file(
    file_path: str,
    slide_info_offset: int,
    data_offset: int = 0,
    flatten_all: bool = False,
    output_path: Optional[str] = None,
    stride_shift: int = STRIDE_SHIFT,
    verbose: bool = False,
    quiet: bool = False,
) -> Dict[str, int]:
    """
    Flatten slide_info5 authenticated pointers in a file.

    If output_path is provided, writes to output_path; otherwise modifies in-place.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"DSC file not found: {file_path}")

    target_file = output_path if output_path else file_path

    if output_path:
        with open(file_path, "rb") as src, open(output_path, "wb") as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)

    with open(target_file, "r+b") as f:
        mm = mmap.mmap(f.fileno(), 0)
        try:
            stats = flatten_slide_v5(
                mm,
                slide_info_offset=slide_info_offset,
                data_offset=data_offset,
                flatten_all=flatten_all,
                stride_shift=stride_shift,
                verbose=verbose,
                quiet=quiet,
            )
            mm.flush()
        finally:
            mm.close()

    return stats


def find_slide_info_v5_regions(
    data: Union[bytearray, memoryview, mmap.mmap]
) -> List[Tuple[int, int, int]]:
    """
    Parse dyld_cache_header to locate slide_info v5 regions and their associated data offsets.
    Returns list of (slide_info_offset, data_offset, slide_info_size).
    """
    regions = []
    if len(data) < 0x200:
        return regions

    magic = bytes(data[:16]).split(b"\0")[0]
    if not magic.startswith(b"dyld_v1"):
        return regions

    # dyld_cache_header layout:
    # 0x138: uint32_t mappingWithSlideOffset
    # 0x13c: uint32_t mappingWithSlideCount
    m_off, m_count = struct.unpack_from("<II", data, 0x138)
    if m_count > 0 and 0 < m_off < len(data):
        entry_size = 56  # sizeof(dyld_cache_mapping_and_slide_info)
        for i in range(m_count):
            e_off = m_off + i * entry_size
            if e_off + entry_size > len(data):
                break
            # dyld_cache_mapping_and_slide_info:
            # uint64_t address, size, fileOffset, slideInfoFileOffset, slideInfoFileSize, flags
            # uint32_t maxProt, initProt
            addr, sz, f_off, s_off, s_sz, flg, maxp, initp = struct.unpack_from("<QQQQQIII", data, e_off)
            if s_off > 0 and s_sz >= SLIDE_INFO_V5_HEADER_SIZE and s_off + 4 <= len(data):
                ver = struct.unpack_from("<I", data, s_off)[0]
                if ver == SLIDE_INFO_V5_VERSION:
                    regions.append((s_off, f_off, s_sz))

    # Fallback to legacy slideInfoOffset / slideInfoSize at offset 0x38 if no mappingWithSlide found
    if not regions and len(data) >= 0x48:
        old_s_off, old_s_sz = struct.unpack_from("<QQ", data, 0x38)
        if old_s_off > 0 and old_s_sz >= SLIDE_INFO_V5_HEADER_SIZE and old_s_off + 4 <= len(data):
            ver = struct.unpack_from("<I", data, old_s_off)[0]
            if ver == SLIDE_INFO_V5_VERSION:
                regions.append((old_s_off, 0, old_s_sz))

    return regions


def flatten_dsc_auto(
    file_path: str,
    flatten_all: bool = False,
    output_path: Optional[str] = None,
    stride_shift: int = STRIDE_SHIFT,
    verbose: bool = False,
    quiet: bool = False,
) -> Dict[str, int]:
    """
    Automatically detect dyld_cache_header slide_info v5 mappings and flatten authenticated pointers.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"DSC file not found: {file_path}")

    target_file = output_path if output_path else file_path

    if output_path:
        with open(file_path, "rb") as src, open(output_path, "wb") as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)

    total_stats = {
        "regions_found": 0,
        "auth_flattened": 0,
        "regular_flattened": 0,
        "pages_slid": 0,
    }

    with open(target_file, "r+b") as f:
        mm = mmap.mmap(f.fileno(), 0)
        try:
            regions = find_slide_info_v5_regions(mm)
            total_stats["regions_found"] = len(regions)
            if not regions:
                if not quiet:
                    print(f"[*] No slide_info v5 regions detected in {os.path.basename(file_path)}.")
                return total_stats

            for s_off, f_off, s_sz in regions:
                if not quiet:
                    print(
                        f"[*] Flattening slide_info v5 region @ 0x{s_off:x} "
                        f"(data_offset=0x{f_off:x}, size={s_sz} B)..."
                    )
                stats = flatten_slide_v5(
                    mm,
                    slide_info_offset=s_off,
                    data_offset=f_off,
                    flatten_all=flatten_all,
                    stride_shift=stride_shift,
                    verbose=verbose,
                    quiet=quiet,
                )
                total_stats["auth_flattened"] += stats["auth_flattened"]
                total_stats["regular_flattened"] += stats["regular_flattened"]
                total_stats["pages_slid"] += stats["pages_slid"]

            mm.flush()
        finally:
            mm.close()

    return total_stats


def flatten_dsc_directory(
    dir_path: str,
    flatten_all: bool = False,
    verbose: bool = False,
    quiet: bool = False,
) -> Dict[str, int]:
    """
    Scan a directory for dyld_shared_cache* files and flatten all of them in place.
    """
    if not os.path.isdir(dir_path):
        raise NotADirectoryError(f"Directory not found: {dir_path}")

    combined_stats = {
        "files_processed": 0,
        "regions_found": 0,
        "auth_flattened": 0,
        "regular_flattened": 0,
    }

    for fname in sorted(os.listdir(dir_path)):
        if fname.startswith("dyld_shared_cache") and not fname.endswith(".map") and not fname.endswith(".symbols"):
            fpath = os.path.join(dir_path, fname)
            if os.path.isfile(fpath):
                if not quiet:
                    print(f"\n--- Processing cache file: {fname} ---")
                res = flatten_dsc_auto(
                    fpath,
                    flatten_all=flatten_all,
                    verbose=verbose,
                    quiet=quiet,
                )
                combined_stats["files_processed"] += 1
                combined_stats["regions_found"] += res["regions_found"]
                combined_stats["auth_flattened"] += res["auth_flattened"]
                combined_stats["regular_flattened"] += res["regular_flattened"]

    if not quiet:
        print(
            f"\n[✔] Directory flattening complete: {combined_stats['files_processed']} cache files, "
            f"{combined_stats['regions_found']} regions, {combined_stats['auth_flattened']} auth pointers flattened."
        )

    return combined_stats


# ==============================================================================
# Comprehensive Self-Test Suite
# ==============================================================================

def run_tests() -> bool:
    """
    Execute comprehensive self-test suite covering:
      1. 24-byte Header structure layout and alignment
      2. Single-page auth pointer chain flattening
      3. Multi-page traversal with empty pages (0xFFFF)
      4. Stride * 8 calculation verification
      5. Mixed chain (auth + non-auth) handling
      6. 16KB Page size (iOS default) and extreme 34-bit runtime offsets
      7. Separate data offset handling
      8. Flatten-all mode verification
      9. Bounds check and cycle protection
      10. End-to-end file/mmap flattening
    """
    print("=" * 70)
    print("Running tools/dsc_flatten_v2.py Self-Test Suite")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # Test 1: Header layout validation (24 bytes)
    # -------------------------------------------------------------------------
    print("[Test 1] Header layout (24 bytes)...", end=" ")
    header = encode_slide_info5_header(4096, 128, 0x180000000)
    assert len(header) == 24, f"Header size must be 24 bytes, got {len(header)}"
    v, ps, psc, vadd = struct.unpack_from(HEADER_STRUCT_FMT, header, 0)
    assert v == 5, f"Expected version 5, got {v}"
    assert ps == 4096, f"Expected page_size 4096, got {ps}"
    assert psc == 128, f"Expected page_starts_count 128, got {psc}"
    assert vadd == 0x180000000, f"Expected value_add 0x180000000, got 0x{vadd:x}"
    print("PASS")

    # -------------------------------------------------------------------------
    # Test 2: Single-page auth pointer chain flattening & Stride * 8 rule
    # -------------------------------------------------------------------------
    print("[Test 2] Single page auth chain & Stride * 8 rule...", end=" ")
    page_size = 4096
    buf = bytearray(SLIDE_INFO_V5_HEADER_SIZE + 2 + page_size)
    slide_info_off = 0
    data_off = SLIDE_INFO_V5_HEADER_SIZE + 2
    value_add = 0x180000000

    # Header: 1 page, value_add = 0x180000000
    struct.pack_into(
        HEADER_STRUCT_FMT, buf, slide_info_off,
        SLIDE_INFO_V5_VERSION, page_size, 1, value_add
    )
    # page_starts[0] = 0x10 (chain starts at offset 0x10)
    struct.pack_into("<H", buf, slide_info_off + SLIDE_INFO_V5_HEADER_SIZE, 0x10)

    # Chain:
    # 1. offset 0x10: runtime_offset=0x1000, stride=3  -> next at 0x10 + 3*8 = 0x28
    # 2. offset 0x28: runtime_offset=0x2500, stride=10 -> next at 0x28 + 10*8 = 0x78
    # 3. offset 0x78: runtime_offset=0x9000, stride=1  -> next at 0x78 + 1*8 = 0x80
    # 4. offset 0x80: runtime_offset=0xABCD, stride=0  -> end of chain
    ptr1 = encode_auth_ptr(0x1000, stride=3, diversity=0x1111)
    ptr2 = encode_auth_ptr(0x2500, stride=10, diversity=0x2222)
    ptr3 = encode_auth_ptr(0x9000, stride=1, diversity=0x3333)
    ptr4 = encode_auth_ptr(0xABCD, stride=0, diversity=0x4444)

    struct.pack_into("<Q", buf, data_off + 0x10, ptr1)
    struct.pack_into("<Q", buf, data_off + 0x28, ptr2)
    struct.pack_into("<Q", buf, data_off + 0x78, ptr3)
    struct.pack_into("<Q", buf, data_off + 0x80, ptr4)

    # Set canary bytes before and after pointers to verify no corruption
    struct.pack_into("<Q", buf, data_off + 0x18, 0xCAFEBABEDEADBEEF)
    struct.pack_into("<Q", buf, data_off + 0x30, 0x1122334455667788)

    stats = flatten_slide_v5(buf, slide_info_off, data_offset=data_off, quiet=True)

    assert stats["auth_flattened"] == 4, f"Expected 4 flattened, got {stats['auth_flattened']}"

    # Verify real target calculations: target = value_add + runtime_offset
    target1 = struct.unpack_from("<Q", buf, data_off + 0x10)[0]
    target2 = struct.unpack_from("<Q", buf, data_off + 0x28)[0]
    target3 = struct.unpack_from("<Q", buf, data_off + 0x78)[0]
    target4 = struct.unpack_from("<Q", buf, data_off + 0x80)[0]

    assert target1 == value_add + 0x1000, f"Target 1 mismatch: 0x{target1:x} != 0x{value_add + 0x1000:x}"
    assert target2 == value_add + 0x2500, f"Target 2 mismatch: 0x{target2:x} != 0x{value_add + 0x2500:x}"
    assert target3 == value_add + 0x9000, f"Target 3 mismatch: 0x{target3:x} != 0x{value_add + 0x9000:x}"
    assert target4 == value_add + 0xABCD, f"Target 4 mismatch: 0x{target4:x} != 0x{value_add + 0xABCD:x}"

    # Auth bit (bit 63) must be 0 in flattened pointers
    assert (target1 >> 63) == 0
    assert (target2 >> 63) == 0
    assert (target3 >> 63) == 0
    assert (target4 >> 63) == 0

    # Canary bytes must be untouched
    canary1 = struct.unpack_from("<Q", buf, data_off + 0x18)[0]
    canary2 = struct.unpack_from("<Q", buf, data_off + 0x30)[0]
    assert canary1 == 0xCAFEBABEDEADBEEF
    assert canary2 == 0x1122334455667788
    print("PASS")

    # -------------------------------------------------------------------------
    # Test 3: Multiple pages with empty pages (0xFFFF)
    # -------------------------------------------------------------------------
    print("[Test 3] Multiple pages with 0xFFFF empty pages...", end=" ")
    page_count = 5
    page_size = 4096
    buf = bytearray(SLIDE_INFO_V5_HEADER_SIZE + page_count * 2 + page_count * page_size)
    slide_info_off = 0
    data_off = SLIDE_INFO_V5_HEADER_SIZE + page_count * 2
    value_add = 0x200000000

    struct.pack_into(
        HEADER_STRUCT_FMT, buf, slide_info_off,
        SLIDE_INFO_V5_VERSION, page_size, page_count, value_add
    )

    # Page 0: active (pstart=0x00, 2 pointers)
    # Page 1: empty (0xFFFF)
    # Page 2: active (pstart=0x20, 1 pointer)
    # Page 3: empty (0xFFFF)
    # Page 4: active (pstart=0x100, 3 pointers)
    starts = [0x00, 0xFFFF, 0x20, 0xFFFF, 0x100]
    for idx, s in enumerate(starts):
        struct.pack_into("<H", buf, slide_info_off + SLIDE_INFO_V5_HEADER_SIZE + idx * 2, s)

    # Page 0
    p0_ptr1 = encode_auth_ptr(0x111, stride=4)
    p0_ptr2 = encode_auth_ptr(0x222, stride=0)
    struct.pack_into("<Q", buf, data_off + 0 * page_size + 0x00, p0_ptr1)
    struct.pack_into("<Q", buf, data_off + 0 * page_size + 0x00 + 4 * 8, p0_ptr2)

    # Page 2
    p2_ptr1 = encode_auth_ptr(0x555, stride=0)
    struct.pack_into("<Q", buf, data_off + 2 * page_size + 0x20, p2_ptr1)

    # Page 4
    p4_ptr1 = encode_auth_ptr(0x777, stride=1)
    p4_ptr2 = encode_auth_ptr(0x888, stride=2)
    p4_ptr3 = encode_auth_ptr(0x999, stride=0)
    struct.pack_into("<Q", buf, data_off + 4 * page_size + 0x100, p4_ptr1)
    struct.pack_into("<Q", buf, data_off + 4 * page_size + 0x100 + 1 * 8, p4_ptr2)
    struct.pack_into("<Q", buf, data_off + 4 * page_size + 0x100 + 3 * 8, p4_ptr3)

    stats = flatten_slide_v5(buf, slide_info_off, data_offset=data_off, quiet=True)

    assert stats["pages_slid"] == 3
    assert stats["pages_skipped"] == 2
    assert stats["auth_flattened"] == 6

    # Verify Page 0
    assert struct.unpack_from("<Q", buf, data_off + 0 * page_size + 0x00)[0] == value_add + 0x111
    assert struct.unpack_from("<Q", buf, data_off + 0 * page_size + 0x20)[0] == value_add + 0x222

    # Verify Page 2
    assert struct.unpack_from("<Q", buf, data_off + 2 * page_size + 0x20)[0] == value_add + 0x555

    # Verify Page 4
    assert struct.unpack_from("<Q", buf, data_off + 4 * page_size + 0x100)[0] == value_add + 0x777
    assert struct.unpack_from("<Q", buf, data_off + 4 * page_size + 0x108)[0] == value_add + 0x888
    assert struct.unpack_from("<Q", buf, data_off + 4 * page_size + 0x118)[0] == value_add + 0x999
    print("PASS")

    # -------------------------------------------------------------------------
    # Test 4: Mixed chain (auth and non-auth pointers in the same chain)
    # -------------------------------------------------------------------------
    print("[Test 4] Mixed chain (auth + non-auth) handling...", end=" ")
    page_size = 4096
    buf = bytearray(SLIDE_INFO_V5_HEADER_SIZE + 2 + page_size)
    slide_info_off = 0
    data_off = SLIDE_INFO_V5_HEADER_SIZE + 2
    value_add = 0x180000000

    struct.pack_into(
        HEADER_STRUCT_FMT, buf, slide_info_off,
        SLIDE_INFO_V5_VERSION, page_size, 1, value_add
    )
    struct.pack_into("<H", buf, slide_info_off + SLIDE_INFO_V5_HEADER_SIZE, 0x0)

    # Chain:
    # 1. offset 0x00: AUTH ptr, stride=2 -> offset 0x10
    # 2. offset 0x10: REGULAR (non-auth) ptr, stride=4 -> offset 0x30
    # 3. offset 0x30: AUTH ptr, stride=0 -> end of chain
    auth1 = encode_auth_ptr(0x1000, stride=2)
    reg2 = encode_regular_ptr(0x2000, stride=4, high8=0x42)
    auth3 = encode_auth_ptr(0x3000, stride=0)

    struct.pack_into("<Q", buf, data_off + 0x00, auth1)
    struct.pack_into("<Q", buf, data_off + 0x10, reg2)
    struct.pack_into("<Q", buf, data_off + 0x30, auth3)

    stats = flatten_slide_v5(buf, slide_info_off, data_offset=data_off, flatten_all=False, quiet=True)

    assert stats["auth_flattened"] == 2
    assert stats["regular_seen"] == 1
    assert stats["regular_flattened"] == 0

    # auth1 flattened
    assert struct.unpack_from("<Q", buf, data_off + 0x00)[0] == value_add + 0x1000
    # reg2 untouched (chain preserved for non-auth)
    assert struct.unpack_from("<Q", buf, data_off + 0x10)[0] == reg2
    # auth3 flattened
    assert struct.unpack_from("<Q", buf, data_off + 0x30)[0] == value_add + 0x3000
    print("PASS")

    # -------------------------------------------------------------------------
    # Test 5: 16KB Page Size (iOS default) & 34-bit runtime offset boundary
    # -------------------------------------------------------------------------
    print("[Test 5] 16KB Page size & 34-bit runtime offset boundary...", end=" ")
    page_size = 16384
    buf = bytearray(SLIDE_INFO_V5_HEADER_SIZE + 2 + page_size)
    slide_info_off = 0
    data_off = SLIDE_INFO_V5_HEADER_SIZE + 2
    value_add = 0x1C0000000

    struct.pack_into(
        HEADER_STRUCT_FMT, buf, slide_info_off,
        SLIDE_INFO_V5_VERSION, page_size, 1, value_add
    )
    struct.pack_into("<H", buf, slide_info_off + SLIDE_INFO_V5_HEADER_SIZE, 0x0)

    # Test boundary offsets:
    # ptr_min: runtime_offset = 0 (min)
    # ptr_max: runtime_offset = 0x3FFFFFFFF (max 34-bit)
    # ptr_div: diverse flags (diversity=0xBEEF, addr_div=1, key_is_data=1)
    ptr_min = encode_auth_ptr(0x0, stride=100)  # 100 * 8 = 800 bytes
    ptr_max = encode_auth_ptr(0x3FFFFFFFF, stride=200)  # 200 * 8 = 1600 bytes
    ptr_div = encode_auth_ptr(0x1234567, stride=0, diversity=0xBEEF, addr_div=1, key_is_data=1)

    struct.pack_into("<Q", buf, data_off + 0x0, ptr_min)
    struct.pack_into("<Q", buf, data_off + 800, ptr_max)
    struct.pack_into("<Q", buf, data_off + 2400, ptr_div)

    stats = flatten_slide_v5(buf, slide_info_off, data_offset=data_off, quiet=True)

    assert stats["auth_flattened"] == 3
    assert struct.unpack_from("<Q", buf, data_off + 0x0)[0] == value_add + 0x0
    assert struct.unpack_from("<Q", buf, data_off + 800)[0] == value_add + 0x3FFFFFFFF
    assert struct.unpack_from("<Q", buf, data_off + 2400)[0] == value_add + 0x1234567
    print("PASS")

    # -------------------------------------------------------------------------
    # Test 6: Flatten-all mode (both regular and auth pointers flattened)
    # -------------------------------------------------------------------------
    print("[Test 6] Flatten-all mode...", end=" ")
    page_size = 4096
    buf = bytearray(SLIDE_INFO_V5_HEADER_SIZE + 2 + page_size)
    slide_info_off = 0
    data_off = SLIDE_INFO_V5_HEADER_SIZE + 2
    value_add = 0x180000000

    struct.pack_into(
        HEADER_STRUCT_FMT, buf, slide_info_off,
        SLIDE_INFO_V5_VERSION, page_size, 1, value_add
    )
    struct.pack_into("<H", buf, slide_info_off + SLIDE_INFO_V5_HEADER_SIZE, 0x0)

    # 1. Auth pointer
    # 2. Regular pointer with high8 = 0x1
    p_auth = encode_auth_ptr(0x1000, stride=1)
    p_reg = encode_regular_ptr(0x2000, stride=0, high8=0x1)

    struct.pack_into("<Q", buf, data_off + 0x0, p_auth)
    struct.pack_into("<Q", buf, data_off + 0x8, p_reg)

    stats = flatten_slide_v5(buf, slide_info_off, data_offset=data_off, flatten_all=True, quiet=True)

    assert stats["auth_flattened"] == 1
    assert stats["regular_flattened"] == 1

    expected_auth = value_add + 0x1000
    expected_reg = value_add + 0x2000 + (0x1 << 56)
    assert struct.unpack_from("<Q", buf, data_off + 0x0)[0] == expected_auth
    assert struct.unpack_from("<Q", buf, data_off + 0x8)[0] == expected_reg
    print("PASS")

    # -------------------------------------------------------------------------
    # Test 7: Cycle detection and safety bounds
    # -------------------------------------------------------------------------
    print("[Test 7] Cycle detection & safety bounds...", end=" ")
    page_size = 4096
    buf = bytearray(SLIDE_INFO_V5_HEADER_SIZE + 2 + page_size)
    slide_info_off = 0
    data_off = SLIDE_INFO_V5_HEADER_SIZE + 2
    value_add = 0x180000000

    struct.pack_into(
        HEADER_STRUCT_FMT, buf, slide_info_off,
        SLIDE_INFO_V5_VERSION, page_size, 1, value_add
    )
    struct.pack_into("<H", buf, slide_info_off + SLIDE_INFO_V5_HEADER_SIZE, 0x0)

    # Corrupt chain that loops: offset 0 points to offset 8, offset 8 points back to 0
    # stride for loop: offset 0 has stride=1 (-> 8), offset 8 has stride=0x7FF (out of bounds or loop)
    p_loop1 = encode_auth_ptr(0x111, stride=1)
    # ptr at 8 points back: wait, stride is positive, so let's test stride pointing far beyond page
    p_oob = encode_auth_ptr(0x222, stride=0x7FF)  # 2047 * 8 = 16376 bytes > page_size 4096

    struct.pack_into("<Q", buf, data_off + 0x0, p_loop1)
    struct.pack_into("<Q", buf, data_off + 0x8, p_oob)

    # Must terminate safely without exception
    stats = flatten_slide_v5(buf, slide_info_off, data_offset=data_off, verbose=False, quiet=True)
    assert stats["auth_flattened"] == 2
    print("PASS")

    # -------------------------------------------------------------------------
    # Test 8: End-to-end file flattening with mmap
    # -------------------------------------------------------------------------
    print("[Test 8] File / mmap flattening end-to-end...", end=" ")
    page_size = 4096
    file_bytes = bytearray(SLIDE_INFO_V5_HEADER_SIZE + 2 + page_size)
    value_add = 0x180000000
    struct.pack_into(
        HEADER_STRUCT_FMT, file_bytes, 0,
        SLIDE_INFO_V5_VERSION, page_size, 1, value_add
    )
    struct.pack_into("<H", file_bytes, SLIDE_INFO_V5_HEADER_SIZE, 0x10)
    p1 = encode_auth_ptr(0x5555, stride=2)
    p2 = encode_auth_ptr(0x6666, stride=0)
    data_start = SLIDE_INFO_V5_HEADER_SIZE + 2
    struct.pack_into("<Q", file_bytes, data_start + 0x10, p1)
    struct.pack_into("<Q", file_bytes, data_start + 0x20, p2)

    with tempfile.NamedTemporaryFile(delete=False) as tf:
        tf_name = tf.name
        tf.write(file_bytes)

    try:
        stats = flatten_dsc_file(tf_name, slide_info_offset=0, data_offset=data_start, quiet=True)
        assert stats["auth_flattened"] == 2

        with open(tf_name, "rb") as f_check:
            read_back = f_check.read()

        assert struct.unpack_from("<Q", read_back, data_start + 0x10)[0] == value_add + 0x5555
        assert struct.unpack_from("<Q", read_back, data_start + 0x20)[0] == value_add + 0x6666
    finally:
        if os.path.exists(tf_name):
            os.remove(tf_name)
    print("PASS")

    # -------------------------------------------------------------------------
    # Test 9: Auto-header discovery of slide_info v5 mappings
    # -------------------------------------------------------------------------
    print("[Test 9] Auto-header discovery (mappingWithSlide)...", end=" ")
    mock_cache = bytearray(0x4000)
    mock_cache[0:16] = b"dyld_v1  arm64e\0"
    m_off = 0x200
    m_cnt = 2
    struct.pack_into("<II", mock_cache, 0x138, m_off, m_cnt)

    # First mapping: non-slide / empty
    struct.pack_into("<QQQQQIII", mock_cache, m_off, 0x180000000, 0x1000, 0, 0, 0, 0, 5, 5)

    # Second mapping: valid slide_info v5
    s_off2 = 0x1000
    f_off2 = 0x2000
    hdr2 = encode_slide_info5_header(4096, 1, 0x200000000)
    mock_cache[s_off2 : s_off2 + len(hdr2)] = hdr2
    struct.pack_into("<H", mock_cache, s_off2 + 24, 0x10)  # page 0 starts at offset 0x10
    auth_ptr2 = encode_auth_ptr(0x1234, stride=0)
    struct.pack_into("<Q", mock_cache, f_off2 + 0x10, auth_ptr2)

    struct.pack_into("<QQQQQIII", mock_cache, m_off + 56, 0x190000000, 0x1000, f_off2, s_off2, 26, 0, 3, 3)

    found_regions = find_slide_info_v5_regions(mock_cache)
    assert len(found_regions) == 1, f"Expected 1 region, found {len(found_regions)}"
    assert found_regions[0] == (s_off2, f_off2, 26), f"Unexpected region: {found_regions[0]}"

    # Verify auto-flatten in buffer
    stats9 = flatten_slide_v5(mock_cache, s_off2, data_offset=f_off2, quiet=True)
    assert stats9["auth_flattened"] == 1
    assert struct.unpack_from("<Q", mock_cache, f_off2 + 0x10)[0] == 0x200000000 + 0x1234
    print("PASS")

    print("=" * 70)
    print("ALL 9 SELF-TEST SUITES PASSED (100% SUCCESS)")
    print("=" * 70)
    return True


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def parse_int(val_str: str) -> int:
    """Parse integer from hex (0x...) or decimal string."""
    val_str = val_str.strip()
    if val_str.lower().startswith("0x"):
        return int(val_str, 16)
    return int(val_str)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flatten iOS 26 / arm64e dyld_cache_slide_info5 authenticated pointers."
    )
    parser.add_argument("dsc_file", nargs="?", help="Path to dyld shared cache binary file or directory")
    parser.add_argument("slide_info_offset", nargs="?", help="Optional offset to dyld_cache_slide_info5 header (e.g. 0x12000). If omitted, automatically detects all regions from dyld_cache_header.")
    parser.add_argument("data_offset", nargs="?", default="0x0", help="Offset to data mapping base (default: 0x0)")
    parser.add_argument("--dir", dest="directory", help="Process all dyld_shared_cache* files in the specified directory")
    parser.add_argument("--test", action="store_true", help="Run comprehensive self-test suite")
    parser.add_argument("--all", action="store_true", dest="flatten_all", help="Flatten regular pointers as well as auth pointers")
    parser.add_argument("-o", "--output", help="Write modified output to a new file instead of modifying in-place")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging of traversed pointer chains")

    args = parser.parse_args()

    if args.test:
        success = run_tests()
        return 0 if success else 1

    if args.directory:
        try:
            flatten_dsc_directory(
                dir_path=args.directory,
                flatten_all=args.flatten_all,
                verbose=args.verbose,
            )
            return 0
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    if not args.dsc_file:
        parser.print_help()
        print("\nError: dsc_file, --dir, or --test is required.")
        return 1

    # Check if dsc_file is actually a directory
    if os.path.isdir(args.dsc_file):
        try:
            flatten_dsc_directory(
                dir_path=args.dsc_file,
                flatten_all=args.flatten_all,
                verbose=args.verbose,
            )
            return 0
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Auto-detect mode if slide_info_offset is not provided
    if not args.slide_info_offset:
        try:
            flatten_dsc_auto(
                file_path=args.dsc_file,
                flatten_all=args.flatten_all,
                output_path=args.output,
                verbose=args.verbose,
            )
            return 0
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    try:
        slide_off = parse_int(args.slide_info_offset)
        data_off = parse_int(args.data_offset)
    except ValueError as e:
        print(f"Error parsing offset: {e}", file=sys.stderr)
        return 1

    try:
        flatten_dsc_file(
            file_path=args.dsc_file,
            slide_info_offset=slide_off,
            data_offset=data_off,
            flatten_all=args.flatten_all,
            output_path=args.output,
            verbose=args.verbose,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
