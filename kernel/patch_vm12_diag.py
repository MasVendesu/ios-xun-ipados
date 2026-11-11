#!/usr/bin/env python3
"""
patch_vm12_diag.py <xnu_src_dir>  -- them chan doan vao EL1 dump (sleh.c), idempotent.
In them: PAR_EL1 sau 'AT S1E1R' cho FAR (cho biet that su fault o level nao),
         gPhysBase/gVirtBase, cac truong boot_args (physBase, memSize, topOfKernelData,
         deviceTreeP/Length, virtBase).
"""
import os, re, sys

def rd(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n")

def wr(p, c):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(c)

def patch_sleh_diag(src):
    p = os.path.join(src, "osfmk", "arm64", "sleh.c")
    if not os.path.isfile(p):
        print("  [WARN] sleh.c not found"); return
    c = rd(p)
    if "hk-diag12" in c:
        print("  [OK] already present"); return

    BLOCK = r'''
		/* hk-diag12 */
		{
			extern void hk_con_red_ptr(const char *tag, uint64_t val);
			extern vm_offset_t gPhysBase, gVirtBase;
			uint64_t hk_f, hk_par;
			__asm__ volatile ("mrs %0, far_el1" : "=r"(hk_f));
			__asm__ volatile ("at s1e1r, %0\n\tisb" :: "r"(hk_f));
			__asm__ volatile ("mrs %0, par_el1" : "=r"(hk_par));
			hk_con_red_ptr("PAR(FAR): ", hk_par);          /* bit0=1: fault, bit[6:1]=FST */
			hk_con_red_ptr("gPhysBase: ", (uint64_t)gPhysBase);
			hk_con_red_ptr("gVirtBase: ", (uint64_t)gVirtBase);
			boot_args *hk_ba = (boot_args *)PE_state.bootArgs;
			hk_con_red_ptr("bootArgs: ", (uint64_t)(uintptr_t)hk_ba);
			if ((uint64_t)(uintptr_t)hk_ba >= 0xfffffff000000000ULL) {
				hk_con_red_ptr("ba.virt: ", (uint64_t)hk_ba->virtBase);
				hk_con_red_ptr("ba.phys: ", (uint64_t)hk_ba->physBase);
				hk_con_red_ptr("ba.mem : ", (uint64_t)hk_ba->memSize);
				hk_con_red_ptr("ba.topK: ", (uint64_t)hk_ba->topOfKernelData);
				hk_con_red_ptr("ba.dtP : ", (uint64_t)(uintptr_t)hk_ba->deviceTreeP);
				hk_con_red_ptr("ba.dtLn: ", (uint64_t)hk_ba->deviceTreeLength);
			}
		}
'''
    pattern = re.compile(r'hk_con_red_ptr\("SP\* : ", \(uint64_t\)get_saved_state_sp\(([^)]+)\)\);\n')
    matches = list(pattern.finditer(c))
    if not matches:
        print("  [WARN] anchor 'SP* :' not found"); return

    for m in reversed(matches):
        c = c[:m.end()] + BLOCK + c[m.end():]
    inc = '#include <pexpert/pexpert.h>\n#include <arm/pmap.h>\n'
    if "pexpert/pexpert.h" not in c:
        k = c.find("#include")
        c = c[:k] + inc + c[k:]
    wr(p, c)
    print("  [OK] sleh.c: PAR/boot_args diag added")

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "."
    patch_sleh_diag(src)
