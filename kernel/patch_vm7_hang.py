#!/usr/bin/env python3
"""patch_vm7_hang.py <xnu_src_dir>

Chay SAU patch_cortex_a73.py. Idempotent, co backup *.hk_orig.
  1. Stub ml_enable_monitor (call site + dinh nghia C/asm)
  2. Bien TLBI/DSB broadcast (is/ish) thanh local (nsh) - CHI cho bring-up cpus=1
  3. Chen "LED" bisect + in VBAR/TTBR0 quanh doan treo sau [vm] 6
  4. Telemetry cho vong L3 bootstrap ([vm] 8)
"""
import glob, os, re, shutil, sys


def rd(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n")


def wr(p, s):
    if not os.path.exists(p + ".hk_orig"):
        shutil.copy2(p, p + ".hk_orig")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


HELPER = r'''
/* hk-vm7-helper: LED o le phai (x=1062..1077) - text console khong bao gio de len */
extern void hk_con_log(const char *s);
extern void hk_con_print_ptr(const char *tag, uint64_t val);
static inline __attribute__((always_inline)) void hk_led(unsigned n, uint32_t color)
{
	volatile uint32_t *fb = (volatile uint32_t *)0xfffffff004000000ULL; /* KVA, khong phai PA 0x5c000000 */
	for (unsigned y = 0; y < 16; y++) {
		for (unsigned x = 0; x < 16; x++) {
			fb[(64u + n * 20u + y) * 1080u + 1062u + x] = color;
		}
	}
	__asm__ volatile ("dsb sy" ::: "memory");
}
'''


HELPER_NB = r"""
/* hk-vm7-nb: LED khong barrier - de LED khong tu 'hoan tat' hoac che lenh dang test */
static inline __attribute__((always_inline)) void hk_led_nb(unsigned n, uint32_t color)
{
	volatile uint32_t *fb = (volatile uint32_t *)0xfffffff004000000ULL;
	for (unsigned y = 0; y < 16; y++) {
		for (unsigned x = 0; x < 16; x++) {
			fb[(64u + n * 20u + y) * 1080u + 1062u + x] = color;
		}
	}
}
"""

FLUSH_NEW = r"""/* hakios: thay flush_mmu_tlb() bang chuoi ARMv8.0 local; LED 5..9 (cyan) tung buoc.
	 * MAC DINH BO tlbi (treo cung tren SM6225). -DHK_DO_TLBI de bat lai khi thu nghiem. */
	{
		uint64_t hk_tcr;
		__asm__ volatile ("mrs %0, TCR_EL1" : "=r" (hk_tcr));
		hk_tcr |= (1ULL << 7);                          /* EPD0 = 1: cam walker TTBR0 */
		__asm__ volatile ("msr TCR_EL1, %0" : : "r" (hk_tcr) : "memory");
		hk_led_nb(5, 0x0000FFFF);                       /* 2a: sau msr TCR_EL1 */
		__asm__ volatile ("isb sy" ::: "memory");
		hk_led_nb(6, 0x0000FFFF);                       /* 2b: sau isb */
#ifdef HK_DO_TLBI
		__asm__ volatile ("tlbi vmalle1" ::: "memory");
#endif
		hk_led_nb(7, 0x0000FFFF);                       /* 2c: sau tlbi vmalle1 (chua cho hoan tat) */
		__asm__ volatile ("dsb nsh" ::: "memory");
		hk_led_nb(8, 0x0000FFFF);                       /* 2d: sau dsb nsh = tlbi da hoan tat */
		__asm__ volatile ("isb sy" ::: "memory");
		hk_led_nb(9, 0x0000FFFF);                       /* 2e: sau isb */
	}
	hk_led(3, 0x00FFFF00);                                 /* LED3: qua flush */
"""

SEQ_NEW = r'''{
		uint64_t hk_vbar, hk_t0, hk_t1;
		__asm__ volatile ("mrs %0, VBAR_EL1" : "=r" (hk_vbar));
		__asm__ volatile ("mrs %0, TTBR0_EL1" : "=r" (hk_t0));
		__asm__ volatile ("mrs %0, TTBR1_EL1" : "=r" (hk_t1));
		hk_con_print_ptr("[vm]   VBAR   : ", hk_vbar);   /* PHAI la 0xfffffff0xxxxxxxx */
		hk_con_print_ptr("[vm]   TTBR0  : ", hk_t0);
		hk_con_print_ptr("[vm]   TTBR1  : ", hk_t1);
	}
	hk_led(0, 0x00FFFF00);                                 /* LED0: toi truoc monitor */
	/* hakios: ml_enable_monitor() bo - SM6225 khong co Apple Monitor/PPL */
	hk_led(1, 0x00FFFF00);                                 /* LED1: qua monitor */
	set_mmu_ttb(invalid_ttep & TTBR_BADDR_MASK);
	hk_led(2, 0x00FFFF00);                                 /* LED2: qua set_mmu_ttb (TTBR0 da chet) */
	@@FLUSH@@	kva_active = TRUE;
	hk_led(4, 0x0000FF00);                                 /* LED4 xanh: xong doan nay */
'''


def patch_arm_vm_init(src):
    p = os.path.join(src, "osfmk", "arm64", "arm_vm_init.c")
    if not os.path.isfile(p):
        print("  [WARN] arm_vm_init.c not found"); return
    c = rd(p)
    if "hk-vm7-helper" not in c:
        incs = list(re.finditer(r"^#include[^\n]*\n", c, re.M))
        pos = incs[-1].end() if incs else 0
        c = c[:pos] + HELPER + c[pos:]
        print("  [OK] arm_vm_init.c: hk_led helper")
    pat = re.compile(
        r"ml_enable_monitor\(\);\s*"
        r"set_mmu_ttb\(invalid_ttep & TTBR_BADDR_MASK\);\s*"
        r"flush_mmu_tlb\(\);\s*"
        r"kva_active = TRUE;\n")
    if "hk-vm7-nb" not in c:
        c = c.replace("/* hk-vm7-helper:", HELPER_NB + "/* hk-vm7-helper:", 1)
        print("  [OK] arm_vm_init.c: hk_led_nb helper")
    upg = re.compile(r"flush_mmu_tlb\(\);\s*hk_led\(3, 0x00FFFF00\);[^\n]*\n")
    if "hk_led_nb(5," in c:
        print("  [OK] arm_vm_init.c: micro-LED flush already present")
    elif "hk_led(0," in c and upg.search(c):
        c = upg.sub(lambda m: FLUSH_NEW, c, count=1)
        print("  [OK] arm_vm_init.c: flush_mmu_tlb() -> EPD0 + tlbi vmalle1 + micro-LED 5..9 (upgrade)")
    elif pat.search(c):
        c = pat.sub(lambda m: SEQ_NEW.replace('@@FLUSH@@', FLUSH_NEW), c, count=1)
        print("  [OK] arm_vm_init.c: monitor stubbed + LED0..4 bisect installed")
    else:
        print("  [WARN] khong khop chuoi ml_enable_monitor/set_mmu_ttb/flush/kva_active -"
              " dan tay: grep -n 'ml_enable_monitor' osfmk/arm64/arm_vm_init.c")

    # Vong L3 bootstrap: in KVA/PA cua L2 page cap theo yeu cau (toi da 4 dong)
    m = re.search(r"\[vm\] 8:", c)
    old = "*cpu_l1_tte = (kvtophys(l2_page) & ARM_TTE_TABLE_MASK)"
    if m and "hk_l2n" not in c:
        i = c.find(old, m.end())
        if i >= 0:
            tele = ("{ static int hk_l2n; if (hk_l2n++ < 4) {\n"
                    "\t\t\t\thk_con_print_ptr(\"[vm]   L2 KVA: \", (uint64_t)l2_page);\n"
                    "\t\t\t\thk_con_print_ptr(\"[vm]   L2 PA : \", (uint64_t)kvtophys(l2_page)); } }\n\t\t\t")
            c = c[:i] + tele + c[i:]
            print("  [OK] arm_vm_init.c: [vm] 8 L2 telemetry")
        else:
            print("  [WARN] L2 alloc statement not found after [vm] 8")
    # nang cap cay da patch ban truoc: doi #ifndef HK_SKIP_TLBI -> #ifdef HK_DO_TLBI
    if "#ifndef HK_SKIP_TLBI" in c:
        c = c.replace("#ifndef HK_SKIP_TLBI", "#ifdef HK_DO_TLBI")
        c = c.replace("-DHK_SKIP_TLBI de bo han lenh tlbi (kiem tra xem tlbi co phai thu pham).",
                      "MAC DINH BO tlbi. -DHK_DO_TLBI de bat lai.")
        print("  [OK] arm_vm_init.c: tlbi vmalle1 now OFF by default (HK_DO_TLBI to enable)")
    # LED 10/11 (magenta) quanh vong L3 bootstrap
    if "hk_led(10," not in c:
        a = 'hk_con_log("[vm] 8: bootstrap L3 loop starting..."); }'
        b = 'hk_con_log("[vm] 9: bootstrap L3 loop done"); }'
        if a in c:
            c = c.replace(a, a + " hk_led(10, 0x00FF00FF);", 1)
        if b in c:
            c = c.replace(b, b + " hk_led(11, 0x00FF00FF);", 1)
        print("  [OK] arm_vm_init.c: LED10 (loop entry) / LED11 (loop done)")
    wr(p, c)


def patch_monitor_defs(src):
    n = 0
    files = []
    for ext in ("c", "h", "s", "S"):
        files += glob.glob(os.path.join(src, "osfmk", "arm64", "*." + ext))
        files += glob.glob(os.path.join(src, "osfmk", "arm", "*." + ext))
    for p in files:
        c = rd(p)
        if "ml_enable_monitor" not in c or "hk: monitor stub" in c:
            continue
        new = re.sub(r"(\bml_enable_monitor\s*\(\s*void\s*\)\s*\{)",
                     r"\1\n\treturn; /* hk: monitor stub */", c)
        new = re.sub(r"(LEXT\(ml_enable_monitor\)[ \t]*\n)",
                     r"\1\tret /* hk: monitor stub */\n", new)
        if new != c:
            wr(p, new); n += 1
            print("  [OK] %s: ml_enable_monitor body -> return" % os.path.relpath(p, src))
    if n == 0:
        print("  [INFO] khong thay dinh nghia ml_enable_monitor (co the o #if XNU_MONITOR - khong build)."
              " Kiem tra: grep -rn ml_enable_monitor osfmk/")


def patch_local_barriers(src):
    """CHI cho cpus=1. Phai revert (dung .hk_orig) truoc khi bat SMP."""
    files = []
    for d, exts in (("osfmk/arm64", ("h", "c", "s", "S")), ("osfmk/arm/pmap", ("h", "c"))):
        for e in exts:
            files += glob.glob(os.path.join(src, d, "*." + e))
    for p in files:
        c = rd(p)
        n = c
        n = re.sub(r"(tlbi\s+[a-z0-9]+?)is\b", r"\1", n)              # vmalle1is -> vmalle1
        n = re.sub(r"\bDSB_ISH(ST)?\b", r"DSB_NSH\1", n)               # C
        n = re.sub(r"\bdsb(\s+)ish(st)?\b", r"dsb\1nsh\2", n)          # asm
        n = re.sub(r"\bdsb(\s+)sy /\* hakios: sy avoids interconnect deadlock \*/",
                   r"dsb\1nsh /* hakios: local */", n)
        if n != c:
            wr(p, n)
            print("  [OK] %s: TLBI/DSB -> local" % os.path.relpath(p, src))


def patch_no_tlbi_global(src):
    """HK_NO_TLBI=1: bien MOI lenh tlbi trong tlb.h / machine_routines_asm.s thanh nop.
    Chi de bring-up cpus=1: arm_vm_prot_finalize / pmap_bootstrap cung goi flush_mmu_tlb()."""
    for rel in ("osfmk/arm64/tlb.h", "osfmk/arm64/machine_routines_asm.s"):
        p = os.path.join(src, rel)
        if not os.path.isfile(p):
            continue
        c = rd(p)
        n = re.sub(r'"tlbi\s+[a-z0-9]+(?:\s*,\s*%[0-9a-z]+)?"', '"nop /* hk: tlbi off */"', c)
        n = re.sub(r'(?m)^([ \t]*)tlbi[ \t]+[a-z0-9]+(?:[ \t]*,[ \t]*\w+)?', r'\1nop /* hk: tlbi off */', n)
        if n != c:
            wr(p, n)
            print("  [OK] %s: tlbi -> nop" % rel)


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    src = sys.argv[1]
    patch_arm_vm_init(src)
    patch_monitor_defs(src)
    if not os.environ.get("HK_KEEP_BROADCAST"):
        patch_local_barriers(src)
    if os.environ.get("HK_NO_TLBI"):
        patch_no_tlbi_global(src)
    print("Done. Backup: *.hk_orig")


if __name__ == "__main__":
    main()
