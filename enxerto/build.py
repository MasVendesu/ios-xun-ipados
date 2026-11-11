#!/usr/bin/env python3
"""
Enxerto build script (pure Python).
Cross-compiles Enxerto bootloader for AArch64 and packages Android boot.img
without requiring WSL2, Linux, mkbootimg, cpio, or bash.

Supported boards:
  sweet     - Redmi Note 10 Pro (SM7150), boot.img header v2 with DTB
  beryllium - Poco F1 (SDM845), boot.img header v1 with Image.gz + appended DTB
  sapphire  - Redmi Note 13 4G (SM6225), boot.img header v4 (GKI layout)
  xun       - Redmi Pad SE (SM6225 tablet), boot.img header v4 (GKI layout)
"""

import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import gzip
from pathlib import Path


def align_up(val: int, align: int) -> int:
    return (val + align - 1) // align * align


def parse_os_ver(v_str: str, p_str: str) -> int:
    """Parse Android os_version ('13.0.0') and os_patch_level ('2025-01') into 32-bit field."""
    os_ver = 0
    if v_str:
        m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", v_str)
        if m:
            a = int(m.group(1))
            b = int(m.group(2)) if m.group(2) else 0
            c = int(m.group(3)) if m.group(3) else 0
            os_ver = (a << 14) | (b << 7) | c

    patch = 0
    if p_str:
        m = re.match(r"^(\d{4})-(\d{2})", p_str)
        if m:
            y = int(m.group(1)) - 2000
            mon = int(m.group(2))
            patch = (y << 4) | mon

    return (os_ver << 11) | patch


def make_cpio_newc(filename: bytes, data: bytes = b"", is_dir: bool = False, mode: int = 0o755) -> bytes:
    """Generate a single entry in standard cpio newc (SVR4 no CRC) format."""
    namesize = len(filename) + 1
    full_mode = (0o040000 if is_dir else 0o100000) | (mode & 0o777)
    filesize = len(data)
    hdr = f"070701{0:08x}{full_mode:08x}{0:08x}{0:08x}{1:08x}{0:08x}{filesize:08x}{0:08x}{0:08x}{0:08x}{0:08x}{namesize:08x}{0:08x}".encode("ascii")
    res = bytearray()
    res += hdr
    res += filename + b"\0"
    while len(res) % 4 != 0:
        res += b"\0"
    res += data
    while len(res) % 4 != 0:
        res += b"\0"
    return bytes(res)


def make_empty_ramdisk() -> bytes:
    """Create an empty gzipped newc cpio ramdisk archive."""
    cpio = make_cpio_newc(b".", is_dir=True, mode=0o755) + make_cpio_newc(b"TRAILER!!!", is_dir=False, mode=0)
    return gzip.compress(cpio, 9)


def pack_bootimg_v1(kernel_data: bytes, ramdisk_data: bytes, cmdline: str = "",
                    os_version: str = "16.0.0", os_patch_level: str = "2025-06",
                    base: int = 0, kernel_offset: int = 0x8000, ramdisk_offset: int = 0x1000000,
                    tags_offset: int = 0x100, page_size: int = 4096) -> bytes:
    """Pack Android boot.img header v1 (SDM845 / Poco F1 beryllium layout)."""
    ks = len(kernel_data)
    rs = len(ramdisk_data)
    os_ver_field = parse_os_ver(os_version, os_patch_level)
    cmd_bytes = cmdline.encode("ascii", "replace")[:1536]
    cmd1 = cmd_bytes[:512]
    cmd2 = cmd_bytes[512:1536]

    h = hashlib.sha1()
    h.update(kernel_data)
    h.update(struct.pack("<I", ks))
    h.update(ramdisk_data)
    h.update(struct.pack("<I", rs))
    id_digest = h.digest().ljust(32, b"\0")

    hdr = bytearray(1632)
    struct.pack_into("<8s10I16s512s32s1024s", hdr, 0,
                     b"ANDROID!",
                     ks, base + kernel_offset,
                     rs, base + ramdisk_offset,
                     0, 0, base + tags_offset,
                     page_size, 1, os_ver_field,
                     b"", cmd1, id_digest, cmd2)

    hdr += struct.pack("<IQI", 0, 0, 1648)  # recovery_dtbo_size, recovery_dtbo_offset, header_size
    hdr += b"\0" * (align_up(len(hdr), page_size) - len(hdr))

    out = bytearray(hdr)
    out += kernel_data + b"\0" * (align_up(ks, page_size) - ks)
    out += ramdisk_data + b"\0" * (align_up(rs, page_size) - rs)
    return bytes(out)


def pack_bootimg_v2(kernel_data: bytes, ramdisk_data: bytes, dtb_data: bytes, cmdline: str = "",
                    os_version: str = "17.0.0", os_patch_level: str = "2035-06",
                    base: int = 0, kernel_offset: int = 0x8000, ramdisk_offset: int = 0x1000000,
                    tags_offset: int = 0x100, dtb_offset: int = 0x1f00000, page_size: int = 4096) -> bytes:
    """Pack Android boot.img header v2 (SM7150 / Redmi Note 10 Pro sweet layout)."""
    ks = len(kernel_data)
    rs = len(ramdisk_data)
    ds = len(dtb_data)
    os_ver_field = parse_os_ver(os_version, os_patch_level)
    cmd_bytes = cmdline.encode("ascii", "replace")[:1536]
    cmd1 = cmd_bytes[:512]
    cmd2 = cmd_bytes[512:1536]

    h = hashlib.sha1()
    h.update(kernel_data)
    h.update(struct.pack("<I", ks))
    h.update(ramdisk_data)
    h.update(struct.pack("<I", rs))
    h.update(dtb_data)
    h.update(struct.pack("<I", ds))
    id_digest = h.digest().ljust(32, b"\0")

    hdr = bytearray(1632)
    struct.pack_into("<8s10I16s512s32s1024s", hdr, 0,
                     b"ANDROID!",
                     ks, base + kernel_offset,
                     rs, base + ramdisk_offset,
                     0, 0, base + tags_offset,
                     page_size, 2, os_ver_field,
                     b"", cmd1, id_digest, cmd2)

    hdr += struct.pack("<IQI", 0, 0, 1660)  # recovery_dtbo_size, recovery_dtbo_offset, header_size
    hdr += struct.pack("<IQ", ds, base + dtb_offset)  # dtb_size, dtb_addr
    hdr += b"\0" * (align_up(len(hdr), page_size) - len(hdr))

    out = bytearray(hdr)
    out += kernel_data + b"\0" * (align_up(ks, page_size) - ks)
    out += ramdisk_data + b"\0" * (align_up(rs, page_size) - rs)
    out += dtb_data + b"\0" * (align_up(ds, page_size) - ds)
    return bytes(out)


def pack_bootimg_v4(kernel_data: bytes, ramdisk_data: bytes, cmdline: str = "",
                    os_version: str = "13.0.0", os_patch_level: str = "2025-01") -> bytes:
    """Pack Android boot.img header v4 (SM6225 / Redmi Note 13 4G sapphire GKI layout)."""
    page_size = 4096
    ks = len(kernel_data)
    rs = len(ramdisk_data)
    os_ver_field = parse_os_ver(os_version, os_patch_level)
    cmd = cmdline.encode("ascii", "replace")[:1536].ljust(1536, b"\0")
    header_size = 1584
    header_version = 4
    signature_size = 0

    hdr = struct.pack("<8sIIII4II1536sI",
                      b"ANDROID!",
                      ks,
                      rs,
                      os_ver_field,
                      header_size,
                      0, 0, 0, 0,
                      header_version,
                      cmd,
                      signature_size)
    assert len(hdr) == 1584
    hdr += b"\0" * (page_size - len(hdr))

    out = bytearray(hdr)
    out += kernel_data + b"\0" * (align_up(ks, page_size) - ks)
    out += ramdisk_data + b"\0" * (align_up(rs, page_size) - rs)
    return bytes(out)


def find_toolchain(user_toolchain: str = None) -> dict:
    """Locate AArch64 GCC cross-compilation binaries."""
    candidates_dirs = []
    prefix_override = None

    if user_toolchain:
        p = Path(user_toolchain)
        if p.is_dir():
            candidates_dirs.append(p)
        else:
            candidates_dirs.append(p.parent)
            prefix_override = p.name

    # Check env vars
    for ev in ("CROSS", "TOOLCHAIN", "AARCH64_TOOLCHAIN"):
        val = os.environ.get(ev)
        if val:
            p = Path(val)
            if p.is_dir():
                candidates_dirs.append(p)
            elif not prefix_override:
                candidates_dirs.append(p.parent)
                prefix_override = p.name

    # Common portable toolchain unzip paths on Windows and macOS
    home = Path.home()
    candidates_dirs.extend([
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        home / "Downloads" / "arm-toolchain" / "arm-gnu-toolchain-13.3.rel1-mingw-w64-i686-aarch64-none-elf" / "bin",
        home / "Downloads" / "arm-toolchain" / "bin",
        Path("C:/aarch64/bin"),
        Path("C:/arm-gnu-toolchain/bin"),
        Path("D:/aarch64/bin"),
        Path("C:/msys64/mingw64/bin"),
        Path("C:/msys64/ucrt64/bin"),
    ])

    prefixes = [prefix_override] if prefix_override else ["aarch64-none-elf-", "aarch64-linux-gnu-", "aarch64-elf-"]

    for d in candidates_dirs:
        if not d.is_dir():
            continue
        for pfx in prefixes:
            if not pfx:
                continue
            gcc = d / f"{pfx}gcc"
            gcc_exe = d / f"{pfx}gcc.exe"
            target_gcc = gcc_exe if gcc_exe.is_file() else (gcc if gcc.is_file() else None)
            if target_gcc:
                bin_dir = target_gcc.parent
                return {
                    "cc": str(target_gcc),
                    "objcopy": str(bin_dir / f"{pfx}objcopy" if not (bin_dir / f"{pfx}objcopy.exe").is_file() else bin_dir / f"{pfx}objcopy.exe"),
                    "readelf": str(bin_dir / f"{pfx}readelf" if not (bin_dir / f"{pfx}readelf.exe").is_file() else bin_dir / f"{pfx}readelf.exe"),
                    "nm": str(bin_dir / f"{pfx}nm" if not (bin_dir / f"{pfx}nm.exe").is_file() else bin_dir / f"{pfx}nm.exe"),
                    "prefix": pfx,
                }

    # Search PATH via shutil.which
    for pfx in prefixes:
        if not pfx:
            continue
        w = shutil.which(f"{pfx}gcc")
        if w:
            w_path = Path(w)
            bin_dir = w_path.parent
            return {
                "cc": str(w_path),
                "objcopy": str(shutil.which(f"{pfx}objcopy") or bin_dir / f"{pfx}objcopy"),
                "readelf": str(shutil.which(f"{pfx}readelf") or bin_dir / f"{pfx}readelf"),
                "nm": str(shutil.which(f"{pfx}nm") or bin_dir / f"{pfx}nm"),
                "prefix": pfx,
            }

    return None


def run_cmd(cmd: list, cwd: Path = None, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and res.returncode != 0:
        print(f"[!] Error running command: {' '.join(str(c) for c in cmd)}", file=sys.stderr)
        if res.stdout:
            print(f"--- stdout ---\n{res.stdout}", file=sys.stderr)
        if res.stderr:
            print(f"--- stderr ---\n{res.stderr}", file=sys.stderr)
        sys.exit(res.returncode)
    return res


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="Enxerto Pure Python Build & Packaging Tool")
    parser.add_argument("--board", choices=["sweet", "beryllium", "sapphire", "xun"], default=os.environ.get("BOARD", "sweet"),
                        help="Target device board (default: %(default)s)")
    parser.add_argument("--toolchain", help="Path to toolchain bin directory or toolchain prefix")
    parser.add_argument("--payload", help="Path to xnu payload.bin to include in boot image")
    parser.add_argument("--dtb", help="Path to device tree blob (required for sweet and beryllium)")
    parser.add_argument("--bootlog", choices=["0", "1"], default=os.environ.get("BOOTLOG", "0"),
                        help="Enable boot UI log window (default: %(default)s)")
    parser.add_argument("--charger", choices=["0", "1"], default=os.environ.get("CHARGER", "1"),
                        help="Enable early charging driver (forced 0 for sapphire, default: %(default)s)")
    parser.add_argument("--bootpause", default=os.environ.get("BOOTPAUSE"),
                        help="Pause in ms before jumping to kernel")
    parser.add_argument("--bergamota", choices=["0", "1"], default=os.environ.get("BERGAMOTA"),
                        help="Force bergamota.fastboot flag in cmdline")
    parser.add_argument("--imgsuffix", default=os.environ.get("IMGSUFFIX"),
                        help="Suffix to append to output image copies (e.g. -chg)")
    parser.add_argument("--skip-host-tests", action="store_true",
                        help="Skip compiling/running host PPM and fastboot unit tests")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned operations without compiling")

    args = parser.parse_args()

    enxerto_dir = Path(__file__).resolve().parent
    out_dir = enxerto_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Enxerto Python Build System ===")
    print(f"Board:     {args.board}")
    print(f"Workdir:   {enxerto_dir}")

    # Resolve DTB
    dtb_path = None
    if args.dtb:
        dtb_path = Path(args.dtb).resolve()
    else:
        if args.board == "sweet":
            dtb_path = (enxerto_dir.parent / "dtb/boot_unpacked/dtb").resolve()
        elif args.board == "beryllium":
            dtb_path = (enxerto_dir.parent / "dtb/beryllium/dtb").resolve()
        elif args.board == "sapphire":
            dtb_path = (enxerto_dir.parent / "dtb/sapphire/dtb").resolve()
        elif args.board == "xun":
            dtb_path = (enxerto_dir.parent / "dtb/xun/dtb").resolve()

    if args.board not in ("sapphire", "xun"):
        if not dtb_path or not dtb_path.is_file():
            if args.dry_run:
                print(f"[dry-run] Warning: DTB file '{dtb_path}' not found, but continuing dry-run.")
            else:
                print(f"[!] Error: DTB file '{dtb_path}' not found (stock DTB required for {args.board}).", file=sys.stderr)
                sys.exit(1)

    # Toolchain resolution
    toolchain = find_toolchain(args.toolchain)
    if not toolchain:
        print("\n[!] No AArch64 GCC cross-compiler found on system or provided paths.", file=sys.stderr)
        print("To build Enxerto on Windows without WSL2 or Linux VM:\n", file=sys.stderr)
        print("1. Download portable Arm GNU Toolchain zip (~100 MB):", file=sys.stderr)
        print("   https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads", file=sys.stderr)
        print("   (e.g. arm-gnu-toolchain-*-mingw-w64-i686-aarch64-none-elf.zip)\n", file=sys.stderr)
        print("2. Extract the archive to any folder, e.g. C:\\aarch64\n", file=sys.stderr)
        print("3. Run this script pointing to the bin directory:", file=sys.stderr)
        print(f"   python enxerto/build.py --board {args.board} --toolchain C:\\aarch64\\bin\n", file=sys.stderr)
        if not args.dry_run:
            sys.exit(1)
        toolchain = {
            "cc": "aarch64-none-elf-gcc",
            "objcopy": "aarch64-none-elf-objcopy",
            "readelf": "aarch64-none-elf-readelf",
            "nm": "aarch64-none-elf-nm",
            "prefix": "aarch64-none-elf-",
        }

    print(f"Compiler:  {toolchain['cc']}")
    print(f"Objcopy:   {toolchain['objcopy']}")
    print(f"Readelf:   {toolchain['readelf']}")
    print(f"NM:        {toolchain['nm']}")

    # Setup CFLAGS
    cflags = [
        "-O2", "-ffreestanding", "-nostdlib", "-fno-pic",
        "-mgeneral-regs-only", "-mstrict-align", "-fno-stack-protector",
        "-fno-tree-loop-distribute-patterns", "-Wall", "-Wextra"
    ]
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=enxerto_dir, text=True).strip()
    except Exception:
        git_sha = "unknown"
    cflags.append(f"-DENXERTO_GIT_SHA={git_sha}")
    print(f"Build SHA: {git_sha}")
    if args.bootlog == "1":
        cflags.append("-DBOOTUI_LOG=1")

    # Charger flag
    if args.board in ("sapphire", "xun") or args.charger == "0":
        cflags.append("-DENXERTO_CHARGER=0")

    if args.bootpause:
        cflags.append(f"-DBOOT_PAUSE_MS={args.bootpause}")

    if args.board == "beryllium":
        cflags.append("-DBOARD_BERYLLIUM")
    elif args.board == "sapphire":
        cflags.append("-DBOARD_SAPPHIRE")
    elif args.board == "xun":
        cflags.append("-DBOARD_XUN")

    # Cmdline
    cmdline = "console=null"
    if args.bergamota == "1":
        cmdline += " bergamota.fastboot=1"
    elif args.bergamota == "0":
        cmdline += " bergamota.fastboot=0"

    srcs = [
        "start.S", "enxerto.c", "console.c", "bootui.c", "fbui.c", "mmu.c",
        "fdt.c", "loader.c", "ufs.c", "inflate.c", "usb_dwc3.c", "usb_power.c",
        "fastboot.c", "sparse.c", "storage.c", "blake2b.c", "bergamota.c",
        "pmic.c", "charge.c", "charge_mode.c", "charger.c"
    ]

    objs = []
    print("\n--- Compiling Enxerto objects ---")
    for s in srcs:
        src_path = enxerto_dir / s
        stem = src_path.stem
        obj_path = out_dir / f"{stem}.o"
        objs.append(obj_path)
        cmd = [toolchain["cc"]] + cflags + ["-c", str(src_path), "-o", str(obj_path)]
        if args.dry_run:
            print(f"[dry-run] {' '.join(cmd)}")
        else:
            print(f"  CC {s} -> out/{stem}.o")
            run_cmd(cmd, cwd=enxerto_dir)

    if args.dry_run:
        print("[dry-run] Skipping link and binary steps.")
        return

    # Check for absolute relocations in C objects
    print("\n--- Checking relocations ---")
    c_objs = [str(o) for o in objs if o.name != "start.o"]
    res = run_cmd([toolchain["readelf"], "-r"] + c_objs, cwd=enxerto_dir)
    abs_count = len(re.findall(r"\b(?:R_AARCH64_)?ABS64\b", res.stdout))
    if abs_count != 0:
        print(f"[!] Error: {abs_count} absolute relocations found in C objects!", file=sys.stderr)
        sys.exit(1)
    print("  ✓ Zero absolute relocations in C objects.")

    # Link ELF
    elf_path = out_dir / "enxerto.elf"
    ld_script = enxerto_dir / "enxerto.ld"
    print("\n--- Linking ELF ---")
    link_cmd = [
        toolchain["cc"]
    ] + cflags + [
        "-static", "-T", str(ld_script),
        "-Wl,--build-id=none", "-Wl,--no-warn-rwx-segments", "-Wl,-N"
    ] + [str(o) for o in objs] + [
        "-o", str(elf_path), "-lgcc"
    ]
    run_cmd(link_cmd, cwd=enxerto_dir)
    print(f"  Linked {elf_path.name}")

    # Extract binary image
    image_path = out_dir / "Image"
    print("\n--- Generating Image binary ---")
    run_cmd([toolchain["objcopy"], "-O", "binary", str(elf_path), str(image_path)], cwd=enxerto_dir)

    # Pad to _end symbol
    nm_res = run_cmd([toolchain["nm"], str(elf_path)], cwd=enxerto_dir)
    end_size = None
    for line in nm_res.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[2] == "_end":
            end_size = int(parts[0], 16)
            break
    if end_size is None:
        print("[!] Error: symbol '_end' not found in ELF", file=sys.stderr)
        sys.exit(1)

    with open(image_path, "r+b") as f:
        f.truncate(end_size)
    print(f"  Padded Image to _end: {end_size} bytes (0x{end_size:x})")

    # Ramdisk generation
    print("\n--- Preparing ramdisk ---")
    ramdisk_path = out_dir / "ramdisk.bin"
    empty_ramdisk_path = out_dir / "ramdisk-empty.bin"

    empty_rd_bytes = make_empty_ramdisk()
    empty_ramdisk_path.write_bytes(empty_rd_bytes)
    print(f"  Generated empty ramdisk: {len(empty_rd_bytes)} bytes")

    if args.payload:
        p = Path(args.payload).resolve()
        if not p.is_file():
            print(f"[!] Error: payload file '{p}' not found", file=sys.stderr)
            sys.exit(1)
        if args.board in ("sapphire", "xun"):
            # GKI ABL ignores v4 boot ramdisk; append payload directly to Image at 4K boundary
            payload_data = p.read_bytes()
            pad_sz = align_up(end_size, 4096)
            with open(image_path, "r+b") as f:
                f.truncate(pad_sz)
                f.seek(pad_sz)
                f.write(payload_data)
                new_total = f.tell()
                f.seek(16)
                f.write(struct.pack("<Q", new_total))
            print(f"  Appended payload to Image at 4K boundary: {len(payload_data)} bytes (total Image: {new_total} bytes)")
            ramdisk_path.write_bytes(empty_rd_bytes)
        else:
            shutil.copyfile(p, ramdisk_path)
            print(f"  Copied payload from {p.name} ({p.stat().st_size} bytes)")
    else:
        ramdisk_path.write_bytes(empty_rd_bytes)
        print("  Using empty ramdisk as default payload")

    kernel_bytes = image_path.read_bytes()
    ramdisk_bytes = ramdisk_path.read_bytes()

    # Package boot images
    print(f"\n--- Packing Android boot images ({args.board}) ---")
    boot_img_path = out_dir / "enxerto-boot.img"
    charge_img_path = out_dir / "charge-test.img"
    fastboot_img_path = out_dir / "bergamota-fastboot.img"
    fastboot_flash_path = out_dir / "bergamota-fastboot-flash.img"

    if args.board == "beryllium":
        dtb_bytes = dtb_path.read_bytes()
        gz_kernel = gzip.compress(kernel_bytes, 9, mtime=0) + dtb_bytes
        (out_dir / "Image.gz-dtb").write_bytes(gz_kernel)

        boot_img_path.write_bytes(pack_bootimg_v1(gz_kernel, ramdisk_bytes, cmdline=cmdline))
        charge_img_path.write_bytes(pack_bootimg_v1(gz_kernel, empty_rd_bytes, cmdline="console=null bergamota.charge=1"))
        fb_bytes = pack_bootimg_v1(gz_kernel, empty_rd_bytes, cmdline="console=null bergamota.fastboot=1")
        fastboot_img_path.write_bytes(fb_bytes)
        fastboot_flash_path.write_bytes(fb_bytes)

    elif args.board == "sweet":
        dtb_bytes = dtb_path.read_bytes()
        boot_img_path.write_bytes(pack_bootimg_v2(kernel_bytes, ramdisk_bytes, dtb_bytes, cmdline=cmdline))
        # sweet doesn't package separate charge-test / bergamota-fastboot in build.sh, but can if needed

    elif args.board in ("sapphire", "xun"):
        boot_img_path.write_bytes(pack_bootimg_v4(kernel_bytes, ramdisk_bytes, cmdline=cmdline))
        charge_img_path.write_bytes(pack_bootimg_v4(kernel_bytes, empty_rd_bytes, cmdline="console=null bergamota.charge=1"))
        fb_bytes = pack_bootimg_v4(kernel_bytes, empty_rd_bytes, cmdline="console=null bergamota.fastboot=1")
        fastboot_img_path.write_bytes(fb_bytes)
        fastboot_flash_path.write_bytes(fb_bytes)

    # Optional IMGSUFFIX copies
    if args.imgsuffix:
        for f in ["enxerto-boot", "bergamota-fastboot", "bergamota-fastboot-flash", "charge-test"]:
            p = out_dir / f"{f}.img"
            if p.is_file():
                shutil.copyfile(p, out_dir / f"{f}{args.imgsuffix}.img")

    # Host test execution (optional)
    if not args.skip_host_tests:
        host_cc = shutil.which("gcc") or shutil.which("clang")
        if host_cc:
            print("\n--- Running host tests ---")
            host_cflags = ["-O1", "-Wall"]
            if args.board == "beryllium":
                host_cflags.append("-DBOARD_BERYLLIUM")
            elif args.board == "sapphire":
                host_cflags.append("-DBOARD_SAPPHIRE")
            elif args.board == "xun":
                host_cflags.append("-DBOARD_XUN")
            if args.bootlog == "1":
                host_cflags.append("-DBOOTUI_LOG=1")

            (out_dir / "preview").mkdir(parents=True, exist_ok=True)
            host_test_exe = out_dir / ("host_test.exe" if sys.platform == "win32" else "host_test")
            run_cmd([host_cc] + host_cflags + ["-o", str(host_test_exe), "console.c", "bootui.c", "fbui.c", "charge.c", "host_test.c"], cwd=enxerto_dir)
            run_cmd([str(host_test_exe)], cwd=enxerto_dir)
            print("  ✓ Host UI test passed.")

            fastboot_test_exe = out_dir / ("fastboot_test.exe" if sys.platform == "win32" else "fastboot_test")
            run_cmd([host_cc, "-O1", "-Wall", "-Wextra", "-o", str(fastboot_test_exe), "fastboot.c", "sparse.c", "storage.c", "blake2b.c", "fastboot_test.c"], cwd=enxerto_dir)
            run_cmd([str(fastboot_test_exe)], cwd=enxerto_dir)
            print("  ✓ Host fastboot test passed.")
        else:
            print("\n[i] Note: Host C compiler (gcc/clang) not found; host preview tests skipped.")

    # Summary
    print("\n=== Build Completed Successfully ===")
    images = [image_path, boot_img_path, charge_img_path, fastboot_img_path]
    for img in images:
        if img.is_file():
            sha = hashlib.sha256(img.read_bytes()).hexdigest()
            print(f"  {img.name:<24} {img.stat().st_size:>9} bytes  sha256:{sha[:16]}...")


if __name__ == "__main__":
    main()
