#!/usr/bin/env python3
"""
patch_vm14_ptwalk_diag.py <gic_v3_irq.c> <xnu_src_dir>      (idempotent)

1. arm_vm_init.c : in va_l1 / va_l1_end luc BAT DAU va KET THUC vong bootstrap L3 [vm] 8..9
                   -> so sanh voi virtual_space_start / gicd_base de biet vung io_map co nam trong vung co L3 khong.
2. gic_v3_irq.c  : them hk_ptwalk(): di bang trang TTBR1 bang tay (L1/L2/L3 descriptor) cho mot VA,
                   goi sau ml_io_map cho gicd_base va gicr_base (+ trang SGI frame).
   Neu L2 descriptor = 0  -> thieu bang L3 (bootstrap khong phu).
   Neu L2 hop le nhung L3 = 0 -> pmap ghi PTE sai cho/khac cay (quad-PTE / pmap_pte).
"""
import os, re, sys
gic = sys.argv[1]; src = sys.argv[2] if len(sys.argv) > 2 else "."

def rd(p): return open(p, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
def wr(p, c): open(p, "w", encoding="utf-8", newline="\n").write(c)

# ---------------- gic_v3_irq.c
WALK = r'''
/* hk-ptwalk: walk TTBR1 (4KB granule) by hand and print the descriptors for va */
static void
hk_ptwalk(const char *tag, uint64_t va)
{
	uint64_t ttbr1 = __builtin_arm_rsr64("TTBR1_EL1") & 0x0000fffffffff000ULL;
	uint64_t tcr = __builtin_arm_rsr64("TCR_EL1");
	unsigned t1sz = (unsigned)((tcr >> 16) & 0x3f);
	unsigned l1bits = (64 - t1sz) - 30;
	uint64_t *l1 = (uint64_t *)phystokv((pmap_paddr_t)ttbr1);
	uint64_t e1 = l1[(va >> 30) & ((1ULL << l1bits) - 1)];
	uint64_t e2 = 0, e3 = 0;

	if ((e1 & 3) == 3) {
		uint64_t *l2 = (uint64_t *)phystokv((pmap_paddr_t)(e1 & 0x0000fffffffff000ULL));
		e2 = l2[(va >> 21) & 0x1ff];
		if ((e2 & 3) == 3) {
			uint64_t *l3 = (uint64_t *)phystokv((pmap_paddr_t)(e2 & 0x0000fffffffff000ULL));
			e3 = l3[(va >> 12) & 0x1ff];
		}
	}
	kprintf("[walk] %s va=0x%lx TTBR1=0x%lx T1SZ=%u L1=0x%lx L2=0x%lx L3=0x%lx\n", tag,
	    (unsigned long)va, (unsigned long)ttbr1, t1sz, (unsigned long)e1, (unsigned long)e2, (unsigned long)e3);
}

'''
c = rd(gic)
if "hk-ptwalk" in c:
    print("  [OK] gic: already present")
else:
    m = re.search(r"\nstatic vm_offset_t\ngic_find_redistributor\(void\)", c)
    k = re.search(r"\t\tgicr_size = gicr_sz;\n", c)
    if not m or not k:
        print("  [WARN] gic: anchor khong thay (gic_find_redistributor / 'gicr_size = gicr_sz;')")
    else:
        call = ("\t\thk_ptwalk(\"GICD\", (uint64_t)gicd_base);\n"
                "\t\thk_ptwalk(\"GICR rd\", (uint64_t)gicr_base);\n"
                "\t\thk_ptwalk(\"GICR sgi\", (uint64_t)gicr_base + 0x10000);\n")
        c = c[:k.end()] + call + c[k.end():]
        c = c[:m.start()] + "\n" + WALK + c[m.start():]
        wr(gic, c); print("  [OK] gic: hk_ptwalk() + 3 loi goi sau ml_io_map")

# ---------------- arm_vm_init.c
p = os.path.join(src, "osfmk", "arm64", "arm_vm_init.c")
if os.path.isfile(p):
    a = rd(p)
    if "hk-vmrange" in a:
        print("  [OK] arm_vm_init: already present")
    else:
        t8 = 'hk_con_log("[vm] 8: bootstrap L3 loop starting..."); }'
        t9 = 'hk_con_log("[vm] 9: bootstrap L3 loop done"); }'
        P8 = ('\n\t{ extern void hk_con_print_ptr(const char *, uint64_t); /* hk-vmrange */\n'
              '\t  hk_con_print_ptr("[vm] 8: va_l1     = ", (uint64_t)va_l1);\n'
              '\t  hk_con_print_ptr("[vm] 8: va_l1_end = ", (uint64_t)va_l1_end); }')
        P9 = ('\n\t{ extern void hk_con_print_ptr(const char *, uint64_t);\n'
              '\t  hk_con_print_ptr("[vm] 9: va_l1 after = ", (uint64_t)va_l1); }')
        if t8 in a and t9 in a:
            a = a.replace(t8, t8 + P8, 1).replace(t9, t9 + P9, 1)
            wr(p, a); print("  [OK] arm_vm_init: in va_l1/va_l1_end quanh vong [vm] 8..9")
        else:
            print("  [WARN] arm_vm_init: marker [vm] 8/[vm] 9 khong khop")
else:
    print("  [WARN] khong thay arm_vm_init.c")
