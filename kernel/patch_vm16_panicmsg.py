#!/usr/bin/env python3
"""patch_vm16_panicmsg.py <xnu_src_dir>  (idempotent)
Print the panic() message on the framebuffer console at the entry of
panic_trap_to_debugger(). The EL1 dump alone only shows the 0xe7ffdeff panic-trap
opcode (decoded as UNDEF), hiding the real panic string.

The block is inserted right BEFORE the "For very early panics" comment, which is
robust against other patches that edit the head of the function."""
import os, sys
src = sys.argv[1] if len(sys.argv) > 1 else "."
p = os.path.join(src, "osfmk", "kern", "debug.c")
if not os.path.isfile(p):
    print("  [WARN] debug.c not found"); sys.exit(0)
c = open(p, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
if "hk-panicmsg" in c:
    print("  [OK] panicmsg already present"); SKIP_MSG = True
else:
    SKIP_MSG = False
anchor = "\t/* For very early panics before XNU serial initialization. */\n"
i = c.find(anchor)
if i < 0:
    print("  [WARN] panic_trap_to_debugger anchor not found"); sys.exit(0)
blk = ('\t/* hk-panicmsg: show the panic string on the console */\n'
       '\t{\n'
       '\t\textern void hk_con_log(const char *s);\n'
       '\t\tchar hk_pb[200];\n'
       '\t\thk_con_log("[PANIC] panic() called:");\n'
       '\t\tif (panic_format_str != NULL) {\n'
       '\t\t\tif (panic_args != NULL) {\n'
       '\t\t\t\tva_list hk_ap;\n'
       '\t\t\t\tva_copy(hk_ap, *panic_args);\n'
       '\t\t\t\tvsnprintf(hk_pb, sizeof(hk_pb), panic_format_str, hk_ap);\n'
       '\t\t\t\tva_end(hk_ap);\n'
       '\t\t\t\thk_pb[sizeof(hk_pb) - 1] = 0;\n'
       '\t\t\t\thk_con_log(hk_pb);\n'
       '\t\t\t} else {\n'
       '\t\t\t\thk_con_log(panic_format_str);\n'
       '\t\t\t}\n'
       '\t\t}\n'
       '\t}\n\n')
if not SKIP_MSG:
    c = c[:i] + blk + c[i:]
    open(p, "w", encoding="utf-8", newline="\n").write(c)
    print("  [OK] debug.c: panic message print added")

# ---- backtrace (frame-pointer walk) so the panic caller chain is visible ----
c = open(p, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
if "hk-bt" in c:
    print("  [OK] backtrace already present"); sys.exit(0)
i = c.find(anchor)
if i < 0:
    print("  [WARN] backtrace anchor not found"); sys.exit(0)
bt = ('\t/* hk-bt: frame-pointer backtrace of the panic caller chain */\n'
      '\t{\n'
      '\t\textern void hk_con_print_ptr(const char *, uint64_t);\n'
      '\t\tuint64_t *hk_fp = (uint64_t *)__builtin_frame_address(0);\n'
      '\t\tfor (int hk_k = 0; hk_k < 12; hk_k++) {\n'
      '\t\t\tif (hk_fp == NULL || ((uint64_t)hk_fp & 7) || (uint64_t)hk_fp < 0xfffffff000000000ULL) {\n'
      '\t\t\t\tbreak;\n'
      '\t\t\t}\n'
      '\t\t\thk_con_print_ptr("[bt] lr: ", hk_fp[1]);\n'
      '\t\t\tif (hk_fp[0] <= (uint64_t)hk_fp || hk_fp[0] - (uint64_t)hk_fp > 0x4000) {\n'
      '\t\t\t\tbreak;\n'
      '\t\t\t}\n'
      '\t\t\thk_fp = (uint64_t *)hk_fp[0];\n'
      '\t\t}\n'
      '\t}\n\n')
c = c[:i] + bt + c[i:]
open(p, "w", encoding="utf-8", newline="\n").write(c)
print("  [OK] debug.c: panic backtrace added")
