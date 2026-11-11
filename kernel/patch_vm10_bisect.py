#!/usr/bin/env python3
"""
patch_vm10_bisect.py  --  chay SAU patch_cortex_a73.py va patch_vm7_hang.py
Usage: python3 patch_vm10_bisect.py <xnu_src_dir>

1. arm_vm_init.c : sau "[vm] 9" chen hk_con_log("[vm] 10.N <cau lenh>") sau MOI cau lenh
   cap than ham cho toi het arm_vm_init()  -> dong log cuoi cung chinh la lenh ke tiep gay crash.
2. sleh.c        : exception dump them x0..x30, 4 lenh tai PC, va backtrace theo frame pointer.
3. arm_init.c    : them marker sau arm_vm_init / truoc kernel_bootstrap.
Idempotent (chay lai nhieu lan khong sao).
"""
import os, re, sys

def rd(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n")

def wr(p, c):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(c)

def esc(s):
    s = re.sub(r"\s+", " ", s.strip())
    return s.replace("\\", "/").replace('"', "'").replace("%", "%%")[:48]

# ---------------------------------------------------------------- 1
def patch_vm_tail(src):
    p = os.path.join(src, "osfmk", "arm64", "arm_vm_init.c")
    if not os.path.isfile(p):
        print("  [WARN] arm_vm_init.c not found"); return
    c = rd(p)
    if "hk-vm10" in c:
        print("  [OK] arm_vm_init.c: vm10 bisect already present"); return
    i = c.find('hk_con_log("[vm] 9: bootstrap L3 loop done"); }')
    if i < 0:
        print("  [WARN] [vm] 9 marker not found - chay patch_cortex_a73.py truoc"); return
    # bat dau tu dong ke tiep sau marker (va sau hk_led(11..) neu co)
    j = c.find("\n", i) + 1
    out, k, n = [c[:j]], j, 0
    depth_b = depth_p = 0
    stmt = ""
    while k < len(c):
        e = c.find("\n", k)
        e = len(c) if e < 0 else e
        line = c[k:e]
        s = line.strip()
        out.append(line + "\n")
        k = e + 1
        if s.startswith("#"):
            continue
        # loai comment 1 dong / bo qua dong trong block comment
        code = re.sub(r"//.*$", "", line)
        code = re.sub(r'"(\\.|[^"\\])*"', '""', code)
        code = re.sub(r"/\*.*?\*/", "", code)
        if code.strip().startswith(("/*", "*")):
            continue
        for ch in code:
            if ch == "{": depth_b += 1
            elif ch == "}": depth_b -= 1
            elif ch == "(": depth_p += 1
            elif ch == ")": depth_p -= 1
        if depth_b < 0:           # gap '}' dong ham arm_vm_init -> dung
            break
        stmt += " " + code
        if depth_b == 0 and depth_p == 0 and code.rstrip().endswith((";", "}")):
            if s and not s.startswith(("return", "}")) and code.rstrip().endswith(";"):
                n += 1
                out.append('\t{ extern void hk_con_log(const char *s); '
                           'hk_con_log("[vm] 10.%d after: %s"); } /* hk-vm10 */\n' % (n, esc(stmt)))
            stmt = ""
    out.append(c[k:])
    # neu da break vi '}' thi out da chua dong '}' va c[k:] la phan con lai cua file
    wr(p, "".join(out))
    print("  [OK] arm_vm_init.c: %d marker [vm] 10.N chen sau [vm] 9" % n)

# ---------------------------------------------------------------- 2
DUMP = r'''
		/* hk-dumpregs: x0..x30 + 4 lenh tai PC + backtrace FP */
		{
			extern void hk_con_puts(const char *s);
			extern void hk_con_putc(char c);
			extern void hk_con_red_ptr(const char *tag, uint64_t val);
			static const char *const rn[] = {"x0 : ","x1 : ","x2 : ","x3 : ","x4 : ","x5 : ","x6 : ","x7 : ",
			    "x8 : ","x9 : ","x10: ","x11: ","x12: ","x13: ","x14: ","x15: ","x16: ","x17: ","x18: ","x19: ",
			    "x20: ","x21: ","x22: ","x23: ","x24: ","x25: ","x26: ","x27: ","x28: ","x29: "};
			for (unsigned r = 0; r < 30; r++) {
				hk_con_red_ptr(rn[r], (uint64_t)get_saved_state_reg(HK_STATE, r));
			}
			uint64_t hk_pc = (uint64_t)get_saved_state_pc(HK_STATE);
			if (hk_pc >= 0xfffffff000000000ULL && !(hk_pc & 3)) {
				volatile uint32_t *ip = (volatile uint32_t *)(hk_pc - 8);
				hk_con_red_ptr("I-8 : ", ip[0]);
				hk_con_red_ptr("I-4 : ", ip[1]);
				hk_con_red_ptr("I@PC: ", ip[2]);   /* ma may lenh gay fault */
				hk_con_red_ptr("I+4 : ", ip[3]);
			}
			uint64_t fp = (uint64_t)get_saved_state_reg(HK_STATE, 29);
			for (unsigned d = 0; d < 6; d++) {
				if (fp < 0xffffff8000000000ULL || (fp & 7)) break;
				hk_con_red_ptr("BT  : ", ((volatile uint64_t *)fp)[1]);
				fp = ((volatile uint64_t *)fp)[0];
			}
		}
'''
def patch_sleh(src):
    p = os.path.join(src, "osfmk", "arm64", "sleh.c")
    if not os.path.isfile(p):
        print("  [WARN] sleh.c not found"); return
    c = rd(p)
    if "hk-dumpregs" in c:
        print("  [OK] sleh.c: dump regs already present"); return
    pattern = re.compile(r'hk_con_red_ptr\("SP\* : ", \(uint64_t\)get_saved_state_sp\(([^)]+)\)\);\n')
    matches = list(pattern.finditer(c))
    if not matches:
        print("  [WARN] anchor 'SP* :' not found in sleh.c"); return
    for m in reversed(matches):
        st_var = m.group(1).strip()
        dump = DUMP.replace("HK_STATE", st_var)
        pos = m.end()
        c = c[:pos] + dump + c[pos:]
        print("  [OK] sleh.c: x0..x30 / I@PC / BT added to EL1 dump (state: %s)" % st_var)
    wr(p, c)

# ---------------------------------------------------------------- 3
def patch_arm_init(src):
    p = os.path.join(src, "osfmk", "arm", "arm_init.c")
    if not os.path.isfile(p):
        print("  [WARN] arm_init.c not found"); return
    c = rd(p)
    t = 'hk_con_log("[*] arm_vm_init OK"); }'
    if t not in c or "hk-after-vm" in c:
        print("  [OK/skip] arm_init.c"); return
    add = ('\n\t{ extern void hk_con_log(const char *s); hk_con_log("[*] arm_init: past arm_vm_init, '
           'next: low_glo/PE_consistent_debug/kprintf/kernel_bootstrap"); } /* hk-after-vm */')
    c = c.replace(t, t + add, 1)
    # truoc kernel_bootstrap()
    c = re.sub(r"(\n\t)(kernel_bootstrap\(\);)",
               r'\1{ extern void hk_con_log(const char *s); hk_con_log("[*] calling kernel_bootstrap"); }\1\2', c, count=1)
    wr(p, c)
    print("  [OK] arm_init.c: post-vm / pre-kernel_bootstrap markers")

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "."
    patch_vm_tail(src); patch_sleh(src); patch_arm_init(src)
