#!/usr/bin/env python3
"""patch_vm15_insn.py <xnu_src_dir>  (idempotent)
Print the 32-bit instruction word at the saved PC (ELR) in the EL1 exception dump,
so UNDEF (EC=0) crashes can be decoded (LSE / PAC / other) without the binary."""
import os, re, sys
src = sys.argv[1] if len(sys.argv) > 1 else "."
p = os.path.join(src, "osfmk", "arm64", "sleh.c")
if not os.path.isfile(p):
    print("  [WARN] sleh.c not found"); sys.exit(0)
c = open(p, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
if "hk-insn15" in c:
    print("  [OK] already present"); sys.exit(0)
pat = re.compile(r'hk_con_red_ptr\("SP\* : ", \(uint64_t\)get_saved_state_sp\(([^)]+)\)\);\n')
ms = list(pat.finditer(c))
if not ms:
    print("  [WARN] anchor 'SP* :' not found"); sys.exit(0)
for m in reversed(ms):
    s = m.group(1)
    blk = ('\t\t/* hk-insn15 */\n\t\t{\n'
           '\t\t\textern void hk_con_red_ptr(const char *tag, uint64_t val);\n'
           '\t\t\tuint64_t hk_pc = (uint64_t)get_saved_state_pc(%s), hk_p = 1;\n'
           '\t\t\t__asm__ volatile ("at s1e1r, %%0\\n\\tisb" :: "r"(hk_pc));\n'
           '\t\t\t__asm__ volatile ("mrs %%0, par_el1" : "=r"(hk_p));\n'
           '\t\t\tif ((hk_p & 1) == 0) {\n'
           '\t\t\t\thk_con_red_ptr("INSN@ELR: ", (uint64_t)*(volatile uint32_t *)hk_pc);\n'
           '\t\t\t} else {\n'
           '\t\t\t\thk_con_red_ptr("INSN@ELR unmapped PAR: ", hk_p);\n'
           '\t\t\t}\n\t\t}\n') % s
    c = c[:m.end()] + blk + c[m.end():]
open(p, "w", encoding="utf-8", newline="\n").write(c)
print("  [OK] sleh.c: INSN@ELR added")