#!/usr/bin/env python3
"""
kernel/patch_bsd_init.py

HakiOS XNU 12377 (iOS 26 ARM64E) BSD Subsystem Lifecycle Monitor
Target Device: Xiaomi Redmi Note 13 4G (Qualcomm Snapdragon SM6225 Bengal)

Instruments bsd_init() in bsd/kern/bsd_init.c with fine-grained visual diagnostic bands
(rows 500..580) to monitor BSD bootstrap stages:
  - Row 500: bsd_init entered (Goldenrod)
  - Row 510: bcache_init completed (Orange)
  - Row 520: vfs_init completed (Yellow Green)
  - Row 530: devfs_kernel_mount completed (Dodger Blue)
  - Row 540: mbuf_init completed (Medium Orchid)
  - Row 550: net_init completed (Hot Pink)
  - Row 580: bsd_init completed / handoff to root mount & launchd (Cyan)
"""

import os
import sys

def patch_bsd_init(src_dir):
    target = os.path.join(src_dir, "bsd", "kern", "bsd_init.c")
    if not os.path.isfile(target):
        print(f"[-] Error: file not found: {target}")
        return False

    with open(target, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    c = content.replace("\r\n", "\n")

    if "/* hakios: bsd_init lifecycle milestone ladder bands */" in c:
        print("  [ALREADY PATCHED] bsd/kern/bsd_init.c: milestone bands already installed")
        return True

    # 1. bsd_init entry band
    t_entry = "bsd_init(void)\n{\n"
    r_entry = (
        "bsd_init(void)\n{\n"
        "\t/* hakios: bsd_init lifecycle milestone ladder bands */\n"
        "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); extern void hk_con_log(const char *); hk_draw_band_direct(0x00DAA520, 600, 4); hk_con_log(\"[+] bsd_init: entered BSD bootstrap\"); } /* Goldenrod: bsd_init entered */\n"
    )
    if t_entry in c:
        c = c.replace(t_entry, r_entry, 1)
        print("  [OK] bsd/kern/bsd_init.c: bsd_init entry band installed (row 600)")
    else:
        print("  [WARN] bsd/kern/bsd_init.c: bsd_init(void) entry pattern not found")

    # 2. bsd_bufferinit band (Row 606: Orange)
    t_bcache = "bsd_bufferinit();"
    r_bcache = (
        "bsd_bufferinit();\n"
        "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); extern void hk_con_log(const char *); hk_draw_band_direct(0x00FFA500, 606, 4); hk_con_log(\"[+] bsd_init: buffer cache initialized\"); } /* Orange: bsd_bufferinit done */"
    )
    if t_bcache in c:
        c = c.replace(t_bcache, r_bcache, 1)
        print("  [OK] bsd/kern/bsd_init.c: bsd_bufferinit band installed (row 606)")
    else:
        # Fallback to legacy bcache_init if present
        t_bcache_alt = "bcache_init();"
        if t_bcache_alt in c:
            r_bcache_alt = (
                "bcache_init();\n"
                "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FFA500, 606, 4); } /* Orange: bcache_init done */"
            )
            c = c.replace(t_bcache_alt, r_bcache_alt, 1)
            print("  [OK] bsd/kern/bsd_init.c: bcache_init band installed via alt anchor (row 606)")
        else:
            print("  [WARN] bsd/kern/bsd_init.c: bsd_bufferinit/bcache_init pattern not found")

    # 3. vfsinit band (Row 612: Yellow Green)
    t_vfs = "vfsinit();"
    r_vfs = (
        "vfsinit();\n"
        "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); extern void hk_con_log(const char *); hk_draw_band_direct(0x009ACD32, 612, 4); hk_con_log(\"[+] bsd_init: vfs initialized\"); } /* Yellow Green: vfsinit done */"
    )
    if t_vfs in c:
        c = c.replace(t_vfs, r_vfs, 1)
        print("  [OK] bsd/kern/bsd_init.c: vfsinit band installed (row 612)")
    else:
        # Fallback to legacy vfs_init if present
        t_vfs_alt = "vfs_init();"
        if t_vfs_alt in c:
            r_vfs_alt = (
                "vfs_init();\n"
                "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x009ACD32, 612, 4); } /* Yellow Green: vfs_init done */"
            )
            c = c.replace(t_vfs_alt, r_vfs_alt, 1)
            print("  [OK] bsd/kern/bsd_init.c: vfs_init band installed via alt anchor (row 612)")
        else:
            print("  [WARN] bsd/kern/bsd_init.c: vfsinit/vfs_init pattern not found")

    # 4. devfs_kernel_mount band (Row 618: Dodger Blue)
    t_devfs = "devfs_kernel_mount(mounthere);"
    r_devfs = (
        "devfs_kernel_mount(mounthere);\n"
        "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); extern void hk_con_log(const char *); hk_draw_band_direct(0x001E90FF, 618, 4); hk_con_log(\"[+] bsd_init: devfs mounted\"); } /* Dodger Blue: devfs mounted */"
    )
    if t_devfs in c:
        c = c.replace(t_devfs, r_devfs, 1)
        print("  [OK] bsd/kern/bsd_init.c: devfs band installed (row 618)")
    else:
        print("  [WARN] bsd/kern/bsd_init.c: devfs_kernel_mount pattern not found")

    # 5. mbinit band (Row 624: Medium Orchid)
    t_mbuf = "mbinit();"
    r_mbuf = (
        "mbinit();\n"
        "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00BA55D3, 624, 4); } /* Medium Orchid: mbinit done */"
    )
    if t_mbuf in c:
        c = c.replace(t_mbuf, r_mbuf, 1)
        print("  [OK] bsd/kern/bsd_init.c: mbinit band installed (row 624)")
    else:
        # Fallback to legacy mbuf_init if present
        t_mbuf_alt = "mbuf_init();"
        if t_mbuf_alt in c:
            r_mbuf_alt = (
                "mbuf_init();\n"
                "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00BA55D3, 624, 4); } /* Medium Orchid: mbuf_init done */"
            )
            c = c.replace(t_mbuf_alt, r_mbuf_alt, 1)
            print("  [OK] bsd/kern/bsd_init.c: mbuf_init band installed via alt anchor (row 624)")
        else:
            print("  [WARN] bsd/kern/bsd_init.c: mbinit/mbuf_init pattern not found")

    # 6. net_init_run band (Row 630: Hot Pink)
    t_net = "net_init_run();"
    r_net = (
        "net_init_run();\n"
        "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FF69B4, 630, 4); } /* Hot Pink: net_init_run done */"
    )
    if t_net in c:
        c = c.replace(t_net, r_net, 1)
        print("  [OK] bsd/kern/bsd_init.c: net_init_run band installed (row 630)")
    else:
        # Fallback to legacy net_init if present
        t_net_alt = "net_init();"
        if t_net_alt in c:
            r_net_alt = (
                "net_init();\n"
                "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FF69B4, 630, 4); } /* Hot Pink: net_init done */"
            )
            c = c.replace(t_net_alt, r_net_alt, 1)
            print("  [OK] bsd/kern/bsd_init.c: net_init band installed via alt anchor (row 630)")
        else:
            print("  [WARN] bsd/kern/bsd_init.c: net_init_run/net_init pattern not found")

    # 7. bsd_init completed band (Row 636: Cyan)
    t_done = 'bsd_init_kprintf("calling bsd_autoconf\\n");'
    r_done = (
        'bsd_init_kprintf("calling bsd_autoconf\\n");\n'
        '\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x0000FFFF, 636, 4); } /* Cyan: bsd_init autoconf -> launchd */'
    )
    if t_done in c:
        c = c.replace(t_done, r_done, 1)
        print("  [OK] bsd/kern/bsd_init.c: bsd_init completed band installed (row 636)")
    else:
        # Fallback to devfs_kernel_mount follow-up
        t_done_alt = 'bsd_init_kprintf("devfs_kernel_mount returned %d\\n", error);'
        r_done_alt = (
            'bsd_init_kprintf("devfs_kernel_mount returned %d\\n", error);\n'
            '\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x0000FFFF, 636, 4); } /* Cyan: bsd_init autoconf -> launchd */'
        )
        if t_done_alt in c:
            c = c.replace(t_done_alt, r_done_alt, 1)
            print("  [OK] bsd/kern/bsd_init.c: bsd_init completed band installed via alt anchor (row 636)")
        else:
            print("  [WARN] bsd/kern/bsd_init.c: bsd_init autoconf completion pattern not found")

    # 8. vfs_mountroot succeeded band (Row 642: Spring Green)
    t_vfsroot = "if (0 == (err = vfs_mountroot())) {"
    r_vfsroot = (
        "if (0 == (err = vfs_mountroot())) {\n"
        "\t\t\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); extern void hk_con_log(const char *); hk_draw_band_direct(0x0000FF7F, 642, 4); hk_con_log(\"[+] bsd_init: root filesystem mounted OK! (mockfs/ramdisk)\"); } /* Spring Green: vfs_mountroot succeeded */"
    )
    if t_vfsroot in c:
        c = c.replace(t_vfsroot, r_vfsroot, 1)
        print("  [OK] bsd/kern/bsd_init.c: vfs_mountroot success band installed (row 642)")
    else:
        print("  [WARN] bsd/kern/bsd_init.c: vfs_mountroot pattern not found")

    # 9. bsd_utaskbootstrap band (Row 648: Deep Sky Blue)
    t_utask = "bsd_utaskbootstrap();"
    r_utask = (
        "bsd_utaskbootstrap();\n"
        "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); extern void hk_con_log(const char *); hk_draw_band_direct(0x0000BFFF, 648, 4); hk_con_log(\"[+] bsd_init: bsd_utaskbootstrap (PID 1 task created)\"); } /* Deep Sky Blue: PID 1 process created */"
    )
    if t_utask in c:
        c = c.replace(t_utask, r_utask, 1)
        print("  [OK] bsd/kern/bsd_init.c: bsd_utaskbootstrap band installed (row 648)")
    else:
        print("  [WARN] bsd/kern/bsd_init.c: bsd_utaskbootstrap pattern not found")

    # 10. bsdinit_task entry band (Row 654: Dark Orange)
    t_bsdinit = "bsdinit_task(void)\n{\n"
    r_bsdinit = (
        "bsdinit_task(void)\n{\n"
        "\t/* hakios: PID 1 kernel bootstrap */\n"
        "\t/* hakios/claude: clear diagnostic bit 63 in TPIDR_EL0 before userland */\n"
        "\t__asm__ volatile (\"mrs x0, tpidr_el0; bic x0, x0, #(1ULL << 63); msr tpidr_el0, x0\" ::: \"x0\");\n"
        "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); extern void hk_con_log(const char *); hk_draw_band_direct(0x00FF8C00, 654, 4); hk_con_log(\"[+] bsdinit_task: PID 1 bootstrap thread running\"); } /* Dark Orange: bsdinit_task entered */\n"
    )
    if t_bsdinit in c:
        c = c.replace(t_bsdinit, r_bsdinit, 1)
        print("  [OK] bsd/kern/bsd_init.c: bsdinit_task entry band installed (row 654)")
    else:
        print("  [WARN] bsd/kern/bsd_init.c: bsdinit_task entry pattern not found")

    # 11. load_init_program return band (Row 660: Magenta, Row 666: DodgerBlue)
    t_loadinit = "load_init_program(p);\n"
    r_pre_loadinit = "\t{ extern void hk_con_log(const char *); hk_con_log(\"[*] bsdinit_task: executing load_init_program(/sbin/launchd)...\"); }\n\tload_init_program(p);\n"
    r_loadinit = (
        "load_init_program(p);\n"
        "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); extern void hk_con_log(const char *); hk_draw_band_direct(0x00FF00FF, 660, 4); hk_con_log(\"[+] bsdinit_task: load_init_program returned OK!\"); } /* Magenta: load_init_program returned */\n"
        "\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); extern void hk_con_log(const char *); hk_draw_band_direct(0x001E90FF, 666, 4); hk_con_log(\"[+] HakiOS: Spawning PID 1 (/sbin/launchd) at EL0...\"); hk_con_log(\"[+] ========================================\"); hk_con_log(\"[+] WELCOME TO USERLAND (EL0)!\"); hk_con_log(\"[+] ========================================\"); } /* DodgerBlue: bsdinit_task returning to user AST */\n"
    )
    if t_loadinit in c:
        c = c.replace(t_loadinit, r_loadinit.replace("load_init_program(p);\n", r_pre_loadinit, 1), 1)
        print("  [OK] bsd/kern/bsd_init.c: load_init_program band installed (row 660)")
    else:
        print("  [WARN] bsd/kern/bsd_init.c: load_init_program pattern not found")

    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(c)

    print("  [OK] bsd/kern/bsd_init.c successfully patched with BSD lifecycle ladder")

    # 11b. KVA framebuffer helpers in osfmk/console/video_console.c.
    # Draw through vinfo.v_baseaddr (XNU's own write-combine KVA mapping of the
    # ABL framebuffer): MMU stays on, interrupts stay on, no TTBR0/SCTLR games,
    # strings and iso_font are read through normal cached KVAs.
    target_vc = os.path.join(src_dir, "osfmk", "console", "video_console.c")
    if os.path.isfile(target_vc):
        with open(target_vc, "r", encoding="utf-8", errors="replace") as f:
            cvc = f.read().replace("\r\n", "\n")
        if "hk_kva_fb_ready" not in cvc:
            # Capture the framebuffer mapping exactly as initialize_screen built it.
            # vinfo.v_baseaddr is NOT usable as the panel origin: patch_cortex_a73.py
            # step 21 shifts it by +720 rows and shrinks v_height by 720 so the console
            # starts below the ladder. The capture is written from the un-shifted
            # expression, so it is correct whichever patch script runs first.
            cap_decl_target = "void\ninitialize_screen(PE_Video * boot_vinfo, unsigned int op)\n{"
            cap_decl = (
                "/* hakios: raw framebuffer mapping captured in initialize_screen (full panel, row 0) */\n"
                "vm_offset_t hk_fb_kva_base = 0;\n"
                "uint32_t hk_fb_width = 0, hk_fb_height = 0, hk_fb_rowbytes = 0;\n"
                "int hk_fb_made_mapping = -1; /* -1: never captured, 0: makeMapping FALSE, 1: TRUE */\n\n"
            )
            cap_target_options = [
                "new_vinfo.v_baseaddr = 0xfffffff004000000ULL + boot_vinfo->v_offset;",
                "new_vinfo.v_baseaddr = newVideoVirt + boot_vinfo->v_offset;",
            ]
            cap_target = None
            for opt in cap_target_options:
                if opt in cvc:
                    cap_target = opt
                    break

            cap_line = (
                "\n\t\t\t/* hakios: record mapping before any ladder shift */\n"
                "\t\t\thk_fb_kva_base = (vm_offset_t)new_vinfo.v_baseaddr;\n"
                "\t\t\thk_fb_width = (uint32_t)new_vinfo.v_width;\n"
                "\t\t\thk_fb_height = (uint32_t)boot_vinfo->v_height;\n"
                "\t\t\thk_fb_rowbytes = (uint32_t)new_vinfo.v_rowbytes;\n"
                "\t\t\thk_fb_made_mapping = makeMapping ? 1 : 0;"
            )
            if cap_decl_target in cvc and cap_target and cvc.count(cap_target) == 1:
                cvc = cvc.replace(cap_decl_target, cap_decl + cap_decl_target, 1)
                cvc = cvc.replace(cap_target, cap_target + cap_line, 1)
                print("  [OK] osfmk/console/video_console.c: framebuffer mapping capture installed in initialize_screen")
            else:
                print(f"  [ERROR] osfmk/console/video_console.c: initialize_screen capture anchors not found (cap_decl_target={cap_decl_target in cvc}, cap_target={cap_target})")
                return False
            cvc += r'''

/* hakios: framebuffer drawing through XNU's KVA mapping (no MMU-off path).
 * Rows are absolute panel rows (row 0 = top of the panel), same as hk_draw_band. */
int hk_kva_fb_ready(void);
int hk_fb_mapping_state(void);
vm_offset_t hk_get_manual_kva(void);
void hk_kva_fill(uint32_t color, uint32_t row, uint32_t count);
void hk_kva_text(const char *s, uint32_t len, uint32_t row);
void hk_run_pid1_diagnostics(void);

int
hk_fb_mapping_state(void)
{
	return hk_fb_made_mapping;
}

vm_offset_t hk_manual_kva = 0;

vm_offset_t
hk_get_manual_kva(void)
{
	if (hk_manual_kva == 0) {
		extern vm_offset_t ml_io_map_wcomb(vm_offset_t, vm_size_t);
		/* 1080 * 2400 * 4 = 10368000 bytes (~10MB) */
		hk_manual_kva = ml_io_map_wcomb(0x5c000000UL, 10368000UL);
	}
	return hk_manual_kva;
}

int
hk_kva_fb_ready(void)
{
	vm_offset_t base = (hk_fb_kva_base >= 0xffff000000000000ULL) ? hk_fb_kva_base : hk_get_manual_kva();
	return (uint64_t)base >= 0xffff000000000000ULL;
}

void
hk_kva_fill(uint32_t color, uint32_t row, uint32_t count)
{
	vm_offset_t base = (hk_fb_kva_base >= 0xffff000000000000ULL) ? hk_fb_kva_base : hk_get_manual_kva();
	uint32_t h = (hk_fb_height != 0) ? hk_fb_height : 2400;
	uint32_t w = (hk_fb_width != 0) ? hk_fb_width : 1080;
	uint32_t rb = (hk_fb_rowbytes >= w * 4) ? hk_fb_rowbytes : 4320;

	if ((uint64_t)base < 0xffff000000000000ULL || row >= h) {
		return;
	}
	if (count > h - row) {
		count = h - row;
	}
	for (uint32_t y = row; y < row + count; y++) {
		volatile uint32_t *p = (volatile uint32_t *)(base + (vm_offset_t)y * rb);
		for (uint32_t x = 0; x < w; x++) {
			p[x] = color;
		}
	}
	__asm__ volatile ("dsb sy" ::: "memory");
}

/* 8x16 iso_font scaled 2x (16x32 cells), opaque white on opaque black. iso_font is LSB = leftmost pixel. */
void
hk_kva_text(const char *s, uint32_t len, uint32_t row)
{
	vm_offset_t base = (hk_fb_kva_base >= 0xffff000000000000ULL) ? hk_fb_kva_base : hk_get_manual_kva();
	uint32_t h = (hk_fb_height != 0) ? hk_fb_height : 2400;
	uint32_t w = (hk_fb_width != 0) ? hk_fb_width : 1080;
	uint32_t rb = (hk_fb_rowbytes >= w * 4) ? hk_fb_rowbytes : 4320;
	const uint32_t fg = 0xFFFFFFFFu, bg = 0xFF000000u;
	uint32_t x0 = 20, y0 = row;

	if ((uint64_t)base < 0xffff000000000000ULL) {
		return;
	}
	for (uint32_t i = 0; i < len && s[i]; i++) {
		unsigned char ch = (unsigned char)s[i];
		if (ch == '\r') {
			continue;
		}
		if (ch == '\n' || x0 + 16 > w) {
			x0 = 20;
			y0 += 32;
			if (ch == '\n') {
				continue;
			}
		}
		if (y0 + 32 > h) {
			break;
		}
		if (ch < 32 || ch > 126) {
			ch = '?';
		}
		const unsigned char *g = &iso_font[ch * 16];
		for (uint32_t gy = 0; gy < 16; gy++) {
			unsigned char bits = g[gy];
			for (uint32_t sy = 0; sy < 2; sy++) {
				volatile uint32_t *p = (volatile uint32_t *)(base +
				    (vm_offset_t)(y0 + gy * 2 + sy) * rb) + x0;
				for (uint32_t gx = 0; gx < 8; gx++) {
					uint32_t c = (bits & (1u << gx)) ? fg : bg;
					p[gx * 2] = c;
					p[gx * 2 + 1] = c;
				}
			}
		}
		x0 += 16;
	}
	__asm__ volatile ("dsb sy" ::: "memory");
}

/* Master diagnostic routine executed on first PID 1 syscall */
void
hk_run_pid1_diagnostics(void)
{
	extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);

	/* 1. CALIBRATION LADDER (Distinct Colors & Thicknesses) */
	hk_draw_band_direct(0x00FF0000, 100, 16);  /* Row 100: Red (16 rows) */
	hk_draw_band_direct(0x00FF8C00, 300, 24);  /* Row 300: Dark Orange (24 rows) */
	hk_draw_band_direct(0x00FFFF00, 500, 32);  /* Row 500: Yellow (32 rows) */
	hk_draw_band_direct(0x0000FF00, 700, 40);  /* Row 700: Green (40 rows) */
	hk_draw_band_direct(0x0000FFFF, 900, 48);  /* Row 900: Cyan (48 rows) */
	hk_draw_band_direct(0x000000FF, 1100, 56); /* Row 1100: Blue (56 rows) */
	hk_draw_band_direct(0x00FFFFFF, 1300, 64); /* Row 1300: White (64 rows) */
	hk_draw_band_direct(0x00FF1493, 2380, 20); /* Row 2380: Deep Pink panel bottom probe */

	/* 2. NIBBLE GAUGE: 16 colors for 4-bit nibbles */
	static const uint32_t nibble_palette[16] = {
		0x00000000, 0x000000FF, 0x0000FF00, 0x0000FFFF,
		0x00FF0000, 0x00FF00FF, 0x00FFFF00, 0x00FFFFFF,
		0x00808080, 0x00000080, 0x00008000, 0x00008080,
		0x00800000, 0x00800080, 0x00808000, 0x00FFA500
	};

	/* Gauge Block A: vinfo.v_baseaddr (16 nibbles, rows 1400..1527) */
	hk_draw_band_direct(0x00FF1493, 1390, 8); /* Pink guide header */
	uint64_t g_val = (uint64_t)vinfo.v_baseaddr;
	for (int ni = 15; ni >= 0; ni--) {
		uint32_t n_val = (uint32_t)((g_val >> (ni * 4)) & 0xF);
		hk_draw_band_direct(nibble_palette[n_val], 1400 + (15 - ni) * 8, 8);
	}

	/* Gauge Block B: vinfo.v_rowbytes (8 nibbles, rows 1540..1603) */
	hk_draw_band_direct(0x00FF1493, 1534, 4); /* Pink guide header */
	uint32_t g_rb = (uint32_t)vinfo.v_rowbytes;
	for (int ni = 7; ni >= 0; ni--) {
		uint32_t n_val = (g_rb >> (ni * 4)) & 0xF;
		hk_draw_band_direct(nibble_palette[n_val], 1540 + (7 - ni) * 8, 8);
	}

	/* Gauge Block C: State flags (rows 1610..1630) */
	/* kernel_map status: Lime if != NULL, Red if NULL */
	hk_draw_band_direct((kernel_map != VM_MAP_NULL) ? 0x0000FF00 : 0x00FF0000, 1610, 8);
	/* makeMapping state: Cyan if TRUE, Magenta if FALSE, Blue if never run */
	int ms = hk_fb_mapping_state();
	hk_draw_band_direct((ms > 0) ? 0x0000FFFF : (ms == 0 ? 0x00FF00FF : 0x000000FF), 1620, 8);

	/* 3. TEST ml_io_map_wcomb CALL DIRECTLY */
	vm_offset_t man_kva = hk_get_manual_kva();
	/* Row 1640: Lime if manual KVA >= 0xffff000000000000, Red if failed */
	hk_draw_band_direct(((uint64_t)man_kva >= 0xffff000000000000ULL) ? 0x0000FF00 : 0x00FF0000, 1640, 12);

	/* 4. ATTEMPT KVA WRITE (Orange test band on manual KVA) */
	if ((uint64_t)man_kva >= 0xffff000000000000ULL) {
		hk_kva_fill(0x00FFA500, 1660, 32); /* 32 rows Orange via KVA */
	}
	/* Row 1700: White via direct VA drawer to prove CPU survived KVA write! */
	hk_draw_band_direct(0x00FFFFFF, 1700, 16);
}
'''
            with open(target_vc, "w", encoding="utf-8", newline="\n") as f:
                f.write(cvc)
            print("  [OK] osfmk/console/video_console.c: hk_kva_fill/hk_kva_text installed")
    else:
        print("  [WARN] osfmk/console/video_console.c not found")


    # 11c. Instrument osfmk/arm64/sleh.c for userland exception & SVC monitoring (Rows 672, 676, 680)
    target_sleh = os.path.join(src_dir, "osfmk", "arm64", "sleh.c")
    if os.path.isfile(target_sleh):
        with open(target_sleh, "r", encoding="utf-8", errors="replace") as f:
            csleh = f.read().replace("\r\n", "\n")
        if "/* hakios: userland sleh exception monitor */" not in csleh:
            t_sleh = "bool is_user = PSR64_IS_USER(get_saved_state_cpsr(state));"
            r_sleh = (
                "bool is_user = PSR64_IS_USER(get_saved_state_cpsr(state));\n"
                "\t/* hakios: userland sleh exception monitor (runs BEFORE early trap dispatch, non-blocking) */\n"
                "\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);\n"
                "\tif (is_user) {\n"
                "\t\tif (class == ESR_EC_SVC_64) {\n"
                "\t\t\thk_draw_band_direct(0x0000FF00, 672, 8); /* Lime: user SVC_64 arrived */\n"
                "\t\t} else {\n"
                "\t\t\tstatic int hk_user_fault_count = 0;\n"
                "\t\t\tif (hk_user_fault_count++ < 8) {\n"
                "\t\t\t\thk_draw_band_direct(0x00FF1493, 676, 8); /* DeepPink: user exception (not SVC) */\n"
                "\t\t\t\t/* 1. Exception Class Diagnostic Band (Row 686, 8 rows thick) */\n"
                "\t\t\t\tif (class == 0x20 || class == 0x21) {\n"
                "\t\t\t\t\thk_draw_band_direct(0x0000FFFF, 686, 8); /* Cyan: IABORT (Demand page fault / Prefetch) */\n"
                "\t\t\t\t} else if (class == 0x24 || class == 0x25) {\n"
                "\t\t\t\t\thk_draw_band_direct(0x00FF0000, 686, 8); /* Vivid Red: DABORT (Data Abort) */\n"
                "\t\t\t\t} else if (class == 0x00) {\n"
                "\t\t\t\t\thk_draw_band_direct(0x00FFFF00, 686, 8); /* Yellow: UNDEF instruction / PAC trap */\n"
                "\t\t\t\t} else if (class == 0x26) {\n"
                "\t\t\t\t\thk_draw_band_direct(0x00FFFFFF, 686, 8); /* White: SP alignment fault */\n"
                "\t\t\t\t} else {\n"
                "\t\t\t\t\thk_draw_band_direct(0x00FFA500, 686, 8); /* Orange: other exception class */\n"
                "\t\t\t\t}\n"
                "\t\t\t\t/* 2. Fault Status Code (FSC = esr & 0x3F) at Row 696, 8 rows thick */\n"
                "\t\t\t\tuint32_t fsc = (uint32_t)(esr & 0x3F);\n"
                "\t\t\t\tif (fsc >= 4 && fsc <= 7) {\n"
                "\t\t\t\t\thk_draw_band_direct(0x00FFD700, 696, 8); /* Gold: Translation Fault (Demand Paging) */\n"
                "\t\t\t\t} else if (fsc >= 8 && fsc <= 11) {\n"
                "\t\t\t\t\thk_draw_band_direct(0x0000FF7F, 696, 8); /* SpringGreen: Access Flag Fault */\n"
                "\t\t\t\t} else if (fsc >= 12 && fsc <= 15) {\n"
                "\t\t\t\t\thk_draw_band_direct(0x00FF00FF, 696, 8); /* Magenta: Permission Fault (UXN/AP) */\n"
                "\t\t\t\t} else {\n"
                "\t\t\t\t\thk_draw_band_direct(0x008A2BE2, 696, 8); /* BlueViolet: other FSC */\n"
                "\t\t\t\t}\n"
                "\t\t\t}\n"
                "\t\t}\n"
                "\t}"
            )
            if t_sleh in csleh:
                csleh = csleh.replace(t_sleh, r_sleh, 1)
                print("  [OK] osfmk/arm64/sleh.c: userland exception/SVC monitor installed (rows 672, 676)")
            else:
                print("  [WARN] osfmk/arm64/sleh.c: t_sleh anchor not found")

            t_hsvc = "static void\nhandle_svc(arm_saved_state_t *state)\n{"
            if t_hsvc not in csleh:
                t_hsvc = "static void handle_svc(arm_saved_state_t *state)\n{"
            r_hsvc = (
                t_hsvc + "\n"
                "\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);\n"
                "\thk_draw_band_direct(0x00FFFF00, 706, 8); /* Yellow: handle_svc entered (row 706) */\n"
            )
            if t_hsvc in csleh:
                csleh = csleh.replace(t_hsvc, r_hsvc, 1)
                print("  [OK] osfmk/arm64/sleh.c: handle_svc entry band installed (row 706)")
            else:
                print("  [WARN] osfmk/arm64/sleh.c: handle_svc pattern not found")

            with open(target_sleh, "w", encoding="utf-8", newline="\n") as f:
                f.write(csleh)

    # 12. Instrument bsd/dev/arm/systemcalls.c for PID 1 userland syscall interception, banner display & heartbeat
    target_sys = os.path.join(src_dir, "bsd", "dev", "arm", "systemcalls.c")
    if os.path.isfile(target_sys):
        with open(target_sys, "r", encoding="utf-8", errors="replace") as f:
            csys = f.read().replace("\r\n", "\n")
        if "/* hakios: userland PID 1 text banner and syscall interception */" in csys:
            print("  [ALREADY PATCHED] bsd/dev/arm/systemcalls.c: userland syscall monitor already installed")
        else:
            t_syscall = "\terror = (*(callp->sy_call))(proc, &uthread->uu_arg[0], &(uthread->uu_rval[0]));"
            r_syscall = (
                "\t/* hakios: userland PID 1 diagnostic suite: calibration, bit-gauge & KVA render */\n"
                "\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);\n"
                "\thk_draw_band_direct(0x0000FFFF, 716, 8); /* Cyan: unix_syscall entered (row 716) */\n"
                "\tint hk_pid = proc_getpid(proc);\n"
                "\tif (hk_pid == 1) {\n"
                "\t\tstatic int pid1_boot_banner_drawn = 0;\n"
                "\t\tif (!pid1_boot_banner_drawn) {\n"
                "\t\t\tpid1_boot_banner_drawn = 1;\n"
                "\t\t\thk_draw_band_direct(0x00FFFFFF, 726, 12); /* Bright White: PID 1 in unix_syscall (row 726) */\n"
                "\t\t\thk_draw_band_direct(0x00FFFF00, 1720, 24); /* Yellow: PID 1 first syscall arrived! */\n"
                "\t\t\textern void hk_run_pid1_diagnostics(void);\n"
                "\t\t\thk_run_pid1_diagnostics();\n"
                "\t\t}\n\n"
                "\t\tif (code == 5 || (code & 0x00FFFFFF) == 5 /* SYS_open */) {\n"
                "\t\t\thk_draw_band_direct(0x00FFD700, 740, 8); /* Gold: SYS_open handled! (row 740) */\n"
                "\t\t\tuthread->uu_rval[0] = 1;\n"
                "\t\t\tuthread->uu_rval[1] = 0;\n"
                "\t\t\terror = 0;\n"
                "\t\t\tgoto hakios_pid1_handled;\n"
                "\t\t}\n\n"
                "\t\tif (code == 4 || (code & 0x00FFFFFF) == 4 /* SYS_write */) {\n"
                "\t\t\thk_draw_band_direct(0x0000FF7F, 750, 8); /* SpringGreen: SYS_write handled! (row 750) */\n"
                "\t\t\tstatic char hk_user_write_buf[256];\n"
                "\t\t\tstatic uint32_t hk_text_row = 1750;\n"
                "\t\t\tuser_addr_t ubuf = (user_addr_t)uthread->uu_arg[1];\n"
                "\t\t\tuser_size_t ulen = (user_size_t)uthread->uu_arg[2];\n"
                "\t\t\tuint32_t to_copy = (ulen < sizeof(hk_user_write_buf) - 1) ? (uint32_t)ulen : (sizeof(hk_user_write_buf) - 1);\n"
                "\t\t\tif (to_copy > 0 && copyin(ubuf, hk_user_write_buf, to_copy) == 0) {\n"
                "\t\t\t\thk_user_write_buf[to_copy] = '\\0';\n"
                "\t\t\t\textern void hk_kva_fill(uint32_t, uint32_t, uint32_t);\n"
                "\t\t\t\textern void hk_kva_text(const char *, uint32_t, uint32_t);\n"
                "\t\t\t\thk_draw_band_direct(0x00FFFFFF, 1730, 8);\n"
                "\t\t\t\thk_kva_fill(0xFF000000, hk_text_row, 32);\n"
                "\t\t\t\thk_kva_text(hk_user_write_buf, to_copy, hk_text_row);\n"
                "\t\t\t\thk_text_row += 32;\n"
                "\t\t\t\tif (hk_text_row > 2200) hk_text_row = 1750;\n"
                "\t\t\t}\n"
                "\t\t\tuthread->uu_rval[0] = (int)ulen;\n"
                "\t\t\tuthread->uu_rval[1] = 0;\n"
                "\t\t\terror = 0;\n"
                "\t\t\tgoto hakios_pid1_handled;\n"
                "\t\t}\n\n"
                "\t\tif (code == 20 || (code & 0x00FFFFFF) == 20 /* SYS_getpid */) {\n"
                "\t\t\thk_draw_band_direct(0x00FFA500, 760, 8); /* Orange: SYS_getpid handled! (row 760) */\n"
                "\t\t\tuthread->uu_rval[0] = 1;\n"
                "\t\t\tuthread->uu_rval[1] = 0;\n"
                "\t\t\terror = 0;\n"
                "\t\t\tgoto hakios_pid1_handled;\n"
                "\t\t}\n\n"
                "\t\tif (code == 93 || (code & 0x00FFFFFF) == 93 /* SYS_select */) {\n"
                "\t\t\thk_draw_band_direct(0x0098FF98, 770, 8); /* Mint: SYS_select heartbeat handled! (row 770) */\n"
                "\t\t\tstatic uint32_t hb_color = 0x0000FF00;\n"
                "\t\t\thk_draw_band_direct(hb_color, 1740, 8);\n"
                "\t\t\thb_color = (hb_color == 0x0000FF00) ? 0x0000FFFF : 0x0000FF00;\n"
                "\t\t}\n"
                "\t}\n\n"
                "\terror = (*(callp->sy_call))(proc, &uthread->uu_arg[0], &(uthread->uu_rval[0]));\nhakios_pid1_handled: ;"
            )
            if t_syscall in csys:
                csys = csys.replace(t_syscall, r_syscall, 1)
                with open(target_sys, "w", encoding="utf-8", newline="\n") as f:
                    f.write(csys)
                print("  [OK] bsd/dev/arm/systemcalls.c: userland syscall interception & text banner engine installed")
            else:
                t_syscall_alt = "error = (*(callp->sy_call))(proc, &uthread->uu_arg[0], &(uthread->uu_rval[0]));"
                if t_syscall_alt in csys:
                    csys = csys.replace(t_syscall_alt, r_syscall.lstrip("\t"), 1)
                    with open(target_sys, "w", encoding="utf-8", newline="\n") as f:
                        f.write(csys)
                    print("  [OK] bsd/dev/arm/systemcalls.c: userland syscall monitor installed via alt anchor")
                else:
                    print("  [WARN] bsd/dev/arm/systemcalls.c: error = (*(callp->sy_call)) pattern not found")

    return True

def main():
    if len(sys.argv) < 2:
        print("Usage: patch_bsd_init.py <xnu_src_dir>")
        sys.exit(1)

    src_dir = sys.argv[1]
    if not os.path.isdir(src_dir):
        print(f"[-] Error: directory does not exist: {src_dir}")
        sys.exit(1)

    print(f"=== Applying BSD Subsystem Lifecycle Ladder to {src_dir} ===")
    if not patch_bsd_init(src_dir):
        sys.exit(1)
    print("=== BSD lifecycle ladder instrumentation complete ===")

if __name__ == "__main__":
    main()
