#!/usr/bin/env python3
"""
kernel/patch_iokit_root.py

HakiOS XNU 12377 (iOS 26 ARM64E) IOKit Root Device Resolver
Target Device: Xiaomi Redmi Note 13 4G (Qualcomm Snapdragon SM6225 Bengal)

Instruments IOFindBSDRoot in iokit/bsddev/IOKitBSDInit.cpp to trace root device resolution
and ensure immediate detection of memory disk root (rd=md0):
  - Row 638: IOFindBSDRoot entered (Dark Cyan)
  - Row 640: Memory disk root (rd=md0) boot argument parsed (Lime)
  - Row 641: RAMDisk registered via mdevadd as md0 (Deep Pink)
Uses 100% direct VA drawer (hk_draw_band_direct) and hk_con_log for console output.
"""

import os
import sys

def patch_iokit_root(src_dir):
    target = os.path.join(src_dir, "iokit", "bsddev", "IOKitBSDInit.cpp")
    if not os.path.isfile(target):
        print(f"[-] Error: file not found: {target}")
        return False

    with open(target, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    c = content.replace("\r\n", "\n")

    if "/* hakios: iokit root resolution visual ladder bands */" in c:
        print("  [OK] iokit/bsddev/IOKitBSDInit.cpp: root bands already installed")
        return True

    # 0. Add file-scope extern "C" declarations at top of C++ file
    top_decl = (
        '/* hakios: C linkage declarations for early framebuffer and console */\n'
        '#include <stdint.h>\n'
        'extern "C" {\n'
        '    void hk_draw_band_direct(uint32_t color, uint32_t row_start, uint32_t row_count);\n'
        '    void hk_con_log(const char *s);\n'
        '}\n\n'
    )
    c = top_decl + c

    # 1. IOFindBSDRoot entry band (Row 638: Dark Cyan)
    idx = c.find("IOFindBSDRoot(")
    if idx != -1:
        brace_idx = c.find("{\n", idx)
        if brace_idx != -1:
            insert_pos = brace_idx + 2
            entry_band = (
                "\t/* hakios: iokit root resolution visual ladder bands */\n"
                "\t{ hk_draw_band_direct(0x00008B8B, 638, 4); hk_con_log(\"[*] iokit: IOFindBSDRoot searching for root device...\"); } /* Dark Cyan: IOFindBSDRoot entered (row 638) */\n"
            )
            c = c[:insert_pos] + entry_band + c[insert_pos:]
            print("  [OK] iokit/bsddev/IOKitBSDInit.cpp: IOFindBSDRoot entry band installed (row 638)")
        else:
            print("  [WARN] iokit/bsddev/IOKitBSDInit.cpp: opening brace for IOFindBSDRoot not found")
    else:
        print("  [WARN] iokit/bsddev/IOKitBSDInit.cpp: IOFindBSDRoot function signature not found")

    # 2. md0 root selection band: hook into the rd=md detection
    t_md0 = "bsdWait = kSecondScale / 10;"
    r_md0 = (
        "bsdWait = kSecondScale / 10;\n"
        "\t\t{ hk_draw_band_direct(0x0000FF00, 640, 4); hk_con_log(\"[+] iokit: root device rd=md0 detected\"); } /* Lime: rd=md root detected (row 640) */"
    )
    if t_md0 in c:
        c = c.replace(t_md0, r_md0, 1)
        print("  [OK] iokit/bsddev/IOKitBSDInit.cpp: rd=md0 detection band installed (row 640)")
    else:
        print("  [WARN] iokit/bsddev/IOKitBSDInit.cpp: bsdWait pattern not found")

    # 3. mdevadd RAMDisk registration band (Row 641: Deep Pink)
    t_mdev = "(void)mdevadd(-1, ml_static_ptovirt(ramdParms[0]) >> 12, (unsigned int) (ramdParms[1] >> 12), 0);"
    r_mdev = (
        "(void)mdevadd(-1, ml_static_ptovirt(ramdParms[0]) >> 12, (unsigned int) (ramdParms[1] >> 12), 0);\n"
        "\t\t\t\t{ hk_draw_band_direct(0x00FF1493, 641, 4); hk_con_log(\"[+] iokit: RAMDisk registered via mdevadd as md0\"); } /* Deep Pink: RAMDisk registered as md0 (row 641) */"
    )
    if t_mdev in c:
        c = c.replace(t_mdev, r_mdev, 1)
        print("  [OK] iokit/bsddev/IOKitBSDInit.cpp: RAMDisk registration band installed (row 641)")
    else:
        print("  [WARN] iokit/bsddev/IOKitBSDInit.cpp: mdevadd RAMDisk pattern not found")

    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(c)

    print("  [OK] iokit/bsddev/IOKitBSDInit.cpp successfully instrumented for root device resolution")
    return True

def main():
    if len(sys.argv) < 2:
        print("Usage: patch_iokit_root.py <xnu_src_dir>")
        sys.exit(1)

    src_dir = sys.argv[1]
    if not os.path.isdir(src_dir):
        print(f"[-] Error: directory does not exist: {src_dir}")
        sys.exit(1)

    print(f"=== Applying IOKit Root Device Resolver Patch to {src_dir} ===")
    if not patch_iokit_root(src_dir):
        sys.exit(1)
    print("=== IOKit root device resolver patch complete ===")

if __name__ == "__main__":
    main()
