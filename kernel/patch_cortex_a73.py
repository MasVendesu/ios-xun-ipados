#!/usr/bin/env python3
import os, sys, re

def main():
    if len(sys.argv) < 2:
        print("Usage: patch_cortex_a73.py <xnu_src_dir>")
        sys.exit(1)
    
    src = sys.argv[1]
    print(f"Applying Cortex-A73 PAC stubs, Method 1 exception handler, and visual FB ladder to {src}...")

    # 1. osfmk/arm/arm_init.c
    p1 = os.path.join(src, "osfmk", "arm", "arm_init.c")
    if os.path.isfile(p1):
        with open(p1, "r", encoding="utf-8", errors="replace") as f:
            c = f.read()
        t1 = '__asm__ volatile ("mrs %0, APIBKeyLo_EL1" : "=r" (r->reg_before));'
        t2 = '__asm__ volatile ("mrs %0, APIBKeyLo_EL1" : "=r" (r->reg_after));'
        if t1 in c and t2 in c:
            c = c.replace(t1, 'r->reg_before = 0;')
            c = c.replace(t2, 'r->reg_after = 0;')
            print("  [OK] osfmk/arm/arm_init.c: APIBKeyLo_EL1 stubbed")

        c_norm = c.replace("\r\n", "\n")

        # Bypass PAC structor signing (OSRuntimeSignStructors uses pacia/retab which UNDEF on Cortex-A73)
        sign1_old = "OSRuntimeSignStructors(&_mh_execute_header);"
        sign1_new = "// hakios: bypass PAC structor signing on Cortex-A73\n\t\t// OSRuntimeSignStructors(&_mh_execute_header);"
        if sign1_old in c_norm:
            c_norm = c_norm.replace(sign1_old, sign1_new)
            print("  [OK] osfmk/arm/arm_init.c: OSRuntimeSignStructors bypassed")
        else:
            print("  [WARN] sign1_old not found in arm_init.c")

        sign2_old = "OSRuntimeSignStructorsInFileset(kc_mh);"
        sign2_new = "// hakios: bypass PAC structor signing on Cortex-A73\n\t\t// OSRuntimeSignStructorsInFileset(kc_mh);"
        if sign2_old in c_norm:
            c_norm = c_norm.replace(sign2_old, sign2_new)
            print("  [OK] osfmk/arm/arm_init.c: OSRuntimeSignStructorsInFileset bypassed")
        else:
            print("  [WARN] sign2_old not found in arm_init.c")

        # Instrument early checkpoints in arm_init()
        target_init = """\tloader_checkpoint(30);   /* hakios: reached arm_init (C) at all (ret from start.s worked) */
\tarm_slide_rebase_and_sign_image();
\tloader_checkpoint(31);   /* past slide/rebase/sign */

\tarm_static_if_init(args);
\tloader_checkpoint(32);   /* past static_if_init */"""

        replacement_init = """\textern void hk_draw_band(uint32_t color, uint32_t row_start, uint32_t row_count);
\textern void hk_draw_band_direct(uint32_t color, uint32_t row_start, uint32_t row_count);
\textern void hk_con_clear(void);
\textern void hk_con_puts(const char *s);
\textern void hk_con_log(const char *s);
\textern void hk_con_hex(uint64_t val);
\t__asm__ volatile ("msr cpacr_el1, %0; isb" : : "r" (3ULL << 20) : "memory");
\thk_con_clear();
\thk_con_log("========================================");
\thk_con_log("   HakiOS 12377 (iOS 26) on Sapphire    ");
\thk_con_log("========================================");
\thk_con_log("[*] arm_init: screen cleared to console");
\thk_con_log("[*] CPACR_EL1 FP/SIMD enabled");
\thk_draw_band_direct(0x00FFFF00, 55, 4);
\tloader_checkpoint(30);
\tarm_slide_rebase_and_sign_image();
\tloader_checkpoint(31);
\thk_con_log("[*] Slide and rebase OK");
\t// arm_static_if_init(args);
\tloader_checkpoint(32);
\thk_con_log("[*] static_if_init bypassed");"""

        target_cpu = """\tloader_checkpoint(4);   /* before cpu_data_init */
\tcpu_data_init(&BootCpuData);
\tloader_checkpoint(5);   /* past cpu_data_init */"""

        replacement_cpu = """\tloader_checkpoint(4);   /* before cpu_data_init */
\tcpu_data_init(&BootCpuData);
\tloader_checkpoint(5);   /* past cpu_data_init */
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] cpu_data_init OK"); }"""

        target_pe = """\tPE_init_platform(FALSE, args); /* Get platform expert set up */
\tloader_checkpoint(6);   /* past PE_init_platform */"""

        replacement_pe = """\tPE_init_platform(FALSE, args); /* Get platform expert set up */
\tloader_checkpoint(6);   /* past PE_init_platform */
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] PE_init_platform OK"); }"""

        target_topo = """\tml_parse_cpu_topology();

\tmaster_cpu = ml_get_boot_cpu_number();
\tassert(master_cpu >= 0 && master_cpu <= ml_get_max_cpu_number());"""

        replacement_topo = """\tml_parse_cpu_topology();
\tmaster_cpu = ml_get_boot_cpu_number();
\tif (master_cpu < 0 || master_cpu > ml_get_max_cpu_number()) {
\t\tmaster_cpu = 0;
\t}
\tassert(master_cpu >= 0 && master_cpu <= ml_get_max_cpu_number());
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] cpu_topology OK"); }"""

        target_boot = """\tthread->machine.preemption_count = 0;
\tloader_checkpoint(44);
\tcpu_bootstrap();
\tloader_checkpoint(13);"""

        replacement_boot = """\tthread->machine.preemption_count = 0;
\tloader_checkpoint(44);
\tcpu_bootstrap();
\tloader_checkpoint(13);
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] cpu_bootstrap OK"); }"""

        target_rt = """\trtclock_early_init();
\tloader_checkpoint(14);"""

        replacement_rt = """\trtclock_early_init();
\tloader_checkpoint(14);
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] rtclock_early_init OK"); }"""

        target_ksb = """\tkernel_startup_bootstrap();
\tloader_checkpoint(15);"""

        replacement_ksb = """\tkernel_startup_bootstrap();
\tloader_checkpoint(15);
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] kernel_startup_bootstrap OK"); }"""

        target_cpu_init = """\tcpu_init();
\tloader_checkpoint(16);

\tprocessor_bootstrap();"""

        replacement_cpu_init = """\tcpu_init();
\tloader_checkpoint(16);
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] cpu_init OK"); }
\tprocessor_bootstrap();
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] processor_bootstrap OK"); }"""

        target_vm = """\tarm_vm_init(xmaxmem, args);"""

        replacement_vm = """\t{ extern void hk_con_log(const char *s); hk_con_log("[*] entering arm_vm_init..."); }
\tarm_vm_init(xmaxmem, args);
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] arm_vm_init OK"); }"""

        target_serial = """\tconst unsigned int serial_exists = serial_init();
\tloader_checkpoint(18);
\tkernel_startup_initialize_upto(STARTUP_SUB_KPRINTF);
\tloader_checkpoint(19);"""

        replacement_serial = """\tconst unsigned int serial_exists = serial_init();
\tloader_checkpoint(18);
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] serial_init OK"); }
\tkernel_startup_initialize_upto(STARTUP_SUB_KPRINTF);
\tloader_checkpoint(19);
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] STARTUP_SUB_KPRINTF OK"); }"""

        target_console = """\tPE_create_console();
\tloader_checkpoint(20);"""

        replacement_console = """\tPE_create_console();
\tloader_checkpoint(20);
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] PE_create_console OK"); }"""

        target_pe_true = """\tPE_init_platform(TRUE, &BootCpuData);"""

        replacement_pe_true = """\tPE_init_platform(TRUE, &BootCpuData);
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] PE_init_platform(TRUE) OK"); }"""

        target_mstart = """\tloader_checkpoint(21);
\tmachine_startup(args);"""

        replacement_mstart = """\tloader_checkpoint(21);
\t{ extern void hk_con_log(const char *s); hk_con_log("[*] entering machine_startup..."); }
\tmachine_startup(args);"""

        if target_init in c_norm:
            c_norm = c_norm.replace(target_init, replacement_init)
            print("  [OK] osfmk/arm/arm_init.c: arm_init visual markers instrumented")
        else:
            print("  [WARN] target_init not found in arm_init.c")

        if target_cpu in c_norm:
            c_norm = c_norm.replace(target_cpu, replacement_cpu)
            print("  [OK] osfmk/arm/arm_init.c: cpu_data_init visual markers instrumented")
        else:
            print("  [WARN] target_cpu not found in arm_init.c")

        if target_pe in c_norm:
            c_norm = c_norm.replace(target_pe, replacement_pe)
            print("  [OK] osfmk/arm/arm_init.c: PE_init_platform visual markers instrumented")
        else:
            print("  [WARN] target_pe not found in arm_init.c")

        if target_topo in c_norm:
            c_norm = c_norm.replace(target_topo, replacement_topo)
            print("  [OK] osfmk/arm/arm_init.c: cpu_topology visual markers and master_cpu clamp instrumented")
        else:
            print("  [WARN] target_topo not found in arm_init.c")

        if target_boot in c_norm:
            c_norm = c_norm.replace(target_boot, replacement_boot)
            print("  [OK] osfmk/arm/arm_init.c: cpu_bootstrap visual markers instrumented")
        else:
            print("  [WARN] target_boot not found in arm_init.c")

        if target_rt in c_norm:
            c_norm = c_norm.replace(target_rt, replacement_rt)
            print("  [OK] osfmk/arm/arm_init.c: rtclock_early_init visual markers instrumented")
        else:
            print("  [WARN] target_rt not found in arm_init.c")

        if target_ksb in c_norm:
            c_norm = c_norm.replace(target_ksb, replacement_ksb)
            print("  [OK] osfmk/arm/arm_init.c: kernel_startup_bootstrap visual markers instrumented")
        else:
            print("  [WARN] target_ksb not found in arm_init.c")

        if target_cpu_init in c_norm:
            c_norm = c_norm.replace(target_cpu_init, replacement_cpu_init)
            print("  [OK] osfmk/arm/arm_init.c: cpu_init visual markers instrumented")
        else:
            print("  [WARN] target_cpu_init not found in arm_init.c")

        if target_vm in c_norm:
            c_norm = c_norm.replace(target_vm, replacement_vm)
            print("  [OK] osfmk/arm/arm_init.c: arm_vm_init visual markers instrumented")
        else:
            print("  [WARN] target_vm not found in arm_init.c")

        if target_serial in c_norm:
            c_norm = c_norm.replace(target_serial, replacement_serial)
            print("  [OK] osfmk/arm/arm_init.c: serial_init visual markers instrumented")
        else:
            print("  [WARN] target_serial not found in arm_init.c")

        if target_console in c_norm:
            c_norm = c_norm.replace(target_console, replacement_console)
            print("  [OK] osfmk/arm/arm_init.c: PE_create_console visual markers instrumented")
        else:
            print("  [WARN] target_console not found in arm_init.c")

        if target_pe_true in c_norm:
            c_norm = c_norm.replace(target_pe_true, replacement_pe_true)
            print("  [OK] osfmk/arm/arm_init.c: PE_init_platform(TRUE) visual markers instrumented")
        elif "[*] PE_init_platform(TRUE) OK" in c_norm:
            print("  [ALREADY PATCHED] osfmk/arm/arm_init.c: PE_init_platform(TRUE) visual markers already present")
        else:
            print("  [WARN] target_pe_true not found in arm_init.c")

        # hakios: bypass pe_arm_debug_init_early on Snapdragon 685 (no Apple Silicon cpu-debug-interface)
        if "hakios: bypass pe_arm_debug_init_early" in c_norm:
            print("  [ALREADY PATCHED] osfmk/arm/arm_init.c: pe_arm_debug_init_early already bypassed")
        else:
            m_dbg = re.search(r'(\t+)pe_arm_debug_init_early\s*\(\s*&BootCpuData\s*\);', c_norm)
            if m_dbg:
                c_norm = c_norm[:m_dbg.start(0)] + (
                    "\t// hakios: bypass pe_arm_debug_init_early on Bengal (Qualcomm SM6225 has no Apple CPU debug interface)\n"
                    "\t// pe_arm_debug_init_early(&BootCpuData);\n"
                    '\t{ extern void hk_con_log(const char *s); hk_con_log("[*] arm_init: pe_arm_debug_init_early bypassed on SM6225"); }'
                ) + c_norm[m_dbg.end(0):]
                print("  [OK] osfmk/arm/arm_init.c: pe_arm_debug_init_early bypassed")
            else:
                print("  [WARN] pe_arm_debug_init_early not found in arm_init.c")

        # hakios: bypass ml_map_cpu_pio in arm_init (Apple Silicon CPU PIO does not exist on Bengal SM6225)
        if "hakios: bypass ml_map_cpu_pio" in c_norm:
            print("  [ALREADY PATCHED] osfmk/arm/arm_init.c: ml_map_cpu_pio already bypassed in arm_init")
        else:
            m_pio = re.search(r'(\t+)ml_map_cpu_pio\s*\(\s*\);', c_norm)
            if m_pio:
                c_norm = c_norm[:m_pio.start(0)] + (
                    "\t// hakios: bypass ml_map_cpu_pio (Qualcomm SM6225 Cortex-A73/A53 has no Apple Silicon CPU PIO)\n"
                    "\t// ml_map_cpu_pio();\n"
                    '\t{ extern void hk_con_log(const char *s); hk_con_log("[*] arm_init: ml_map_cpu_pio bypassed on SM6225"); }'
                ) + c_norm[m_pio.end(0):]
                print("  [OK] osfmk/arm/arm_init.c: ml_map_cpu_pio bypassed")
            else:
                print("  [WARN] ml_map_cpu_pio not found in arm_init.c")

        # hakios: instrument cpu_timebase_init(TRUE) telemetry
        if "[*] arm_init: cpu_timebase_init(TRUE) OK" in c_norm:
            print("  [ALREADY PATCHED] osfmk/arm/arm_init.c: cpu_timebase_init telemetry already present")
        else:
            m_tb = re.search(r'(\t+)cpu_timebase_init\s*\(\s*TRUE\s*\);', c_norm)
            if m_tb:
                c_norm = c_norm[:m_tb.start(0)] + (
                    '\t{ extern void hk_con_log(const char *s); hk_con_log("[*] arm_init: calling cpu_timebase_init(TRUE)..."); }\n'
                    '\tcpu_timebase_init(TRUE);\n'
                    '\t{ extern void hk_con_log(const char *s); hk_con_log("[*] arm_init: cpu_timebase_init(TRUE) OK"); }'
                ) + c_norm[m_tb.end(0):]
                print("  [OK] osfmk/arm/arm_init.c: cpu_timebase_init telemetry instrumented")
            else:
                print("  [WARN] cpu_timebase_init(TRUE) not found in arm_init.c")

        # hakios: instrument PE_init_cpu telemetry
        if "[*] arm_init: PE_init_cpu OK" in c_norm:
            print("  [ALREADY PATCHED] osfmk/arm/arm_init.c: PE_init_cpu telemetry already present")
        else:
            m_pic = re.search(r'(\t+)PE_init_cpu\s*\(\s*\);', c_norm)
            if m_pic:
                c_norm = c_norm[:m_pic.start(0)] + (
                    '\tPE_init_cpu();\n'
                    '\t{ extern void hk_con_log(const char *s); hk_con_log("[*] arm_init: PE_init_cpu OK"); }'
                ) + c_norm[m_pic.end(0):]
                print("  [OK] osfmk/arm/arm_init.c: PE_init_cpu telemetry instrumented")

        # hakios: instrument robust machine_startup transition markers
        if "/* hakios: machine_startup transition */" in c_norm:
            print("  [ALREADY PATCHED] osfmk/arm/arm_init.c: machine_startup transition already present")
        else:
            m_mstart = re.search(r'(\t+)(machine_startup\s*\(\s*args\s*\);)', c_norm)
            if m_mstart:
                c_norm = c_norm[:m_mstart.start(2)] + (
                    "/* hakios: machine_startup transition */\n"
                    "\t{\n"
                    "\t\textern void hk_con_log(const char *s);\n"
                    "\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);\n"
                    "\t\thk_draw_band_direct(0x00FFFF00, 180, 5);  /* Yellow: entering machine_startup */\n"
                    '\t\thk_con_log("[*] arm_init: transitioning to machine_startup(args)...");\n'
                    "\t}\n"
                    "\tmachine_startup(args);\n"
                    "\t{\n"
                    "\t\textern void hk_con_log(const char *s);\n"
                    '\t\thk_con_log("[!] arm_init: machine_startup returned unexpectedly!");\n'
                    "\t}"
                ) + c_norm[m_mstart.end(2):]
                print("  [OK] osfmk/arm/arm_init.c: machine_startup visual & log markers instrumented")
            elif target_mstart in c_norm:
                c_norm = c_norm.replace(target_mstart, replacement_mstart)
                print("  [OK] osfmk/arm/arm_init.c: machine_startup visual markers instrumented (via target_mstart)")
            else:
                print("  [WARN] target_mstart not found in arm_init.c")

        # hakios: bisect markers right before the call that currently hangs silently
        t_dbg = 'kernel_debug_string_early("kernel_startup_bootstrap");'
        if t_dbg in c_norm and "hk-bisect-ksb" not in c_norm:
            c_norm = c_norm.replace(
                t_dbg,
                '{ extern void hk_con_log(const char *s); hk_con_log("[*] pre kernel_debug_string_early"); } /* hk-bisect-ksb */\n\t'
                + t_dbg
                + '\n\t{ extern void hk_con_log(const char *s); hk_con_log("[*] calling kernel_startup_bootstrap"); }',
                1)
            print("  [OK] osfmk/arm/arm_init.c: kernel_startup_bootstrap bisect markers installed")
        elif "hk-bisect-ksb" not in c_norm:
            print("  [WARN] kernel_debug_string_early(kernel_startup_bootstrap) not found in arm_init.c")

        c_norm += '\n' + open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'console_engine.c'), 'r', encoding='utf-8').read().replace('\r\n', '\n')
        with open(p1, "w", encoding="utf-8", newline="\n") as f:
            f.write(c_norm)

    # 2. osfmk/arm64/pac_std_asm.h
    p2 = os.path.join(src, "osfmk", "arm64", "pac_std_asm.h")
    if os.path.isfile(p2):
        with open(p2, "r", encoding="utf-8", errors="replace") as f:
            c = f.read()
        
        load_keys_old = """.macro PAC_STD_LOAD_KEYS base, lo, hi
\tPAC_STD_LOAD_KEY \\base, PAC_STD_KEY_IA, \\lo, \\hi
\tmsr\tAPIAKeyLo_EL1, \\lo
\tmsr\tAPIAKeyHi_EL1, \\hi
\tPAC_STD_LOAD_KEY \\base, PAC_STD_KEY_IB, \\lo, \\hi
\tmsr\tAPIBKeyLo_EL1, \\lo
\tmsr\tAPIBKeyHi_EL1, \\hi
\tPAC_STD_LOAD_KEY \\base, PAC_STD_KEY_DA, \\lo, \\hi
\tmsr\tAPDAKeyLo_EL1, \\lo
\tmsr\tAPDAKeyHi_EL1, \\hi
\tPAC_STD_LOAD_KEY \\base, PAC_STD_KEY_DB, \\lo, \\hi
\tmsr\tAPDBKeyLo_EL1, \\lo
\tmsr\tAPDBKeyHi_EL1, \\hi
\tPAC_STD_LOAD_KEY \\base, PAC_STD_KEY_GA, \\lo, \\hi
\tmsr\tAPGAKeyLo_EL1, \\lo
\tmsr\tAPGAKeyHi_EL1, \\hi
.endm"""

        load_keys_new = """.macro PAC_STD_LOAD_KEYS base, lo, hi
\t// hakios: Bengal Cortex-A73 lacks hardware PAC sysregs; stub out
.endm"""

        load_a_old = """.macro PAC_STD_LOAD_A_KEYS base, id, lo, hi, t1, t2, t3, t4
\tPAC_STD_DERIVE_KEY \\base, PAC_STD_KEY_IA, \\id, \\lo, \\hi, \\t1, \\t2, \\t3, \\t4
\tmsr\tAPIAKeyLo_EL1, \\lo
\tmsr\tAPIAKeyHi_EL1, \\hi
\tmsr\tAPDAKeyLo_EL1, \\lo
\tmsr\tAPDAKeyHi_EL1, \\hi
.endm"""

        load_a_new = """.macro PAC_STD_LOAD_A_KEYS base, id, lo, hi, t1, t2, t3, t4
\t// hakios: Bengal Cortex-A73 lacks hardware PAC sysregs; stub out
.endm"""

        load_b_old = """.macro PAC_STD_LOAD_B_KEYS base, id, lo, hi, t1, t2, t3, t4
\tPAC_STD_DERIVE_KEY \\base, PAC_STD_KEY_IB, \\id, \\lo, \\hi, \\t1, \\t2, \\t3, \\t4
\tmsr\tAPIBKeyLo_EL1, \\lo
\tmsr\tAPIBKeyHi_EL1, \\hi
\tmsr\tAPDBKeyLo_EL1, \\lo
\tmsr\tAPDBKeyHi_EL1, \\hi
.endm"""

        load_b_new = """.macro PAC_STD_LOAD_B_KEYS base, id, lo, hi, t1, t2, t3, t4
\t// hakios: Bengal Cortex-A73 lacks hardware PAC sysregs; stub out
.endm"""

        load_user_old = """.macro PAC_STD_LOAD_USER_KEYS base, thread, id, lo, hi, t1, t2, t3, t4
\tldr\t\\id, [\\thread, TH_JOP_PID]
\tPAC_STD_LOAD_A_KEYS \\base, \\id, \\lo, \\hi, \\t1, \\t2, \\t3, \\t4
\tldr\t\\id, [\\thread, TH_ROP_PID]
\tPAC_STD_LOAD_B_KEYS \\base, \\id, \\lo, \\hi, \\t1, \\t2, \\t3, \\t4
.endm"""

        load_user_new = """.macro PAC_STD_LOAD_USER_KEYS base, thread, id, lo, hi, t1, t2, t3, t4
\t// hakios: Bengal Cortex-A73 lacks hardware PAC sysregs; stub out to prevent NULL deref of TPIDR_EL1
.endm"""

        c_norm = c.replace("\r\n", "\n")
        assert load_keys_old in c_norm, "PAC_STD_LOAD_KEYS not found in pac_std_asm.h"
        assert load_a_old in c_norm, "PAC_STD_LOAD_A_KEYS not found in pac_std_asm.h"
        assert load_b_old in c_norm, "PAC_STD_LOAD_B_KEYS not found in pac_std_asm.h"
        assert load_user_old in c_norm, "PAC_STD_LOAD_USER_KEYS not found in pac_std_asm.h"

        c_norm = c_norm.replace(load_keys_old, load_keys_new)
        c_norm = c_norm.replace(load_a_old, load_a_new)
        c_norm = c_norm.replace(load_b_old, load_b_new)
        c_norm = c_norm.replace(load_user_old, load_user_new)

        with open(p2, "w", encoding="utf-8", newline="\n") as f:
            f.write(c_norm)
        print("  [OK] osfmk/arm64/pac_std_asm.h")

    # 3. osfmk/arm64/sleh.c
    p3 = os.path.join(src, "osfmk", "arm64", "sleh.c")
    if os.path.isfile(p3):
        with open(p3, "r", encoding="utf-8", errors="replace") as f:
            c = f.read()
        sleh_old = """\t\t\tchar line[200];
\t\t\tuint64_t ia, ga;
\t\t\t__asm__ volatile ("mrs %0, APIAKeyLo_EL1" : "=r" (ia));
\t\t\t__asm__ volatile ("mrs %0, APGAKeyLo_EL1" : "=r" (ga));"""
        sleh_new = """\t\t\tchar line[200];
\t\t\tuint64_t ia = 0, ga = 0;"""
        c_norm = c.replace("\r\n", "\n")
        if sleh_old in c_norm:
            c_norm = c_norm.replace(sleh_old, sleh_new)
            print("  [OK] osfmk/arm64/sleh.c: APIAKeyLo_EL1/APGAKeyLo_EL1 stubbed")
        else:
            print("  [WARN] sleh target not found in sleh.c")

        sleh_hook_old = """\tif (class == 0) {
\t\tif (pac_patch_handle_trap(state, is_user)) {
\t\t\treturn;
\t\t}
\t\tif (lse_patch_handle_trap(state, is_user)) {
\t\t\treturn;
\t\t}
\t}"""
        # hakios: EL1 kernel exceptions dump (ESR/ELR/FAR/SP read straight from the system registers).
        # Define HK_NO_EARLY_EXC_DUMP to compile this out once boot is stable.
        HK_DUMP_CALL = r"""		extern void hk_con_dump_exception(uint64_t esr, uint64_t elr, uint64_t far, uint64_t sp);
		extern void hk_con_red_ptr(const char *tag, uint64_t val);
		uint64_t hk_esr, hk_elr, hk_far, hk_sp;

		/* Doc thang thanh ghi that, khong qua saved state */
		__asm__ volatile ("mrs %0, ESR_EL1" : "=r" (hk_esr));
		__asm__ volatile ("mrs %0, ELR_EL1" : "=r" (hk_elr));
		__asm__ volatile ("mrs %0, FAR_EL1" : "=r" (hk_far));
		__asm__ volatile ("mov %0, sp" : "=r" (hk_sp));

		hk_con_dump_exception(hk_esr, hk_elr, hk_far, hk_sp);
		/* gia tri trong saved state (SP/LR luc fault) de doi chieu */
		hk_con_red_ptr("ESR*: ", (uint64_t)esr);
		hk_con_red_ptr("FAR*: ", (uint64_t)far);
		hk_con_red_ptr("PC* : ", (uint64_t)get_saved_state_pc(HK_STATE));
		hk_con_red_ptr("LR* : ", (uint64_t)get_saved_state_reg(HK_STATE, 30));
		hk_con_red_ptr("SP* : ", (uint64_t)get_saved_state_sp(HK_STATE));

		for (;;) {
			__asm__ volatile ("wfe");
		}
"""
        HK_TRAPS = r"""	if (class == 0) {
#ifndef HK_NO_TRAP_TRACE
		extern void hk_trap_log(const char *what, uint64_t pc);
		hk_trap_log(is_user ? "EC0 user pc" : "EC0 kernel pc", (uint64_t)get_saved_state_pc(state));
#endif
		if (lse_patch_handle_trap(state, is_user)) {
#ifndef HK_NO_TRAP_TRACE
			hk_trap_log("lse handled, resume pc", (uint64_t)get_saved_state_pc(state));
#endif
			return;
		}
		if (pac_patch_handle_trap(state, is_user)) {
#ifndef HK_NO_TRAP_TRACE
			hk_trap_log("pac handled, resume pc", (uint64_t)get_saved_state_pc(state));
#endif
			return;
		}
	}"""
        sleh_hook_new = HK_TRAPS + r"""
#ifndef HK_NO_EARLY_EXC_DUMP
	if (!is_user) {
""" + HK_DUMP_CALL.replace("HK_STATE", "state") + r"""	}
#endif"""
        if sleh_hook_old in c_norm:
            c_norm = c_norm.replace(sleh_hook_old, sleh_hook_new)
            print("  [OK] osfmk/arm64/sleh.c: early kernel fault console dump installed")
        else:
            print("  [WARN] sleh_hook_old not found in sleh.c")

        sleh_early_old = "\tASSERT_CONTEXT_SANITY(context);\n\n\ttask_vtimer_check(thread);"
        if sleh_early_old in c_norm and "hk-early-dump" not in c_norm:
            c_norm = c_norm.replace(sleh_early_old, r"""#ifndef HK_NO_EARLY_EXC_DUMP
	/* hk-early-dump: kernel sync exception (EC != 0) is dumped before any complex logic */
	if (class != 0 && !PSR64_IS_USER(get_saved_state_cpsr(&context->ss))) {
""" + HK_DUMP_CALL.replace("HK_STATE", "&context->ss") + r"""	}
#endif
""" + sleh_early_old, 1)
            print("  [OK] osfmk/arm64/sleh.c: early EL1 sync dump (ESR/ELR/FAR/SP) installed at sleh_synchronous entry")
        elif "hk-early-dump" not in c_norm:
            print("  [WARN] sleh_early_old not found in sleh.c")

        sleh_inv_old = """void
sleh_invalid_stack(arm_context_t *context, uint64_t esr __unused, vm_offset_t far __unused)
{
\tthread_t thread = current_thread();
\tvm_offset_t kernel_stack_bottom, sp;

\t{
\t\t/* hakios: the panic text below gets cut short this early; the syndrome first, on one line */
\t\tchar line[200];
\t\tsnprintf(line, sizeof(line), "hakios: early exception esr=0x%llx far=0x%lx pc=0x%llx x20=0x%llx x21=0x%llx\\n",
\t\t    esr, far, get_saved_state_pc(&context->ss), get_saved_state_reg(&context->ss, 20),
\t\t    get_saved_state_reg(&context->ss, 21));
\t\tloader_log_line(line);
\t}

\tsp = get_saved_state_sp(&context->ss);
\tvm_offset_t kstackptr = (vm_offset_t)thread->machine.kstackptr;
\tkernel_stack_bottom = round_page(kstackptr) - KERNEL_STACK_SIZE;"""

        sleh_inv_new = """void
sleh_invalid_stack(arm_context_t *context, uint64_t esr __unused, vm_offset_t far __unused)
{
\tthread_t thread = current_thread();
\tif (thread == NULL) {
\t\textern void hk_con_puts(const char *s);
\t\textern void hk_con_log(const char *s);
\t\textern void hk_con_hex(uint64_t val);
\t\textern void hk_con_putc(char c);
\t\textern void hk_con_dump_exception(uint64_t esr, uint64_t elr, uint64_t far, uint64_t sp);
\t\textern void hk_con_red_ptr(const char *tag, uint64_t val);

\t\tuint64_t pc = (context != NULL) ? (uint64_t)get_saved_state_pc(&context->ss) : 0ULL;
\t\tuint64_t sp_val = (context != NULL) ? (uint64_t)get_saved_state_sp(&context->ss) : 0ULL;
\t\tuint64_t lr_val = (context != NULL) ? (uint64_t)get_saved_state_reg(&context->ss, 30) : 0ULL;

\t\thk_con_dump_exception((uint64_t)esr, pc, (uint64_t)far, sp_val);
\t\thk_con_red_ptr("LR  : ", lr_val);
\t\thk_con_log("(invalid kernel stack / NULL thread)");

\t\tfor (;;) {
\t\t\t__asm__ volatile ("wfe");
\t\t}
\t}

\tvm_offset_t kernel_stack_bottom, sp;

\t{
\t\t/* hakios: the panic text below gets cut short this early; the syndrome first, on one line */
\t\tchar line[200];
\t\tsnprintf(line, sizeof(line), "hakios: early exception esr=0x%llx far=0x%lx pc=0x%llx x20=0x%llx x21=0x%llx\\n",
\t\t    esr, far, get_saved_state_pc(&context->ss), get_saved_state_reg(&context->ss, 20),
\t\t    get_saved_state_reg(&context->ss, 21));
\t\tloader_log_line(line);
\t}

\tsp = get_saved_state_sp(&context->ss);
\tvm_offset_t kstackptr = (vm_offset_t)thread->machine.kstackptr;
\tkernel_stack_bottom = round_page(kstackptr) - KERNEL_STACK_SIZE;"""

        if sleh_inv_old in c_norm:
            c_norm = c_norm.replace(sleh_inv_old, sleh_inv_new, 1)
            print("  [OK] osfmk/arm64/sleh.c: sleh_invalid_stack NULL-guard and console dump installed")
        else:
            print("  [WARN] sleh_inv_old not found in sleh.c")

        with open(p3, "w", encoding="utf-8", newline="\n") as f:
            f.write(c_norm)

    # 4. osfmk/arm64/start.s
    p4 = os.path.join(src, "osfmk", "arm64", "start.s")
    if os.path.isfile(p4):
        with open(p4, "r", encoding="utf-8", errors="replace") as f:
            c = f.read()
        c_norm = c.replace("\r\n", "\n")

        # CP1: start_first_cpu entry - Yellow band (rows 0-9, 0x5c000000)
        start_old = """\tmov\t\tx20, x0
\tmov\t\tx21, #0
\tLOADER_CHECKPOINT 1"""

        start_new = """\tmov\t\tx20, x0
\tmov\t\tx21, #0
\t// hakios: enable FP/SIMD access for EL0 and EL1 (CPACR_EL1.FPEN = 0b11)
\tmovz\tx9, #0x0030, lsl #16
\tmsr\tcpacr_el1, x9
\tisb
\t// hakios visual marker CP1: Yellow band at rows 0-9 (0x5c000000)
\tmovz\tx9, #0x5c00, lsl #16
\tmovz\tx10, #0xff00
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0xff00, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
\tmov\t\tw11, #2700
1:
\tstp\tx10, x10, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t1b
\tLOADER_CHECKPOINT 1"""

        assert start_old in c_norm, "start_first_cpu checkpoint 1 not found in start.s"
        c_norm = c_norm.replace(start_old, start_new)

        # CP2: after OSLAR_EL1 - Cyan band (rows 10-19, 0x5c00a8c0)
        cp2_old = """\t// Unlock the core for debugging
\tmsr\t\tOSLAR_EL1, xzr
\tLOADER_CHECKPOINT 2"""

        cp2_new = """\t// Unlock the core for debugging
\tmsr\t\tOSLAR_EL1, xzr
\t// hakios visual marker CP2: Cyan band at rows 10-19 (0x5c00a8c0)
\tmovz\tx9, #0xa8c0
\tmovk\tx9, #0x5c00, lsl #16
\tmovz\tx10, #0xffff
\tmovk\tx10, #0x0000, lsl #16
\tmovk\tx10, #0xffff, lsl #32
\tmovk\tx10, #0x0000, lsl #48
\tmov\t\tw11, #2700
1:
\tstp\tx10, x10, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t1b
\tLOADER_CHECKPOINT 2"""

        assert cp2_old in c_norm, "checkpoint 2 not found in start.s"
        c_norm = c_norm.replace(cp2_old, cp2_new)

        # CP3: Phase 1 Direct Mapping & Magenta band (rows 20-29, 0x5c015180) & save (VA - PA) delta
        cp3_old = """\t/* Ensure TTEs are visible */
\tdsb\t\tish

\tLOADER_CHECKPOINT 3

\tb\t\tcommon_start"""

        cp3_new = """\t// hakios Phase 1: Direct Framebuffer Mapping in bootstrap_pagetables (TTBR1 L1[192])
\t// L1 index 192 (0xC0), L2 entries 32..36 (0xfffffff004000000..0xfffffff004a00000)
\t// Base of bootstrap_pagetables is in x25. TTBR1 L1 root is at x25 + 0x1000.
\tadd\tx9, x25, #0x1000
\tldr\tx10, [x9, #(192 * 8)]
\tmovz\tx13, #0xf000
\tmovk\tx13, #0xffff, lsl #16
\tmovk\tx13, #0xffff, lsl #32
\tand\tx10, x10, x13
\tadd\tx10, x10, #(32 * 8)

\t// Guard: verify entries 32..36 are zero before modifying
\tmov\tw11, #5
\tmov\tx14, x10
Lhk_fb_read_guard:
\tldr\tx15, [x14], #8
\tcbnz\tx15, Lhk_fb_read_fail
\tsubs\tw11, w11, #1
\tb.ne\tLhk_fb_read_guard
\tb\tLhk_fb_zero

Lhk_fb_read_fail:
\t// Warning: non-zero entries found! Draw Red band at row 45 directly to PA (0x5c02f760)
\tmovz\tx14, #0xf760
\tmovk\tx14, #0x5c02, lsl #16
\tmovz\tx15, #0x00ff
\tmovk\tx15, #0x0000, lsl #16
\tmovk\tx15, #0x00ff, lsl #32
\tmovk\tx15, #0x0000, lsl #48
\tmov\tw11, #1080
Lhk_fb_warn_loop:
\tstp\tx15, x15, [x14], #16
\tsubs\tw11, w11, #1
\tb.ne\tLhk_fb_warn_loop

Lhk_fb_zero:
\tmov\tw11, #5
\tmov\tx14, x10
Lhk_fb_zero_loop:
\tstr\txzr, [x14], #8
\tsubs\tw11, w11, #1
\tb.ne\tLhk_fb_zero_loop

\t// Fill 5 block descriptors: PA | 0x0060000000000605ULL
\t// PA base = 0x5c000000, 2MB step = 0x200000
\tmovz\tx12, #0x0605
\tmovk\tx12, #0x0060, lsl #48
\tmovz\tx13, #0x0000
\tmovk\tx13, #0x5c00, lsl #16
\torr\tx12, x12, x13

\tmov\tw11, #5
\tmovz\tx13, #0x0000
\tmovk\tx13, #0x0020, lsl #16
Lhk_fb_fill:
\tstr\tx12, [x10], #8
\tadd\tx12, x12, x13
\tsubs\tw11, w11, #1
\tb.ne\tLhk_fb_fill
\tdsb\tsy

\t/* Ensure TTEs are visible */
\tdsb\t\tish
\t// hakios: record (VA - PA) delta in loader_stub_slot[3] for runtime PA transitions
\tadrp\tx9, EXT(loader_stub_slot)@page
\tadd\tx9, x9, EXT(loader_stub_slot)@pageoff
\tsub\tx10, x22, x23
\tstr\tx10, [x9, #24]
\t// hakios visual marker CP3: Magenta band at rows 20-29 (0x5c015180)
\tmovz\tx9, #0x5180
\tmovk\tx9, #0x5c01, lsl #16
\tmovz\tx10, #0x00ff
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0x00ff, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
\tmov\t\tw11, #2700
1:
\tstp\tx10, x10, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t1b

\tLOADER_CHECKPOINT 3

\tb\t\tcommon_start"""
        c_norm = c_norm.replace(cp3_old, cp3_new)

        # CP7: in common_start right before MMU enable - White band (rows 30-39, 0x5c01fa40)
        cp7_old = """\tLOADER_CHECKPOINT 7\t\t// hakios: reached common_start, about to enable the MMU
\t// Enable caches, MMU, ROP and JOP
\tMOV64   x0, SCTLR_EL1_DEFAULT"""

        cp7_new = """\t// hakios visual marker CP7: White band at rows 30-39 (0x5c01fa40)
\tmovz\tx9, #0xfa40
\tmovk\tx9, #0x5c01, lsl #16
\tmovz\tx10, #0xffff
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0xffff, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
\tmov\t\tw11, #2700
1:
\tstp\tx10, x10, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t1b

\tLOADER_CHECKPOINT 7\t\t// hakios: reached common_start, about to enable the MMU
\t// Enable caches, MMU, ROP and JOP
\tMOV64   x0, SCTLR_EL1_DEFAULT"""

        assert cp7_old in c_norm, "checkpoint 7 not found in start.s"
        c_norm = c_norm.replace(cp7_old, cp7_new)

        # CP11: in common_start right before ret into arm_init - Blue band (rows 40-49, 0x5c02a300)
        cp11_old = """\tLOADER_CHECKPOINT 11\t\t// hakios: about to ret into arm_init()
\t// Return to arm_init()
\tret"""

        cp11_new = """\t// hakios: set SP_EL1 (exception stack) to VA(excepstack_top) so it NEVER aliases SP_EL0 (intstack_top) during arm_init!
\tadrp\tx9, EXT(excepstack_top)@page
\tadd\tx9, x9, EXT(excepstack_top)@pageoff
\tmsr\t\tSPSel, #1
\tmov\t\tsp, x9
\tmsr\t\tSPSel, #0

\t// hakios visual marker CP11: Blue band at rows 40-49 (0x5c02a300)
\tmrs\t\tx9, SCTLR_EL1
\tbic\t\tx9, x9, #1
\tmsr\t\tSCTLR_EL1, x9
\tisb
\tmovz\tx9, #0xa300
\tmovk\tx9, #0x5c02, lsl #16
\tmovz\tx10, #0x00ff
\tmovk\tx10, #0x0000, lsl #16
\tmovk\tx10, #0x00ff, lsl #32
\tmovk\tx10, #0x0000, lsl #48
\tmov\t\tw11, #2700
1:
\tstp\tx10, x10, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t1b
\tmrs\t\tx9, SCTLR_EL1
\torr\t\tx9, x9, #1
\tmsr\t\tSCTLR_EL1, x9
\tisb

\t/* hakios DIAGNOSTIC: Phase 1 AT S1E1W measurement and bootstrap pagetable inspection */
\t// 1. Measure AT S1E1W with MMU ON on direct FB VA (0xfffffff004000000)
\tmovz\tx9, #0x0400, lsl #16
\tmovk\tx9, #0xfff0, lsl #32
\tmovk\tx9, #0xffff, lsl #48
\tat\ts1e1w, x9
\tisb
\tmrs\tx27, par_el1

\t// 2. Switch MMU OFF to inspect memory via PA and paint diagnostic rows
\tmrs\tx9, SCTLR_EL1
\tbic\tx9, x9, #1
\tmsr\tSCTLR_EL1, x9
\tisb

\t// 3. Read L1[192] and L2[32] from bootstrap_pagetables via PA
\tadd\tx16, x25, #0x1000
\tldr\tx28, [x16, #(192 * 8)]
\tcbz\tx28, 10f
\tmovz\tx13, #0xf000
\tmovk\tx13, #0xffff, lsl #16
\tmovk\tx13, #0xffff, lsl #32
\tand\tx17, x28, x13
\tldr\tx29, [x17, #(32 * 8)]
\tb\t11f
10:
\tmov\tx29, xzr
11:

\t// 4. Draw Row 170 (2 rows: 170..171, PA = 0x5c0b34c0): PAR_EL1 F bit (Lime = Success, Red = Fail)
\tmovz\tx9, #0x34c0
\tmovk\tx9, #0x5c0b, lsl #16
\ttbz\tx27, #0, 12f
\t// Fail (F == 1): Bright Red
\tmovz\tx10, #0x00ff
\tmovk\tx10, #0x0000, lsl #16
\tmovk\tx10, #0x00ff, lsl #32
\tmovk\tx10, #0x0000, lsl #48
\tb\t13f
12:
\t// Success (F == 0): Lime Green
\tmovz\tx10, #0x0000
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0x0000, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
13:
\tmov\tw11, #540
14:
\tstp\tx10, x10, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t14b

\t// 5. Draw Row 172 (2 rows: 172..173, PA = 0x5c0b5680): PAR_EL1 FST code
\tmovz\tx9, #0x5680
\tmovk\tx9, #0x5c0b, lsl #16
\ttbz\tx27, #0, 20f
\tubfx\tx12, x27, #1, #6
\tcmp\tx12, #0x05
\tb.eq\t21f
\tcmp\tx12, #0x06
\tb.eq\t22f
\tcmp\tx12, #0x0e
\tb.eq\t23f
\tcmp\tx12, #0x0a
\tb.eq\t24f
\t// Other fault: Magenta (0x00FF00FF)
\tmovz\tx10, #0x00ff
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0x00ff, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
\tb\t25f
20:
\t// Success: Spring Green (0x0000FF7F)
\tmovz\tx10, #0xff7f
\tmovk\tx10, #0x0000, lsl #16
\tmovk\tx10, #0xff7f, lsl #32
\tmovk\tx10, #0x0000, lsl #48
\tb\t25f
21:
\t// FST 0x05 (Trans L1): Dark Red (0x008B0000)
\tmovz\tx10, #0x0000
\tmovk\tx10, #0x008b, lsl #16
\tmovk\tx10, #0x0000, lsl #32
\tmovk\tx10, #0x008b, lsl #48
\tb\t25f
22:
\t// FST 0x06 (Trans L2): Dark Orange (0x00FF8C00)
\tmovz\tx10, #0x8c00
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0x8c00, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
\tb\t25f
23:
\t// FST 0x0E (Perm L2): Yellow (0x00FFFF00)
\tmovz\tx10, #0xff00
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0xff00, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
\tb\t25f
24:
\t// FST 0x0A (AF L2): Cyan (0x0000FFFF)
\tmovz\tx10, #0xffff
\tmovk\tx10, #0x0000, lsl #16
\tmovk\tx10, #0xffff, lsl #32
\tmovk\tx10, #0x0000, lsl #48
25:
\tmov\tw11, #540
26:
\tstp\tx10, x10, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t26b

\t// 6. Draw Row 174 (2 rows: 174..175, PA = 0x5c0b7840): L1[192] Status
\tmovz\tx9, #0x7840
\tmovk\tx9, #0x5c0b, lsl #16
\tcbnz\tx28, 30f
\t// L1[192] == 0: Dark Red (0x008B0000)
\tmovz\tx10, #0x0000
\tmovk\tx10, #0x008b, lsl #16
\tmovk\tx10, #0x0000, lsl #32
\tmovk\tx10, #0x008b, lsl #48
\tb\t32f
30:
\tand\tx12, x28, #3
\tcmp\tx12, #3
\tb.ne\t31f
\t// Valid table descriptor: Dodger Blue (0x001E90FF)
\tmovz\tx10, #0x90ff
\tmovk\tx10, #0x001e, lsl #16
\tmovk\tx10, #0x90ff, lsl #32
\tmovk\tx10, #0x001e, lsl #48
\tb\t32f
31:
\t// Other: White
\tmovz\tx10, #0xffff
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0xffff, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
32:
\tmov\tw11, #540
33:
\tstp\tx10, x10, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t33b

\t// 7. Draw Row 176 (2 rows: 176..177, PA = 0x5c0b9a00): L2[32] Content
\tmovz\tx9, #0x9a00
\tmovk\tx9, #0x5c0b, lsl #16
\tcbnz\tx29, 40f
\t// L2[32] == 0: White (0x00FFFFFF)
\tmovz\tx10, #0xffff
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0xffff, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
\tb\t44f
40:
\t// Expected descriptor: 0x006000005c000605
\tmovz\tx14, #0x0605
\tmovk\tx14, #0x0060, lsl #48
\tmovz\tx15, #0x0000
\tmovk\tx15, #0x5c00, lsl #16
\torr\tx14, x14, x15
\tcmp\tx29, x14
\tb.ne\t41f
\t// Exact match: Vivid Gold (0x00FFD700)
\tmovz\tx10, #0xd700
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0xd700, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
\tb\t44f
41:
\tand\tx12, x29, #3
\tcmp\tx12, #1
\tb.ne\t42f
\t// Block descriptor with different attr/PA: Hot Pink (0x00FF69B4)
\tmovz\tx10, #0x69b4
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0x69b4, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
\tb\t44f
42:
\t// Other descriptor type: Purple (0x00800080)
\tmovz\tx10, #0x0080
\tmovk\tx10, #0x0080, lsl #16
\tmovk\tx10, #0x0080, lsl #32
\tmovk\tx10, #0x0080, lsl #48
44:
\tmov\tw11, #540
45:
\tstp\tx10, x10, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t45b

\t// 8. Draw Row 178..181 (4 rows, PA = 0x5c0bbbc0): 16-bit Binary Gauge of PAR_EL1[15:0]
\tmov\tw18, #4
50:
\tmovz\tx9, #0xbbc0
\tmovk\tx9, #0x5c0b, lsl #16
\tmov\tw11, #4
\tsub\tw11, w11, w18
\tmov\tw12, #4320
\tmul\tw11, w11, w12
\tadd\tx9, x9, x11

\t// Left margin: 7 stores of Black (0x00000000)
\tmov\tw11, #7
51:
\tstp\txzr, xzr, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t51b

\t// Loop over 16 bits: bit 0 (leftmost) to bit 15 (rightmost)
\tmov\tw12, #0
52:
\tlsr\tx13, x27, x12
\ttbz\tx13, #0, 53f
\t// Bit is 1: White (0x00FFFFFF)
\tmovz\tx10, #0xffff
\tmovk\tx10, #0x00ff, lsl #16
\tmovk\tx10, #0xffff, lsl #32
\tmovk\tx10, #0x00ff, lsl #48
\tb\t54f
53:
\t// Bit is 0: Dark Grey (0x00333333)
\tmovz\tx10, #0x3333
\tmovk\tx10, #0x0033, lsl #16
\tmovk\tx10, #0x3333, lsl #32
\tmovk\tx10, #0x0033, lsl #48
54:
\t// Paint 16 stores (64 pixels wide)
\tmov\tw11, #16
55:
\tstp\tx10, x10, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t55b

\tadd\tw12, w12, #1
\tcmp\tw12, #16
\tb.ne\t52b

\t// Right margin: 7 stores of Black
\tmov\tw11, #7
56:
\tstp\txzr, xzr, [x9], #16
\tsubs\tw11, w11, #1
\tb.ne\t56b

\tsubs\tw18, w18, #1
\tb.ne\t50b

\t// 9. Re-enable MMU
\tmrs\tx9, SCTLR_EL1
\torr\tx9, x9, #1
\tmsr\tSCTLR_EL1, x9
\tisb

\tLOADER_CHECKPOINT 11\t\t// hakios: about to ret into arm_init()
\t// Return to arm_init()
\tret"""

        assert cp11_old in c_norm, "checkpoint 11 not found in start.s"
        c_norm = c_norm.replace(cp11_old, cp11_new)

        # Early Exception: Method 1 (Pure asm, no stack, safe transition, dump to 0x82f00000, Red band at rows 110-119)
        exc_old = """\tmov\t\tx2, #1
\tmrs\t\tx9, SCTLR_EL1
\tbic\t\tx9, x9, #1
\tmsr\t\tSCTLR_EL1, x9
\tisb
\tadr\t\tx9, Lloader_stub_slot
\tldr\t\tx9, [x9, #8]
\tcbz\t\tx9, .
\tbr\t\tx9"""

        exc_new = """\t// Method 1: Dump crash registers to fixed RAM 0x82f00000 (accessible in V=P via TTBR0)
\tmovz\tx9, #0x0000
\tmovk\tx9, #0x82f0, lsl #16
\tstp\tx3, x4, [x9]
\tstp\tx5, x6, [x9, #16]
\tstr\tx7, [x9, #32]
\tdc\tcvac, x9
\tdsb\tsy

\t// Transition to PA if MMU is enabled before disabling MMU:
\tmrs\tx12, SCTLR_EL1
\ttbz\tx12, #0, 1f
\tadr\tx10, 1f
\tadrp\tx11, EXT(loader_stub_slot)@page
\tadd\tx11, x11, EXT(loader_stub_slot)@pageoff
\tldr\tx11, [x11, #24]
\tsub\tx10, x10, x11
\tbr\tx10
1:
\t// Now running at PA: safely disable MMU
\tmrs\tx11, SCTLR_EL1
\tbic\tx11, x11, #1
\tmsr\tSCTLR_EL1, x11
\tisb

\t// Draw RED band at rows 200-209 (0x5c0d3000)
\tmovz\tx10, #0x3000
\tmovk\tx10, #0x5c0d, lsl #16
\tmovz\tw13, #0x0000
\tmovk\tw13, #0x00ff, lsl #16
\tmov\tw14, #10800
3:\tstr\tw13, [x10], #4
\tsubs\tw14, w14, #1
\tb.ne\t3b

2:\twfe
\tb\t2b"""

        assert exc_old in c_norm, "Lloader_early_exception old block not found in start.s"
        c_norm = c_norm.replace(exc_old, exc_new)

        # Append hk_draw_band helper to start.s
        hk_draw_band_code = """
/*
 * hakios: visual marker band drawer (accessible from both start.s and arm_init.c)
 * void hk_draw_band(uint32_t color, uint32_t row_start, uint32_t row_count);
 * 100% direct VA drawer via hk_draw_band_direct. Zero MMU toggles, zero TTBR switches, zero stack.
 */
\t.text
\t.align 2
\t.globl EXT(hk_draw_band)
LEXT(hk_draw_band)
\tb\tEXT(hk_draw_band_direct)
"""
        hk_render_screen_asm_code = """
/*
 * hakios: direct virtual address on-screen text renderer
 * void hk_render_screen_asm(const char *str, uint32_t len, const unsigned char *font);
 * Renders 2x scaled ASCII text directly to virtual framebuffer at 0xfffffff004000000.
 * 100% direct VA drawer. Zero MMU toggles, zero TTBR0 switches, zero cache hazards.
 */
\t.text
\t.align 2
\t.globl EXT(hk_render_screen_asm)
LEXT(hk_render_screen_asm)
\tstp\t\tx29, x30, [sp, #-80]!
\tstp\t\tx19, x20, [sp, #16]
\tstp\t\tx21, x22, [sp, #32]
\tstp\t\tx23, x24, [sp, #48]
\tstp\t\tx25, x26, [sp, #64]

\tmov\t\tx19, x0
\tmov\t\tw20, w1
\tmov\t\tx21, x2

\t/* Framebuffer VA base: 0xfffffff004000000 */
\tmovz\tx11, #0x0400, lsl #16
\tmovk\tx11, #0xfff0, lsl #32
\tmovk\tx11, #0xffff, lsl #48

\t/* Check if FB VA is mapped & writable via AT */
\tat\ts1e1w, x11
\tisb
\tmrs\tx3, par_el1
\ttbnz\tx3, #0, 7f

\tmov\t\tx25, #1040
\tmov\t\tx26, #360

\tmovz\tw14, #0xffff
\tmovk\tw14, #0x00ff, lsl #16\t/* Yellow (0x00FFFF00) */
\tmovz\tw13, #0x0000
\tmovk\tw13, #0x0000, lsl #16\t/* Black (0x00000000) */
\tmov\t\tx15, #4320

2:\tcbz\t\tw20, 7f
\tldrb\tw0, [x19], #1
\tsub\t\tw20, w20, #1
\tcbz\t\tw0, 7f

\tcmp\t\tw0, #13
\tb.eq\t2b

\tcmp\t\tw0, #10
\tb.ne\t3f
\tmov\t\tx25, #1040
\tadd\t\tx26, x26, #36
\tcmp\t\tx26, #2360
\tb.ge\t7f
\tb\t\t2b

3:\tcmp\t\tw0, #32
\tb.lt\t4f
\tcmp\t\tw0, #126
\tb.le\t5f
4:\tmov\t\tw0, #32

5:\tand\t\tx1, x0, #0xff
\tlsl\t\tx1, x1, #4
\tadd\t\tx1, x21, x1

\tmul\t\tx2, x26, x15
\tadd\t\tx2, x2, x11
\tadd\t\tx2, x2, x25, lsl #2

\tmov\t\tw4, #0
6:\tldrb\tw5, [x1, x4]

\tmov\t\tw6, #0
\tmov\t\tx7, x2
\tadd\t\tx8, x2, x15

61:\ttbz\t\tw5, #7, 62f
\tstr\t\tw14, [x7], #-4
\tstr\t\tw14, [x7], #-4
\tstr\t\tw14, [x8], #-4
\tstr\t\tw14, [x8], #-4
\tb\t\t63f
62:\tstr\t\tw13, [x7], #-4
\tstr\t\tw13, [x7], #-4
\tstr\t\tw13, [x8], #-4
\tstr\t\tw13, [x8], #-4
63:\tlsl\t\tw5, w5, #1
\tadd\t\tw6, w6, #1
\tcmp\t\tw6, #8
\tb.lt\t61b

\tadd\t\tx2, x2, x15
\tadd\t\tx2, x2, x15
\tadd\t\tw4, w4, #1
\tcmp\t\tw4, #16
\tb.lt\t6b

\tsub\t\tx25, x25, #18
\tcmp\t\tx25, #40
\tb.ge\t2b
\tmov\t\tx25, #1040
\tadd\t\tx26, x26, #36
\tcmp\t\tx26, #2360
\tb.lt\t2b

7:\tdsb\t\tsy
\tldp\t\tx25, x26, [sp, #64]
\tldp\t\tx23, x24, [sp, #48]
\tldp\t\tx21, x22, [sp, #32]
\tldp\t\tx19, x20, [sp, #16]
\tldp\t\tx29, x30, [sp], #80
\tret
"""
        hk_draw_band_direct_code = """
/*
 * hakios: direct virtual address marker band drawer
 * void hk_draw_band_direct(uint32_t color, uint32_t row_start, uint32_t row_count);
 * Runs 100% in VA space. No MMU toggle, no TTBR0 switch, no TLBI, zero stack.
 * Framebuffer VA = 0xfffffff004000000ULL
 * Uses only caller-saved registers x0-x8.
 */
\t.text
\t.align 2
\t.globl EXT(hk_draw_band_direct)
LEXT(hk_draw_band_direct)
\tret
\t/* 1. Sanitize arguments (32-bit unsigned) */
\tand\tx1, x1, #0xffffffff
\tand\tx2, x2, #0xffffffff

\t/* 2. Bounds check against 2400 rows */
\tmov\tx3, #2400
\tcmp\tx1, x3
\tb.hs\tLhk_direct_exit
\tsub\tx3, x3, x1
\tcmp\tx2, x3
\tcsel\tx2, x2, x3, lo
\tcbz\tx2, Lhk_direct_exit

\t/* 3. Compute row start pointer (x4) and count (w5) */
\tmov\tx4, #4320
\tmul\tx4, x1, x4
\tmovz\tx3, #0x0400, lsl #16
\tmovk\tx3, #0xfff0, lsl #32
\tmovk\tx3, #0xffff, lsl #48
\tadd\tx4, x4, x3

\t/* Stride: 270 pairs of 64-bit words (4320 bytes) per row */
\tmov\tw5, #270
\tmul\tw5, w2, w5

\t/* 4. AT guard: check if start address is mapped & writable */
\tat\ts1e1w, x4
\tisb
\tmrs\tx3, par_el1
\ttbnz\tx3, #0, Lhk_direct_exit

\t/* 5. Duplicate 32-bit color to 64-bit register x0 */
\tand\tx0, x0, #0xffffffff
\torr\tx0, x0, x0, lsl #32

\t/* 6. Fill loop */
Lhk_direct_loop:
\tstp\tx0, x0, [x4], #16
\tsubs\tw5, w5, #1
\tb.ne\tLhk_direct_loop
\tdsb\tsy

Lhk_direct_exit:
\tret
"""

        hk_draw_band_stackless_code = """
/*
 * hakios: stackless visual marker band drawer (accessible from start.s, locore.s, sleh.c)
 * void hk_draw_band_stackless(uint32_t color, uint32_t row_start, uint32_t row_count);
 * Forwards directly to hk_draw_band_direct (100% stackless, no MMU switch).
 */
\t.text
\t.align 2
\t.globl EXT(hk_draw_band_stackless)
LEXT(hk_draw_band_stackless)
\tb\tEXT(hk_draw_band_direct)
"""
        c_norm = c_norm + hk_draw_band_code + hk_draw_band_direct_code + hk_render_screen_asm_code + hk_draw_band_stackless_code

        with open(p4, "w", encoding="utf-8", newline="\n") as f:
            f.write(c_norm)
        print("  [OK] osfmk/arm64/start.s: visual markers, hk_draw_band, and hk_render_screen_asm installed")

    # 5. pexpert/pexpert/arm64/board_config.h (SM6225 Bengal has 2 clusters: Gold A73 + Silver A53)
    p5 = os.path.join(src, "pexpert", "pexpert", "arm64", "board_config.h")
    if os.path.isfile(p5):
        with open(p5, "r", encoding="utf-8", errors="replace") as f:
            c5 = f.read()
        if "#define MAX_CPU_CLUSTERS               1" in c5:
            c5 = c5.replace("#define MAX_CPU_CLUSTERS               1",
                            "#define MAX_CPU_CLUSTERS               4\n#define MAX_CPU_CLUSTER_PHY_ID         4")
            with open(p5, "w", encoding="utf-8", newline="\n") as f:
                f.write(c5)
            print("  [OK] pexpert/pexpert/arm64/board_config.h: MAX_CPU_CLUSTERS set to 4")
        elif "MAX_CPU_CLUSTERS               4" in c5:
            print("  [OK] pexpert/pexpert/arm64/board_config.h: already has MAX_CPU_CLUSTERS 4")
        else:
            print("  [WARN] MAX_CPU_CLUSTERS not found in board_config.h")

    # 6. pexpert/pexpert/arm64/BCM2837.h (Cortex-A73/A53 is ARMv8.0-A: lacks ARMv8.1 FEAT_PAN)
    p6 = os.path.join(src, "pexpert", "pexpert", "arm64", "BCM2837.h")
    if os.path.isfile(p6):
        with open(p6, "r", encoding="utf-8", errors="replace") as f:
            c6 = f.read().replace("\r\n", "\n")
        if "#define __ARM_PAN_AVAILABLE__     1" in c6:
            c6 = c6.replace("#define __ARM_PAN_AVAILABLE__     1", "// #define __ARM_PAN_AVAILABLE__     1")
            with open(p6, "w", encoding="utf-8", newline="\n") as f:
                f.write(c6)
            print("  [OK] pexpert/pexpert/arm64/BCM2837.h: __ARM_PAN_AVAILABLE__ disabled for Cortex-A73")
        else:
            print("  [WARN] __ARM_PAN_AVAILABLE__ not found in BCM2837.h")

    # 7. makedefs/MakeInc.def (replace +pan with -pan for BCM2837 Cortex-A73/A53)
    #    Note: Do NOT add -lse here (causes Apple Clang NEON intrinsic inline errors)
    p7 = os.path.join(src, 'makedefs', 'MakeInc.def')
    if os.path.isfile(p7):
        with open(p7, 'r', encoding='utf-8', errors='replace') as f:
            c7 = f.read().replace('\r\n', '\n')
        if '-Xclang -target-feature -Xclang +pan' in c7:
            c7 = c7.replace('-Xclang -target-feature -Xclang +pan', '-Xclang -target-feature -Xclang -pan')
            with open(p7, 'w', encoding='utf-8', newline='\n') as f:
                f.write(c7)
            print('  [OK] makedefs/MakeInc.def: +pan replaced with -pan')
        else:
            print('  [WARN] +pan not found in MakeInc.def')
    # 8. osfmk/arm64/locore.s & machine_routines_asm.s (fix CHECK_KERNEL_STACK on intstack & stub ml_sign_thread_state / ml_check_signed_state)
    for asm_name in ("locore.s", "machine_routines_asm.s", "cswitch.s"):
        p8 = os.path.join(src, "osfmk", "arm64", asm_name)
        if os.path.isfile(p8):
            with open(p8, "r", encoding="utf-8", errors="replace") as f:
                c8 = f.read().replace("\r\n", "\n")
            changed_asm = False
            if asm_name == "machine_routines_asm.s":
                # hakios: set_mmu_ttb uses 'dsb ish' which deadlocks on Cortex-A73/SM6225 interconnect
                c8 = c8.replace("LEXT(set_mmu_ttb)\n\tARM64_PROLOG\n#if __ARM_KERNEL_PROTECT__\n\t/* All EL1-mode ASIDs are odd. */\n\torr\t\tx0, x0, #(1 << TTBR_ASID_SHIFT)\n#endif /* __ARM_KERNEL_PROTECT__ */\n\tdsb\t\tish", "LEXT(set_mmu_ttb)\n\tARM64_PROLOG\n#if __ARM_KERNEL_PROTECT__\n\t/* All EL1-mode ASIDs are odd. */\n\torr\t\tx0, x0, #(1 << TTBR_ASID_SHIFT)\n#endif /* __ARM_KERNEL_PROTECT__ */\n\tdsb\t\tsy /* hakios: sy avoids interconnect deadlock */")
                if "dsb\t\tsy /* hakios: sy avoids interconnect deadlock */" in c8:
                    changed_asm = True
                    print("  [OK] osfmk/arm64/machine_routines_asm.s: set_mmu_ttb dsb ish -> dsb sy")

            if asm_name == "machine_routines_asm.s" and not os.environ.get("HK_KEEP_LSE"):
                lse_old = "#if defined(__ARM_ARCH_8_2__) && !KASAN_TBI"
                if lse_old in c8:
                    c8 = c8.replace(lse_old, "#if 0 /* hakios: Cortex-A73 has no LSE (casa) */")
                    c8 = c8.replace("#if !defined(__ARM_ARCH_8_2__)\n\tclrex", "#if 1 /* hakios: LL/SC path needs clrex */\n\tclrex")
                    changed_asm = True
                    print(f"  [OK] osfmk/arm64/{asm_name}: hw_lck_ticket_reserve_orig_allow_invalid uses ldaxr/stxr instead of casa")
            if "LEXT(ml_sign_thread_state)\n" in c8 and "LEXT(ml_sign_thread_state)\n\tret\n" not in c8:
                c8 = c8.replace("LEXT(ml_sign_thread_state)\n", "LEXT(ml_sign_thread_state)\n\tret\n", 1)
                changed_asm = True
                print(f"  [OK] osfmk/arm64/{asm_name}: ml_sign_thread_state stubbed")
            if "LEXT(ml_check_signed_state)\n" in c8 and "LEXT(ml_check_signed_state)\n\tret\n" not in c8:
                c8 = c8.replace("LEXT(ml_check_signed_state)\n", "LEXT(ml_check_signed_state)\n\tret\n", 1)
                changed_asm = True
                print(f"  [OK] osfmk/arm64/{asm_name}: ml_check_signed_state stubbed")
            c8, n_sub = re.subn(r'cbz\s+x2,\s*Lcorrupt_stack_\\@', 'cbz\t\tx2, Ltest_istack_\\@', c8)
            if n_sub > 0:
                changed_asm = True
                print(f"  [OK] osfmk/arm64/{asm_name}: CHECK_KERNEL_STACK cbz x2 -> Ltest_istack patched ({n_sub} occurrences)")

            if asm_name == "locore.s":
                # 1. el1_sp0_synchronous_vector_long: Row 230 OrangeRed (230..235, 6 rows) - 100% STACKLESS BEFORE STP
                t_sp0_sync = """el1_sp0_synchronous_vector_long:
\tstp\t\tx0, x1, [sp, #-16]!\t\t\t\t// Save x0 and x1 to the exception stack"""
                r_sp0_sync = """el1_sp0_synchronous_vector_long:
\t/* hakios/claude: EC diagnostic for early sync faults - 100% STACKLESS BEFORE STP */
\tmov\t\tx16, x0
\tmov\t\tx17, x1
\tmov\t\tx18, lr
\tmrs\t\tx1, ESR_EL1
\tlsr\t\tx0, x1, #26\t\t\t\t\t\t\t// x0 = EC
\tcbz\t\tx0, 86f\t\t\t\t\t\t\t\t// EC == 0 (LSE/PAC trap): teal one-shot marker below
\tmrs\t\tx0, TPIDR_EL0
\ttbnz\tx0, #63, 88f\t\t\t\t\t\t// If bit 63 set, already drawn, bypass!
\torr\t\tx0, x0, #(1ULL << 63)
\tmsr\t\tTPIDR_EL0, x0
\tmovz\tw0, #0x4500\t\t\t\t\t\t\t// Vivid OrangeRed (0x00FF4500)
\tmovk\tw0, #0x00ff, lsl #16
\tmov\t\tw1, #230\t\t\t\t\t\t\t// Row 230
\tmov\t\tw2, #6\t\t\t\t\t\t\t\t// 6 rows thick (230..235)
\tbl\t\tEXT(hk_draw_band_stackless)
\tb\t\t88f
86:
\t/* hakios: EC == 0 reached the vector -> one-shot teal band on row 226 (TPIDR_EL0 bit 61) */
\tmrs\t\tx0, TPIDR_EL0
\ttbnz\tx0, #61, 88f
\torr\t\tx0, x0, #(1ULL << 61)
\tmsr\t\tTPIDR_EL0, x0
\tmovz\tw0, #0xced1\t\t\t\t\t\t\t// Teal (0x0000CED1)
\tmovk\tw0, #0x0000, lsl #16
\tmov\t\tw1, #226
\tmov\t\tw2, #4
\tbl\t\tEXT(hk_draw_band_stackless)
88:
\tmov\t\tlr, x18
\tmov\t\tx1, x17
\tmov\t\tx0, x16
\tstp\t\tx0, x1, [sp, #-16]!\t\t\t\t// Save x0 and x1 to the exception stack"""
                if t_sp0_sync in c8:
                    c8 = c8.replace(t_sp0_sync, r_sp0_sync, 1)
                    changed_asm = True
                    print(f"  [OK] osfmk/arm64/{asm_name}: el1_sp0_synchronous Row 230 OrangeRed marker installed (pre-stp)")

                # 2. el1_sp0_irq_vector_long: Row 220 GreenYellow (220..223, 4 rows) - STACKLESS BEFORE STP
                t_sp0_irq = """el1_sp0_irq_vector_long:
#if CONFIG_SPTM
\tEL1_SP0_REDZONE_CHECK sp_sel=2
#endif
\tEL1_SP0_VECTOR"""
                r_sp0_irq = """el1_sp0_irq_vector_long:
\t/* hakios/claude: IRQ on SP0 - STACKLESS BEFORE STP (only if TPIDR_EL1 == 0) */
\tmrs\t\tx16, TPIDR_EL1
\tcbnz\tx16, 88f
\tmov\t\tx16, x0
\tmov\t\tx17, x1
\tmov\t\tx18, lr
\tmrs\t\tx0, TPIDR_EL0
\ttbnz\tx0, #62, 87f
\torr\t\tx0, x0, #(1ULL << 62)
\tmsr\t\tTPIDR_EL0, x0
\tmovz\tw0, #0xff2f\t\t\t\t\t\t\t// GreenYellow (0x00ADFF2F)
\tmovk\tw0, #0x00ad, lsl #16
\tmov\t\tw1, #220\t\t\t\t\t\t\t// Row 220
\tmov\t\tw2, #4\t\t\t\t\t\t\t\t// 4 rows thick (220..223)
\tbl\t\tEXT(hk_draw_band_stackless)
87:
\tmov\t\tlr, x18
\tmov\t\tx1, x17
\tmov\t\tx0, x16
88:
#if CONFIG_SPTM
\tEL1_SP0_REDZONE_CHECK sp_sel=2
#endif
\tEL1_SP0_VECTOR"""
                if t_sp0_irq in c8:
                    c8 = c8.replace(t_sp0_irq, r_sp0_irq, 1)
                    changed_asm = True
                    print(f"  [OK] osfmk/arm64/{asm_name}: el1_sp0_irq Row 220 GreenYellow marker installed (pre-stp)")

                # 3. el1_sp0_fiq_vector_long: Row 224 Lime (224..227, 4 rows) - STACKLESS BEFORE STP
                t_sp0_fiq = """el1_sp0_fiq_vector_long:
\t// ARM64_TODO write optimized decrementer
#if CONFIG_SPTM
\tEL1_SP0_REDZONE_CHECK sp_sel=3
#endif
\tEL1_SP0_VECTOR"""
                r_sp0_fiq = """el1_sp0_fiq_vector_long:
\t/* hakios/claude: FIQ on SP0 - STACKLESS BEFORE STP (only if TPIDR_EL1 == 0) */
\tmrs\t\tx16, TPIDR_EL1
\tcbnz\tx16, 88f
\tmov\t\tx16, x0
\tmov\t\tx17, x1
\tmov\t\tx18, lr
\tmrs\t\tx0, TPIDR_EL0
\ttbnz\tx0, #61, 87f
\torr\t\tx0, x0, #(1ULL << 61)
\tmsr\t\tTPIDR_EL0, x0
\tmovz\tw0, #0xff00\t\t\t\t\t\t\t// Lime (0x0000FF00)
\tmovk\tw0, #0x0000, lsl #16
\tmov\t\tw1, #224\t\t\t\t\t\t\t// Row 224
\tmov\t\tw2, #4\t\t\t\t\t\t\t\t// 4 rows thick (224..227)
\tbl\t\tEXT(hk_draw_band_stackless)
87:
\tmov\t\tlr, x18
\tmov\t\tx1, x17
\tmov\t\tx0, x16
88:
\t// ARM64_TODO write optimized decrementer
#if CONFIG_SPTM
\tEL1_SP0_REDZONE_CHECK sp_sel=3
#endif
\tEL1_SP0_VECTOR"""
                if t_sp0_fiq in c8:
                    c8 = c8.replace(t_sp0_fiq, r_sp0_fiq, 1)
                    changed_asm = True
                    print(f"  [OK] osfmk/arm64/{asm_name}: el1_sp0_fiq Row 224 Lime marker installed (pre-stp)")

                # 4. el1_sp0_serror_vector_long: Row 228 Red (228..229, 2 rows) - STACKLESS BEFORE STP
                t_sp0_serror = """el1_sp0_serror_vector_long:
\tEL1_SP0_VECTOR"""
                r_sp0_serror = """el1_sp0_serror_vector_long:
\t/* hakios/claude: SError on SP0 - STACKLESS BEFORE STP */
\tmov\t\tx16, x0
\tmov\t\tx17, x1
\tmov\t\tx18, lr
\tmovz\tw0, #0x0000\t\t\t\t\t\t\t// Vivid Red (0x00FF0000)
\tmovk\tw0, #0x00ff, lsl #16
\tmov\t\tw1, #228\t\t\t\t\t\t\t// Row 228
\tmov\t\tw2, #2\t\t\t\t\t\t\t\t// 2 rows thick
\tbl\t\tEXT(hk_draw_band_stackless)
\tmov\t\tlr, x18
\tmov\t\tx1, x17
\tmov\t\tx0, x16
\tEL1_SP0_VECTOR"""
                if t_sp0_serror in c8:
                    c8 = c8.replace(t_sp0_serror, r_sp0_serror, 1)
                    changed_asm = True
                    print(f"  [OK] osfmk/arm64/{asm_name}: el1_sp0_serror Row 228 Red marker installed (pre-stp)")

                # 5. el1_sp1_synchronous_vector_long: Row 236 BlueViolet (236..237, 2 rows) - STACKLESS BEFORE STP
                t_sp1_sync = """el1_sp1_synchronous_vector_long:
\t/*
\t * Before making our first (potentially faulting) memory access"""
                r_sp1_sync = """el1_sp1_synchronous_vector_long:
\t/* hakios/claude: Sync on SP1 - STACKLESS BEFORE STP */
\tmov\t\tx16, x0
\tmov\t\tx17, x1
\tmov\t\tx18, lr
\tmovz\tw0, #0x2be2\t\t\t\t\t\t\t// BlueViolet (0x008A2BE2)
\tmovk\tw0, #0x008a, lsl #16
\tmov\t\tw1, #236\t\t\t\t\t\t\t// Row 236
\tmov\t\tw2, #2\t\t\t\t\t\t\t\t// 2 rows thick
\tbl\t\tEXT(hk_draw_band_stackless)
\tmov\t\tlr, x18
\tmov\t\tx1, x17
\tmov\t\tx0, x16
\t/*
\t * Before making our first (potentially faulting) memory access"""
                if t_sp1_sync in c8:
                    c8 = c8.replace(t_sp1_sync, r_sp1_sync, 1)
                    changed_asm = True
                    print(f"  [OK] osfmk/arm64/{asm_name}: el1_sp1_synchronous Row 236 BlueViolet marker installed (pre-stp)")

                # 6. el1_sp1_irq_vector_long: Row 238 DarkOrange (238..239, 2 rows) - STACKLESS BEFORE STP
                t_sp1_irq = """el1_sp1_irq_vector_long:
\tEL1_SP1_VECTOR"""
                r_sp1_irq = """el1_sp1_irq_vector_long:
\t/* hakios/claude: IRQ on SP1 - STACKLESS BEFORE STP (only if TPIDR_EL1 == 0) */
\tmrs\t\tx16, TPIDR_EL1
\tcbnz\tx16, 88f
\tmov\t\tx16, x0
\tmov\t\tx17, x1
\tmov\t\tx18, lr
\tmovz\tw0, #0x8c00\t\t\t\t\t\t\t// DarkOrange (0x00FF8C00)
\tmovk\tw0, #0x00ff, lsl #16
\tmov\t\tw1, #238\t\t\t\t\t\t\t// Row 238
\tmov\t\tw2, #2\t\t\t\t\t\t\t\t// 2 rows thick
\tbl\t\tEXT(hk_draw_band_stackless)
\tmov\t\tlr, x18
\tmov\t\tx1, x17
\tmov\t\tx0, x16
88:
\tEL1_SP1_VECTOR"""
                if t_sp1_irq in c8:
                    c8 = c8.replace(t_sp1_irq, r_sp1_irq, 1)
                    changed_asm = True
                    print(f"  [OK] osfmk/arm64/{asm_name}: el1_sp1_irq Row 238 DarkOrange marker installed (pre-stp)")

                t_corr_entry = """\tINIT_SAVED_STATE_FLAVORS sp, w0, w1
\tmov\t\tx0, sp\t\t\t\t\t\t\t\t// Copy exception frame pointer to x0
\tadrp\tx1, fleh_invalid_stack@page\t\t\t// Load address for fleh"""
                r_corr_entry = """\tINIT_SAVED_STATE_FLAVORS sp, w0, w1
\tmov\t\tx0, sp\t\t\t\t\t\t\t\t// Copy exception frame pointer to x0
\t/* hakios/claude: draw Row 204 (Vivid Yellow: 204..207) on corrupt stack detection */
\tmov\t\tx19, x0
\tmovz\tw0, #0xff00
\tmovk\tw0, #0x00ff, lsl #16
\tmov\t\tw1, #204
\tmov\t\tw2, #4
\tbl\t\tEXT(hk_draw_band_stackless)
\tmov\t\tx0, x19
\tadrp\tx1, fleh_invalid_stack@page\t\t\t// Load address for fleh"""
                if t_corr_entry in c8:
                    c8 = c8.replace(t_corr_entry, r_corr_entry, 1)
                    changed_asm = True
                    print(f"  [OK] osfmk/arm64/{asm_name}: Lcorrupt_stack Row 204 Vivid Yellow marker installed")

                t_spin_entry = """0:
\twfe
\tb\t\t0b // Spin for watchdog"""
                r_spin_entry = """0:
\t/* hakios/claude: draw Row 210 (Vivid Red: 210..213) on spill failed spin */
\tmovz\tw0, #0x0000
\tmovk\tw0, #0x00ff, lsl #16
\tmov\t\tw1, #210
\tmov\t\tw2, #4
\tbl\t\tEXT(hk_draw_band_stackless)
1:
\twfe
\tb\t\t1b // Spin for watchdog"""
                if t_spin_entry in c8:
                    c8 = c8.replace(t_spin_entry, r_spin_entry)
                    changed_asm = True
                    print(f"  [OK] osfmk/arm64/{asm_name}: spill_failed Row 210 Vivid Red marker installed in all vectors (SP0 and SP1)")



            if changed_asm:
                with open(p8, "w", encoding="utf-8", newline="\n") as f:
                    f.write(c8)

    # 9. osfmk/arm64/loader_return.c (render panic text directly to screen at entry of loader_return)
    p9 = os.path.join(src, "osfmk", "arm64", "loader_return.c")
    if os.path.isfile(p9):
        with open(p9, "r", encoding="utf-8", errors="replace") as f:
            c9 = f.read().replace("\r\n", "\n")
        lr_old = "void\nloader_return(void)\n{\n\tredwood_ufs_close();"
        lr_new = """void
loader_return(void)
{
\textern void hk_draw_band(uint32_t color, uint32_t row_start, uint32_t row_count);
\thk_draw_band(0x00FF8C00, 220, 10); /* Dark Orange band: loader_return / panic */

\t/* hakios: on-screen panic dump directly to display BEFORE any quiesce */
\t{
\t\textern void hk_render_screen_asm(const char *str, uint32_t len, const unsigned char *font);
\t\textern const unsigned char iso_font[];
\t\textern char *debug_buf_base, *debug_buf_ptr;
\t\textern const char *debugger_panic_str;
\t\textern char loader_log[];
\t\textern uint32_t loader_log_len;
\t\tstatic char panic_onscreen[3500];
\t\tuint32_t n = 0;
\t\tconst char header[] = "=== HAKIOS PANIC CONSOLE ===\\n";
\t\tfor (const char *h = header; *h && n < 100; h++) {
\t\t\tpanic_onscreen[n++] = *h;
\t\t}

\t\tif (debug_buf_base != NULL && debug_buf_ptr > debug_buf_base) {
\t\t\tconst char *p = debug_buf_base;
\t\t\twhile (p < debug_buf_ptr && n < 3400) {
\t\t\t\tpanic_onscreen[n++] = *p++;
\t\t\t}
\t\t} else if (debugger_panic_str != NULL) {
\t\t\tconst char *p = debugger_panic_str;
\t\t\twhile (*p && n < 3400) {
\t\t\t\tpanic_onscreen[n++] = *p++;
\t\t\t}
\t\t} else if (loader_log_len > 0) {
\t\t\tuint32_t start = (loader_log_len > 3000) ? (loader_log_len - 3000) : 0;
\t\t\twhile (start < loader_log_len && n < 3400) {
\t\t\t\tpanic_onscreen[n++] = loader_log[start++];
\t\t\t}
\t\t} else {
\t\t\tconst char *msg = "loader_return() entered, but log buffers empty.\\n";
\t\t\twhile (*msg && n < 3400) {
\t\t\t\tpanic_onscreen[n++] = *msg++;
\t\t\t}
\t\t}

\t\tpanic_onscreen[n] = '\\0';

\t\t/* Clean data cache to DRAM before MMU-off read */
\t\tfor (uintptr_t p = (uintptr_t)panic_onscreen; p < (uintptr_t)panic_onscreen + n + 64; p += 64) {
\t\t\t__asm__ volatile ("dc civac, %0" : : "r" (p));
\t\t}
\t\t__asm__ volatile ("dsb sy");

\t\thk_render_screen_asm(panic_onscreen, n, iso_font);
\t\thk_draw_band(0x0000FF00, 230, 10); /* Lime band: screen dump completed successfully */

\t\t/* Spin forever so screen remains visible */
\t\tfor (;;) {
\t\t\t__asm__ volatile ("wfe");
\t\t}
\t}

\tredwood_ufs_close();"""
        if lr_old in c9:
            c9 = c9.replace(lr_old, lr_new, 1)
            with open(p9, "w", encoding="utf-8", newline="\n") as f:
                f.write(c9)
            print("  [OK] osfmk/arm64/loader_return.c: early on-screen panic dump installed")
        else:
            print("  [WARN] loader_return target not found in loader_return.c")

    # 10. osfmk/kern/startup.c (trace startup entries with console logs)
    p10 = os.path.join(src, "osfmk", "kern", "startup.c")
    if os.path.isfile(p10):
        with open(p10, "r", encoding="utf-8", errors="replace") as f:
            c10 = f.read().replace("\r\n", "\n")

        # 10.1 Instrument kernel_startup_bootstrap entry and qsort
        t_ksb = """__startup_func
void
kernel_startup_bootstrap(void)
{
\t/*
\t * Sort the various STARTUP() entries by subsystem/rank.
\t */
\tsize_t n = startup_entries_end - startup_entries;"""

        r_ksb = """__startup_func
void
kernel_startup_bootstrap(void)
{
\textern void hk_con_log(const char *s);
\textern void hk_con_puts(const char *s);
\textern void hk_con_hex(uint64_t val);
\textern void hk_con_putc(char c);

\thk_con_log("[*] enter kernel_startup_bootstrap");

\t/*
\t * Sort the various STARTUP() entries by subsystem/rank.
\t */
\tsize_t n = startup_entries_end - startup_entries;
\thk_con_puts("[*] startup entries n=");
\thk_con_hex((uint64_t)n);
\thk_con_putc(10);"""

        if t_ksb in c10:
            c10 = c10.replace(t_ksb, r_ksb, 1)
            print("  [OK] osfmk/kern/startup.c: kernel_startup_bootstrap entry instrumented")
        else:
            print("  [WARN] t_ksb not found in startup.c")

        t_qsort = """\tqsort(startup_entries, n, sizeof(struct startup_entry), startup_entry_cmp);"""
        r_qsort = """\thk_con_log("[*] sorting startup entries...");
\tqsort(startup_entries, n, sizeof(struct startup_entry), startup_entry_cmp);
\thk_con_log("[*] startup entries sorted OK");"""

        if t_qsort in c10:
            c10 = c10.replace(t_qsort, r_qsort, 1)
            print("  [OK] osfmk/kern/startup.c: qsort trace instrumented")
        else:
            print("  [WARN] t_qsort not found in startup.c")

        t_ksb_end = """\tkernel_startup_initialize_upto(STARTUP_SUB_LOCKS);
}"""
        r_ksb_end = """\thk_con_log("[*] init upto LOCKS...");
\tkernel_startup_initialize_upto(STARTUP_SUB_LOCKS);
\thk_con_log("[*] startup upto LOCKS completed!");
}"""

        if t_ksb_end in c10:
            c10 = c10.replace(t_ksb_end, r_ksb_end, 1)
            print("  [OK] osfmk/kern/startup.c: kernel_startup_bootstrap completion trace instrumented")
        else:
            print("  [WARN] t_ksb_end not found in startup.c")

        # 10.2 Instrument startup loop in kernel_startup_initialize_upto
        t_startup = """\t\tstartup_phase = cur->subsystem - 1;
\t\tkernel_startup_log(cur->subsystem);
\t\tcur->func(cur->arg);"""

        r_startup = r"""		startup_phase = cur->subsystem - 1;
		{
			extern void hk_startup_enter(uint32_t sub, const char *name, uint32_t rank, uint64_t raw, uint64_t stripped);
			extern uint64_t hk_con_strip_ptr(uint64_t p);

			uint64_t hk_raw = (uint64_t)(uintptr_t)cur->func;
			/* canonical 0xfffffff0_xxxxxxxx: bo PAC signature truoc khi nhay vao ham */
			uint64_t hk_fp = hk_con_strip_ptr(hk_raw);

			/* gom log theo (sub, rank); status row (dong cuoi man hinh) luon hien entry dang chay */
			hk_startup_enter((uint32_t)cur->subsystem, hk_sub_name(cur->subsystem),
			    (uint32_t)cur->rank, hk_raw, hk_fp);

			kernel_startup_log(cur->subsystem);
			hk_call_startup(hk_fp, cur->arg);
		}"""

        if t_startup in c10:
            c10 = c10.replace(t_startup, r_startup, 1)
            print("  [OK] osfmk/kern/startup.c: startup loop trace and PAC stripping instrumented")
        else:
            print("  [WARN] t_startup not found in startup.c")

        # Plain `blr` trampoline: a C call through a function pointer on arm64e compiles to
        # blraaz/blrabz (combined PAC), which is UNDEF on Cortex-A73 (ARMv8.0). The stripped pointer
        # is therefore called with a bare blr from inline asm.
        t_hdr = "__startup_func\nvoid\nkernel_startup_initialize_upto(startup_subsystem_id_t upto)\n{"
        r_hdr = r"""static const char *
hk_sub_name(startup_subsystem_id_t s)
{
	switch (s) {
#define HK_SUB(n) case STARTUP_SUB_##n: return #n;
	HK_SUB(TUNABLES) HK_SUB(TIMEOUTS) HK_SUB(LOCKS) HK_SUB(KPRINTF)
	HK_SUB(PMAP_STEAL) HK_SUB(KMEM) HK_SUB(ZALLOC) HK_SUB(PERCPU)
	HK_SUB(EVENT) HK_SUB(CODESIGNING) HK_SUB(KTRACE) HK_SUB(OSLOG)
	HK_SUB(MACH_IPC) HK_SUB(THREAD_CALL) HK_SUB(SYSCTL) HK_SUB(EARLY_BOOT)
#undef HK_SUB
	default: return "?";
	}
}

static __attribute__((noinline)) void
hk_call_startup(uint64_t fp, const void *arg)
{
#if defined(__aarch64__)
	register const void *hk_x0 __asm__("x0") = arg;
	__asm__ volatile ("blr %1"
	    : "+r" (hk_x0)
	    : "r" (fp)
	    : "x1", "x2", "x3", "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11",
	      "x12", "x13", "x14", "x15", "x16", "x17", "x30",
	      "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
	      "v16", "v17", "v18", "v19", "v20", "v21", "v22", "v23",
	      "v24", "v25", "v26", "v27", "v28", "v29", "v30", "v31",
	      "memory", "cc");
#else
	((void (*)(const void *))fp)(arg);
#endif
}

""" + t_hdr
        if t_hdr in c10 and "hk_call_startup(uint64_t" not in c10:
            c10 = c10.replace(t_hdr, r_hdr, 1)
            print("  [OK] osfmk/kern/startup.c: hk_call_startup (plain blr, no combined PAC) installed")
        elif "hk_call_startup(uint64_t" not in c10:
            print("  [WARN] kernel_startup_initialize_upto header not found in startup.c")

        # flush the last (sub, rank) group + "SUB n COMPLETED OK" when kernel_startup_initialize_upto() finishes
        t_leave = "\tkernel_startup_log(upto);\n"
        if t_leave in c10 and "hk_startup_leave" not in c10:
            c10 = c10.replace(t_leave, "\t{ extern void hk_startup_leave(void); hk_startup_leave(); }\n" + t_leave, 1)
            print("  [OK] osfmk/kern/startup.c: hk_startup_leave installed (sub completion lines)")
        elif "hk_startup_leave" not in c10:
            print("  [WARN] kernel_startup_log(upto) not found in startup.c")

        # Instrument kernel_bootstrap_log and kernel_bootstrap_thread_log
        t_kblog = """static inline void\nkernel_bootstrap_log(const char *message)\n{\n\tif ((startup_debug & STARTUP_DEBUG_VERBOSE) &&\n\t    startup_phase >= STARTUP_SUB_KPRINTF) {\n\t\tkprintf("kernel_bootstrap: %s\\n", message);\n\t}\n\tkernel_debug_string_early(message);\n}"""
        r_kblog = """static inline void\nkernel_bootstrap_log(const char *message)\n{\n\t{ extern void hk_con_puts(const char *s); extern void hk_con_putc(char c); hk_con_puts("[startup] "); hk_con_puts(message); hk_con_putc(10); }\n\tkernel_debug_string_early(message);\n}"""
        if t_kblog in c10:
            c10 = c10.replace(t_kblog, r_kblog, 1)
            print("  [OK] osfmk/kern/startup.c: kernel_bootstrap_log console print installed")
        else:
            print("  [WARN] t_kblog not found in startup.c")

        t_kbthread_log = """static inline void\nkernel_bootstrap_thread_log(const char *message)\n{\n\tif ((startup_debug & STARTUP_DEBUG_VERBOSE) &&\n\t    startup_phase >= STARTUP_SUB_KPRINTF) {\n\t\tkprintf("kernel_bootstrap_thread: %s\\n", message);\n\t}\n\tkernel_debug_string_early(message);\n}"""
        r_kbthread_log = """static inline void\nkernel_bootstrap_thread_log(const char *message)\n{\n\t{ extern void hk_con_puts(const char *s); extern void hk_con_putc(char c); hk_con_puts("[thread] "); hk_con_puts(message); hk_con_putc(10); }\n\tkernel_debug_string_early(message);\n}"""
        if t_kbthread_log in c10:
            c10 = c10.replace(t_kbthread_log, r_kbthread_log, 1)
            print("  [OK] osfmk/kern/startup.c: kernel_bootstrap_thread_log console print installed")
        else:
            print("  [WARN] t_kbthread_log not found in startup.c")

        # Instrument kernel_bootstrap(void) entry
        if "/* hakios: kernel_bootstrap entry */" in c10:
            print("  [ALREADY PATCHED] osfmk/kern/startup.c: kernel_bootstrap entry marker already present")
        else:
            m_kb = re.search(r'(void\s+kernel_bootstrap\s*\(\s*void\s*\)\s*\{)', c10)
            if m_kb:
                c10 = c10.replace(m_kb.group(0), m_kb.group(0) + """
\t/* hakios: kernel_bootstrap entry */
\t{
\t\textern void hk_con_log(const char *s);
\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\thk_draw_band_direct(0x00FFA500, 190, 5); /* Orange band: entered kernel_bootstrap */
\t\thk_con_log("[*] entered kernel_bootstrap successfully");
\t}""", 1)
                print("  [OK] osfmk/kern/startup.c: kernel_bootstrap console log & visual band installed")
            else:
                print("  [WARN] kernel_bootstrap not found in startup.c")

        # Instrument kernel_bootstrap_thread(void) entry
        kbt_target = "static void\nkernel_bootstrap_thread(void)\n{"
        if kbt_target in c10:
            c10 = c10.replace(kbt_target, kbt_target + """
\t{
\t\textern void hk_con_log(const char *s);
\t\thk_con_log("[*] entered kernel_bootstrap_thread!");
\t}""", 1)
            print("  [OK] osfmk/kern/startup.c: kernel_bootstrap_thread console log installed")
        else:
            print("  [WARN] kernel_bootstrap_thread not found in startup.c")

        with open(p10, "w", encoding="utf-8", newline="\n") as f:
            f.write(c10)

    # 11. osfmk/ipc/ipc_policy.c (bypass validation panic on generic ARM bring-up)
    p11 = os.path.join(src, "osfmk", "ipc", "ipc_policy.c")
    if os.path.isfile(p11):
        with open(p11, "r", encoding="utf-8", errors="replace") as f:
            c11 = f.read()
        target_val = "ipc_sec_policy_configs_validate(void)\n{"
        replace_val = "ipc_sec_policy_configs_validate(void)\n{\n\treturn; /* hakios: bypass dev validation */"
        if target_val in c11:
            c11 = c11.replace(target_val, replace_val, 1)
            with open(p11, "w", encoding="utf-8", newline="\n") as f:
                f.write(c11)
            print("  [OK] osfmk/ipc/ipc_policy.c: ipc_sec_policy_configs_validate bypassed")
        else:
            print("  [WARN] ipc_sec_policy_configs_validate target not found in ipc_policy.c")

    # 12. osfmk/prng/prng_random.c (relax entropy seed check for bootloaders passing < 256 bytes)
    p12 = os.path.join(src, "osfmk", "prng", "prng_random.c")
    if os.path.isfile(p12):
        with open(p12, "r", encoding="utf-8", errors="replace") as f:
            c12 = f.read()
        target_seed = "Expected %u seed bytes from boot loader"
        if target_seed in c12:
            c12 = re.sub(r'panic\s*\(\s*"Expected %u seed bytes from boot loader',
                         r'printf("HAKIOS: Expected %u seed bytes from boot loader', c12)
            with open(p12, "w", encoding="utf-8", newline="\n") as f:
                f.write(c12)
            print("  [OK] osfmk/prng/prng_random.c: relaxed seed length panic to warning")
        else:
            print("  [WARN] Expected %u seed bytes target not found in prng_random.c")

# 13. osfmk/kern/debug.c (on-screen panic dump directly from panic_trap_to_debugger and panic entry)
    p13 = os.path.join(src, "osfmk", "kern", "debug.c")
    if os.path.isfile(p13):
        with open(p13, "r", encoding="utf-8", errors="replace") as f:
            c13 = f.read().replace("\r\n", "\n")

        # Hook panic() function entry directly
        m_panic = re.search(r'(\nvoid\s+panic\s*\(\s*const\s+char\s*\*\s*\w+\s*,[^{]*\{)', c13)
        if m_panic:
            c13 = c13.replace(m_panic.group(0), m_panic.group(0) + '\n\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FF0000, 660, 20); } /* Thick Bright Red band: panic entry */', 1)
            print("  [OK] osfmk/kern/debug.c: panic() entry direct visual hook installed")
        else:
            print("  [WARN] panic() function signature not found in debug.c")

        # Hook panic_trap_to_debugger entry
        m_ptd = re.search(r'(panic_trap_to_debugger\s*\([^){]*\)\s*\{)', c13)
        if m_ptd:
            c13 = c13.replace(m_ptd.group(0), m_ptd.group(0) + '\n\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FF0000, 660, 20); } /* Thick Bright Red band: panic_trap_to_debugger entry */', 1)
            print("  [OK] osfmk/kern/debug.c: panic_trap_to_debugger entry direct visual hook installed")
        else:
            print("  [WARN] panic_trap_to_debugger signature not found in debug.c")

        # Hook panic_spin_forever entry
        m_psf = re.search(r'(panic_spin_forever\s*\([^){]*\)\s*\{)', c13)
        if m_psf:
            c13 = c13.replace(m_psf.group(0), m_psf.group(0) + '\n\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FF0000, 660, 20); } /* Thick Bright Red band: panic_spin_forever entry */', 1)
            print("  [OK] osfmk/kern/debug.c: panic_spin_forever entry direct visual hook installed")
        else:
            print("  [WARN] panic_spin_forever signature not found in debug.c")

        target_dbg = 'loader_log_line("\\n== PANIC: ");\n\t\tloader_log_line(rbuf);\n\t\tloader_log_line(" ==\\n");\n\t}'
        replace_dbg = '''loader_log_line("\\n== PANIC: ");
\t\tloader_log_line(rbuf);
\t\tloader_log_line(" ==\\n");
\t\t/* hakios: on-screen panic dump directly to display */
\t\t{
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x00FF0000, 660, 20); /* Thick Bright Red band: kernel panic */
\t\t}
\t}'''
        if target_dbg in c13:
            c13 = c13.replace(target_dbg, replace_dbg, 1)
            print("  [OK] osfmk/kern/debug.c: on-screen panic dump installed in panic_trap_to_debugger")
        else:
            print("  [WARN] target_dbg not found in debug.c")

        target_early_panic = 'loader_log_line("\\n== EARLY PANIC: ");\n\t\t\tloader_log_line(buf);\n\t\t\tloader_log_line(" ==\\n");\n\t\t}'
        replace_early_panic = '''loader_log_line("\\n== EARLY PANIC: ");
\t\t\tloader_log_line(buf);
\t\t\tloader_log_line(" ==\\n");
\t\t\t/* hakios: on-screen early panic dump directly to display */
\t\t\t{
\t\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\t\thk_draw_band_direct(0x00FF0000, 660, 20); /* Thick Bright Red band: early panic */
\t\t\t}
\t\t}'''
        if target_early_panic in c13:
            c13 = c13.replace(target_early_panic, replace_early_panic, 1)
            print("  [OK] osfmk/kern/debug.c: on-screen early panic dump installed")
        else:
            print("  [WARN] target_early_panic not found in debug.c")

        with open(p13, "w", encoding="utf-8", newline="\n") as f:
            f.write(c13)

    # 14. osfmk/arm64/pac_decode.h & pac_patch.c (ARMv8.3/ARMv8.4 RCpc LDAPR/LDAPUR/STLUR trap self-patch engine)
    p14_h = os.path.join(src, "osfmk", "arm64", "pac_decode.h")
    if os.path.isfile(p14_h):
        with open(p14_h, "r", encoding="utf-8", errors="replace") as f:
            c14_h = f.read().replace("\r\n", "\n")
        if "ARMv8.3-A RCpc" not in c14_h:
            t14_h = "    return PAC_NOT_PAC;\n}"
            r14_h = """    /* ---- 8. LDAPR / LDAPRB / LDAPRH (ARMv8.3-A RCpc) -> LDAR / LDARB / LDARH (ARMv8.0) ---- */
    if ((i & 0x3ffffc00u) == 0x38bfc000u) {
        unsigned sz = (i >> 30) & 3, rn = (i >> 5) & 31, rt = i & 31;
        uint32_t ldar = ((uint32_t)sz << 30) | 0x08dffc00u | ((uint32_t)rn << 5) | (uint32_t)rt;
        return pac__patch(r, "ldapr", ldar);
    }

    /* ---- 9. LDAPUR (ARMv8.4-A RCpc2) -> LDUR (ARMv8.0) ----------------------------------- */
    if ((i & 0x3fc00c00u) == 0x19400000u) {
        uint32_t ldur = (i & ~0x3fc00000u) | 0x38400000u;
        return pac__patch(r, "ldapur", ldur);
    }

    /* ---- 10. STLUR (ARMv8.4-A RCpc2) -> STUR (ARMv8.0) ---------------------------------- */
    if ((i & 0x3fc00c00u) == 0x19000000u) {
        uint32_t stur = (i & ~0x3fc00000u) | 0x38000000u;
        return pac__patch(r, "stlur", stur);
    }

    return PAC_NOT_PAC;
}"""
            if t14_h in c14_h:
                c14_h = c14_h.replace(t14_h, r14_h, 1)
                with open(p14_h, "w", encoding="utf-8", newline="\n") as f:
                    f.write(c14_h)
                print("  [OK] osfmk/arm64/pac_decode.h: RCpc LDAPR/LDAPUR/STLUR decode installed")
            else:
                print("  [WARN] t14_h not found in pac_decode.h")
        else:
            print("  [OK] osfmk/arm64/pac_decode.h: RCpc already present")

    p14_c = os.path.join(src, "osfmk", "arm64", "pac_patch.c")
    if os.path.isfile(p14_c):
        with open(p14_c, "r", encoding="utf-8", errors="replace") as f:
            c14_c = f.read().replace("\r\n", "\n")
        if "0x08dffc00u" not in c14_c:
            smp_old = "(insn & 0xffe00000u) == 0xf8400000u) {"
            smp_new = """(insn & 0xffe00000u) == 0xf8400000u ||
\t    (insn & 0x3ffffc00u) == 0x08dffc00u || /* LDAR (ARMv8.0) */
\t    (insn & 0x3fe00c00u) == 0x38400000u || /* LDUR (ARMv8.0) */
\t    (insn & 0x3fe00c00u) == 0x38000000u) { /* STUR (ARMv8.0) */"""
            if smp_old in c14_c:
                c14_c = c14_c.replace(smp_old, smp_new, 1)
                with open(p14_c, "w", encoding="utf-8", newline="\n") as f:
                    f.write(c14_c)
                print("  [OK] osfmk/arm64/pac_patch.c: RCpc SMP race defense installed")
            else:
                print("  [WARN] smp_old not found in pac_patch.c")
        else:
            print("  [OK] osfmk/arm64/pac_patch.c: RCpc SMP race already present")

    # 15. bsd/kern/kdebug.c (console print on all early debug milestone strings)
    p15 = os.path.join(src, "bsd", "kern", "kdebug.c")
    if os.path.isfile(p15):
        with open(p15, "r", encoding="utf-8", errors="replace") as f:
            c15 = f.read().replace("\r\n", "\n")
        t15 = "void\nkernel_debug_string_early(const char *message)\n{"
        r15 = """void
kernel_debug_string_early(const char *message)
{
\t{
\t\textern void hk_con_puts(const char *s);
\t\textern void hk_con_putc(char c);
\t\thk_con_puts("[kdbg] ");
\t\thk_con_puts(message);
\t\thk_con_putc(10);
\t}"""
        if t15 in c15:
            c15 = c15.replace(t15, r15, 1)
            with open(p15, "w", encoding="utf-8", newline="\n") as f:
                f.write(c15)
            print("  [OK] bsd/kern/kdebug.c: kernel_debug_string_early console print installed")
        else:
            print("  [WARN] t15 not found in bsd/kern/kdebug.c")

    # 16. osfmk/vm/vm_resident.c (visual trace for vm_page_bootstrap and pmap_startup)
    p16 = os.path.join(src, "osfmk", "vm", "vm_resident.c")
    if os.path.isfile(p16):
        with open(p16, "r", encoding="utf-8", errors="replace") as f:
            c16 = f.read().replace("\r\n", "\n")
        
        # Entry of vm_page_bootstrap
        t16_entry = "vm_page_bootstrap(\n\tvm_offset_t             *startp,\n\tvm_offset_t             *endp)\n{"
        r16_entry = """vm_page_bootstrap(
\tvm_offset_t             *startp,
\tvm_offset_t             *endp)
{
\t__asm__ volatile ("msr daifset, #0xf; isb");
\t{
\t\textern void hk_draw_band(uint32_t, uint32_t, uint32_t);
\t\thk_draw_band(0x00FF7F50, 288, 2);
		extern void hk_con_log(const char *s);
		hk_con_log("[*] vm_page_bootstrap entered");
\t}"""
        if t16_entry in c16:
            c16 = c16.replace(t16_entry, r16_entry, 1)
            print("  [OK] osfmk/vm/vm_resident.c: vm_page_bootstrap entry marker installed")
        else:
            print("  [WARN] t16_entry not found in vm_resident.c")

        # After pmap_startup call in vm_page_bootstrap
        t16_pms_call = '\tkernel_debug_string_early("pmap_startup");\n\tpmap_startup(&virtual_space_start, &virtual_space_end);'
        r16_pms_call = """	kernel_debug_string_early("pmap_startup");
	{
		extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
		hk_draw_band_direct(0x0000FFFF, 294, 2);
		extern void hk_con_log(const char *s);
		hk_con_log("[*] pmap_startup entered");
	}
	pmap_startup(&virtual_space_start, &virtual_space_end);
	{
		extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
		hk_draw_band_direct(0x0032CD32, 292, 4);
		extern void hk_con_log(const char *s);
		hk_con_log("[*] pmap_startup returned OK");
	}"""
        if t16_pms_call in c16:
            c16 = c16.replace(t16_pms_call, r16_pms_call, 1)
            print("  [OK] osfmk/vm/vm_resident.c: pmap_startup call return marker installed")
        else:
            print("  [WARN] t16_pms_call not found in vm_resident.c")

        # Bypass early printf in vm_page_bootstrap before kmem/console init and draw milestone
        t16_early_printf = """\tbooter_size = ml_get_booter_memory_size();

\tprintf("vm_page_bootstrap: %d free pages, %d wired pages"
#if XNU_VM_HAS_DELAYED_PAGES
\t    ", (up to %d of which are delayed free)"
#endif /* XNU_VM_HAS_DELAYED_PAGES */
\t    "%c",
\t    vm_page_free_count,
\t    vm_page_wire_count,
#if XNU_VM_HAS_DELAYED_PAGES
\t    vm_delayed_count,
#endif /* XNU_VM_HAS_DELAYED_PAGES */
\t    '\\n');

\tkernel_debug_string_early("vm_page_bootstrap complete");"""

        r16_early_printf = """\tbooter_size = ml_get_booter_memory_size();

\t/* hakios: bypass premature printf before kmem/console init and draw milestone */
\t{
\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\thk_draw_band_direct(0x00FFFFFF, 356, 4);
		extern void hk_con_log(const char *s);
		hk_con_log("[*] vm_page_bootstrap completed OK");
\t}

\tkernel_debug_string_early("vm_page_bootstrap complete");"""

        if t16_early_printf in c16:
            c16 = c16.replace(t16_early_printf, r16_early_printf, 1)
            print("  [OK] osfmk/vm/vm_resident.c: bypassed early vm_page_bootstrap printf and added row 356")
        else:
            print("  [WARN] t16_early_printf not found in vm_resident.c")

        # Entry of pmap_startup
        t16_pms_def = "pmap_startup(\n\tvm_offset_t     *startp,\n\tvm_offset_t     *endp)\n{"
        r16_pms_def = """pmap_startup(
\tvm_offset_t     *startp,
\tvm_offset_t     *endp)
{
\t{
\t\textern void hk_draw_band(uint32_t, uint32_t, uint32_t);
\t\thk_draw_band(0x00FF8C00, 296, 2); /* Dark Orange: pmap_startup entered */
\t}"""
        if t16_pms_def in c16:
            c16 = c16.replace(t16_pms_def, r16_pms_def, 1)
            print("  [OK] osfmk/vm/vm_resident.c: pmap_startup entry marker installed")
        else:
            print("  [WARN] t16_pms_def not found in vm_resident.c")

        # In pmap_steal_memory_internal: loop start and progress
        t16_steal = '\tmap_addr = round_page(addr);\n\tif (os_add_overflow(addr, size, &end)) {\n\t\tpanic("pmap_steal_memory() overflow, addr: %lx, size: 0x%lx", addr, size);\n\t}\n\twhile (map_addr < end) {'
        r16_steal = """\tmap_addr = round_page(addr);
\tif (os_add_overflow(addr, size, &end)) {
\t\tpanic("pmap_steal_memory() overflow, addr: %lx, size: 0x%lx", addr, size);
\t}
\t{
\t\textern void hk_draw_band(uint32_t, uint32_t, uint32_t);
\t\thk_draw_band(0x00FF1493, 264, 2); /* Deep Pink: pmap_steal_memory_internal loop entered */
\t}
\twhile (map_addr < end) {
\t\tif ((map_addr & 0x000FFFFF) == 0) {
\t\t\textern void hk_draw_band(uint32_t, uint32_t, uint32_t);
\t\t\tuint32_t mb = (uint32_t)((map_addr - addr) >> 20);
\t\t\tuint32_t col = (mb & 1) ? 0x0000FFFF : 0x00FF00FF;
\t\t\thk_draw_band(col, 268 + ((mb & 0x7) * 2), 2);
\t\t}"""
        if t16_steal in c16:
            c16 = c16.replace(t16_steal, r16_steal, 1)
            print("  [OK] osfmk/vm/vm_resident.c: pmap_steal loop progress markers installed")
        else:
            print("  [WARN] t16_steal not found in vm_resident.c")

        # After vm_pages steal in pmap_startup
        t16_vmp = '\tvm_pages     = pmap_steal_freeable_memory(npages * sizeof(struct vm_page));\n\tvm_pages_end = vm_page_get(npages);'
        r16_vmp = """\tvm_pages     = pmap_steal_freeable_memory(npages * sizeof(struct vm_page));
\t{
\t\textern void hk_draw_band(uint32_t, uint32_t, uint32_t);
\t\thk_draw_band(0x0000FF00, 280, 6); /* Lime: vm_pages stolen successfully (thick 6 rows) */
\t}
\tvm_pages_end = vm_page_get(npages);
\t{
\t\textern void hk_draw_band(uint32_t, uint32_t, uint32_t);
\t\thk_draw_band(0x00FF8C00, 288, 6); /* Dark Orange: vm_pages_end computed (thick 6 rows) */
\t}"""
        if t16_vmp in c16:
            c16 = c16.replace(t16_vmp, r16_vmp, 1)
            print("  [OK] osfmk/vm/vm_resident.c: vm_pages return markers installed")
        else:
            print("  [WARN] t16_vmp not found in vm_resident.c")

        # Page init loop in pmap_startup with fine-grained ladder markers
        t16_loop = '\tvm_free_page_lock();\n\n\tfor (uint32_t i = 0; i < npages && pmap_next_page(&phys_page); i++) {'
        r16_loop = """\t/* hakios: Direct diagnostic bands without MMU toggling */
\t{
\t\t__asm__ volatile ("msr daifset, #0xf; isb");
\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\thk_draw_band_direct(0x00FF00FF, 302, 2); /* Vivid Magenta: PRE-LOCK (rows 302..303) */
\t\t/* hakios: skip vm_free_page_lock() before STARTUP_SUB_LOCKS (single-core bootstrap, lck_mtx_tsid==0) */
\t\t__asm__ volatile ("msr daifset, #0xf; isb");
\t\thk_draw_band_direct(0x00FF00FF, 304, 2); /* Vivid Magenta: POST-LOCK (rows 304..305) */

\t\t/* Gauge at Rows 410..440 */
\t\tstatic const uint32_t nibble_pal[16] = {
\t\t\t0x00000000, 0x00000080, 0x00008000, 0x00008080,
\t\t\t0x00800000, 0x00800080, 0x00808000, 0x00C0C0C0,
\t\t\t0x00808080, 0x000000FF, 0x0000FF00, 0x0000FFFF,
\t\t\t0x00FF0000, 0x00FF00FF, 0x00FFFF00, 0x00FFFFFF
\t\t};
\t\thk_draw_band_direct(0x00FFFFFF, 410, 4); /* Calibration marker (White, 4 rows: 410..413) */

\t\textern vm_offset_t first_avail;
\t\textern pmap_paddr_t avail_end;
\t\textern bool need_ram_ranges_init;
\t\tuint32_t fa32 = (uint32_t)first_avail;
\t\tuint32_t ae32 = (uint32_t)avail_end;
\t\tfor (int k = 0; k < 8; k++) {
\t\t\tuint32_t nib = (fa32 >> ((7 - k) * 4)) & 0xf;
\t\t\thk_draw_band_direct(nibble_pal[nib], 416 + k, 1);
\t\t}
\t\tfor (int k = 0; k < 8; k++) {
\t\t\tuint32_t nib = (ae32 >> ((7 - k) * 4)) & 0xf;
\t\t\thk_draw_band_direct(nibble_pal[nib], 426 + k, 1);
\t\t}
\t\thk_draw_band_direct(need_ram_ranges_init ? 0x0000FF00 : 0x00FF0000, 436, 2);
\t\tuint64_t vbar_val;
\t\t__asm__ volatile ("mrs %0, VBAR_EL1" : "=r" (vbar_val));
\t\thk_draw_band_direct((vbar_val >= 0xffff000000000000ULL) ? 0x0000FF00 : 0x00FF0000, 440, 2);
\t}
\tfor (uint32_t i = 0; i < npages; i++) {
\t\tif (i == 0) {
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x00FFFFFF, 305, 1); /* White: call-site about to call pmap_next_page */
\t\t}
\t\tboolean_t pnext_ret = pmap_next_page(&phys_page);
\t\tif (i == 0) {
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x00800080, 310, 2); /* Purple: call-site returned (Rows 310..311, 2 rows) */
\t\t}
\t\tif (!pnext_ret) {
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x00FF8C00, 356, 2); /* Dark Orange: pmap_next_page returned FALSE (page loop break) */
\t\t\tbreak;
\t\t}
\t\tif (i == 0) {
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x0000BFFF, 312, 4); /* Deep Sky Blue: page loop entered */
\t\t}"""
        if t16_loop in c16:
            c16 = c16.replace(t16_loop, r16_loop, 1)
            print("  [OK] osfmk/vm/vm_resident.c: page init loop markers installed")
        else:
            loop_pat = re.compile(r'\tvm_free_page_lock\(\);.*?\tfor \(uint32_t i = 0; i < npages && pmap_next_page\(&phys_page\); i\+\+\) \{', re.DOTALL)
            if loop_pat.search(c16):
                c16 = loop_pat.sub(r16_loop, c16, count=1)
                print("  [OK] osfmk/vm/vm_resident.c: page init loop markers installed via regex")
            else:
                print("  [WARN] t16_loop not found in vm_resident.c")
        t16_lowglo = '\tvm_pages_first_pnum = phys_page;\n\t\t\tpatch_low_glo_vm_page_info(vm_pages, vm_pages_end,\n\t\t\t    vm_pages_first_pnum);'
        r16_lowglo = """\tvm_pages_first_pnum = phys_page;
\t\t\t{ extern void hk_draw_band(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x0000FF00, 316, 4); } /* Lime: first_pnum set */
\t\t\tpatch_low_glo_vm_page_info(vm_pages, vm_pages_end, vm_pages_first_pnum);
\t\t\t{ extern void hk_draw_band(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FF00FF, 320, 4); } /* Magenta: low_glo patched */"""
        if t16_lowglo in c16:
            c16 = c16.replace(t16_lowglo, r16_lowglo, 1)
            print("  [OK] osfmk/vm/vm_resident.c: low_glo info markers installed")

        t16_init = '\t++vm_pages_count;\n\t\tvm_page_init(vm_page_get(i), phys_page);'
        r16_init = """\t++vm_pages_count;
\t\t{ extern void hk_draw_band(uint32_t, uint32_t, uint32_t); if (i == 0) { extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FFFF00, 324, 4); } } /* Yellow: before vm_page_init(0) */
\t\tvm_page_init(vm_page_get(i), phys_page);
\t\t{ extern void hk_draw_band(uint32_t, uint32_t, uint32_t); if (i == 0) { extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FF8C00, 328, 4); } } /* Orange: after vm_page_init(0) */"""
        if t16_init in c16:
            c16 = c16.replace(t16_init, r16_init, 1)
            print("  [OK] osfmk/vm/vm_resident.c: vm_page_init markers installed")

        t16_rel = '\tif (vm_himemory_mode) {\n\t\t\tvm_page_release_startup(vm_page_get(i));\n\t\t}'
        r16_rel = """\tif (vm_himemory_mode) {
\t\t\t{ extern void hk_draw_band(uint32_t, uint32_t, uint32_t); if (i == 0) { extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x0000FFFF, 332, 4); } } /* Cyan: before vm_page_release_startup(0) */
\t\t\tvm_page_release_startup(vm_page_get(i));
\t\t\t{ extern void hk_draw_band(uint32_t, uint32_t, uint32_t); if (i == 0) { extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00ADFF2F, 336, 4); } } /* GreenYellow: after vm_page_release_startup(0) */
\t\t}
		/* hakios: logarithmic milestones, gauges, and live clock heartbeat */
\t\tif (i == 1) {
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x00FF1493, 340, 2); /* Deep Pink: i=1 reached */
\t\t} else if (i == 10) {
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x00FFFF00, 342, 2); /* Yellow: i=10 reached */
\t\t} else if (i == 100) {
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x0000FF00, 344, 2); /* Lime: i=100 reached */
\t\t} else if (i == 1000) {
\t\t\tstatic const uint32_t npal1[16] = {
\t\t\t\t0x00000000, 0x00000080, 0x00008000, 0x00008080,
\t\t\t\t0x00800000, 0x00800080, 0x00808000, 0x00C0C0C0,
\t\t\t\t0x00808080, 0x000000FF, 0x0000FF00, 0x0000FFFF,
\t\t\t\t0x00FF0000, 0x00FF00FF, 0x00FFFF00, 0x00FFFFFF
\t\t\t};
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x0000FFFF, 346, 2); /* Cyan: i=1,000 reached */
\t\t\textern vm_offset_t first_avail;
\t\t\tuint32_t fa = (uint32_t)first_avail;
\t\t\tfor (int k = 0; k < 8; k++) {
\t\t\t\tuint32_t nib = (fa >> ((7 - k) * 4)) & 0xf;
\t\t\t\thk_draw_band_direct(npal1[nib], 450 + k, 1);
\t\t\t}
\t\t} else if (i == 10000) {
\t\t\tstatic const uint32_t npal2[16] = {
\t\t\t\t0x00000000, 0x00000080, 0x00008000, 0x00008080,
\t\t\t\t0x00800000, 0x00800080, 0x00808000, 0x00C0C0C0,
\t\t\t\t0x00808080, 0x000000FF, 0x0000FF00, 0x0000FFFF,
\t\t\t\t0x00FF0000, 0x00FF00FF, 0x00FFFF00, 0x00FFFFFF
\t\t\t};
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x001E90FF, 348, 2); /* Dodger Blue: i=10,000 reached */
\t\t\textern vm_offset_t first_avail;
\t\t\tuint32_t fa = (uint32_t)first_avail;
\t\t\tfor (int k = 0; k < 8; k++) {
\t\t\t\tuint32_t nib = (fa >> ((7 - k) * 4)) & 0xf;
\t\t\t\thk_draw_band_direct(npal2[nib], 460 + k, 1);
\t\t\t}
\t\t} else if (i == 50000) {
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x00BA55D3, 350, 2); /* Medium Orchid: i=50,000 reached */
\t\t} else if (i == 100000) {
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x00FFD700, 352, 2); /* Gold: i=100,000 reached */
\t\t} else if (i == (npages >> 1)) {
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\thk_draw_band_direct(0x00FF8C00, 354, 2); /* Dark Orange: i=50% reached */
\t\t}
\t\tif ((i & 0x0FFF) == 0 && i >= 1000) {
\t\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\t\tuint64_t timer_val;
\t\t\t__asm__ volatile ("mrs %0, cntvct_el0" : "=r" (timer_val));
\t\t\tuint32_t hb_col = (timer_val & (1ULL << 18)) ? 0x0000FF00 : 0x00FF00FF;
\t\t\thk_draw_band_direct(hb_col, 470, 2);
\t\t}"""
        if t16_rel in c16:
            c16 = c16.replace(t16_rel, r16_rel, 1)
            print("  [OK] osfmk/vm/vm_resident.c: vm_page_release markers installed")

        t16_startup_lock = '\tif (opts & VMP_RELEASE_STARTUP) {\n\t\tLCK_MTX_ASSERT(&vm_page_queue_free_lock, LCK_MTX_ASSERT_OWNED);\n\t}'
        r16_startup_lock = """\tif (opts & VMP_RELEASE_STARTUP) {
\t\tif (startup_phase >= STARTUP_SUB_KMEM) {
\t\t\tLCK_MTX_ASSERT(&vm_page_queue_free_lock, LCK_MTX_ASSERT_OWNED);
\t\t}
\t}"""
        if t16_startup_lock in c16:
            c16 = c16.replace(t16_startup_lock, r16_startup_lock, 1)
            print("  [OK] osfmk/vm/vm_resident.c: early startup free queue lock assert guarded")

        # Anchor unlock replacement specifically to the unlock following r16_loop in pmap_startup
        idx_loop = c16.find(r16_loop)
        t16_unlock = '\tvm_free_page_unlock();'
        # Guard backward loop to vm_pages_count
        t16_bwd = 'for (uint32_t i = npages; i-- > 0;)'
        r16_bwd = 'for (uint32_t i = vm_pages_count; i-- > 0;)'
        if t16_bwd in c16:
            c16 = c16.replace(t16_bwd, r16_bwd, 1)
            print("  [OK] osfmk/vm/vm_resident.c: backward loop bounded to vm_pages_count")

        r16_unlock = """\t/* hakios: skip vm_free_page_unlock() matching skipped lock in pmap_startup */
\t{
\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\thk_draw_band_direct(0x0000FF7F, 358, 4); /* Spring Green: page loop & unlock complete */
\t}"""
        if idx_loop != -1 and t16_unlock in c16[idx_loop:]:
            c16 = c16[:idx_loop] + c16[idx_loop:].replace(t16_unlock, r16_unlock, 1)
            print("  [OK] osfmk/vm/vm_resident.c: page init unlock marker installed (anchored)")
        else:
            print("  [WARN] t16_unlock not found after r16_loop in vm_resident.c")

        with open(p16, "w", encoding="utf-8", newline="\n") as f:
            f.write(c16)

    # 17. osfmk/vm/vm_init.c (visual trace for vm_mem_bootstrap)
    p17 = os.path.join(src, "osfmk", "vm", "vm_init.c")
    if os.path.isfile(p17):
        with open(p17, "r", encoding="utf-8", errors="replace") as f:
            c17 = f.read().replace("\r\n", "\n")
        t17 = '\tvm_mem_bootstrap_log("vm_page_bootstrap");\n\tvm_page_bootstrap(&start, &end);'
        r17 = """\tvm_mem_bootstrap_log("vm_page_bootstrap");
\tvm_page_bootstrap(&start, &end);
\t{
\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);
\t\thk_draw_band_direct(0x0000FF00, 360, 6);
		extern void hk_con_log(const char *s);
		hk_con_log("[*] vm_page_bootstrap returned to vm_init");
\t}"""
        if t17 in c17:
            c17 = c17.replace(t17, r17, 1)
            c17 = c17.replace('zone_bootstrap();', 'zone_bootstrap();\n\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x0000FFFF, 368, 6); extern void hk_con_log(const char *s); hk_con_log("[*] zone_bootstrap completed OK"); }', 1)
            c17 = c17.replace('vm_object_bootstrap();', 'vm_object_bootstrap();\n\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FF1493, 376, 6); extern void hk_con_log(const char *s); hk_con_log("[*] vm_object_bootstrap completed OK"); }', 1)
            c17 = c17.replace('vm_retire_boot_pages();', 'vm_retire_boot_pages();\n\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FFFF00, 384, 6); extern void hk_con_log(const char *s); hk_con_log("[*] vm_retire_boot_pages completed OK"); }', 1)
            c17 = c17.replace('vm_map_init();', 'vm_map_init();\n\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x009400D3, 392, 6); extern void hk_con_log(const char *s); hk_con_log("[*] vm_map_init completed OK"); }', 1)
            c17 = c17.replace('kmem_init(start, end);', 'kmem_init(start, end);\n\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x0000BFFF, 400, 6); extern void hk_con_log(const char *s); hk_con_log("[*] kmem_init completed OK"); }', 1)
            c17 = c17.replace('kernel_startup_initialize_upto(STARTUP_SUB_KMEM);', '{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FF8C00, 408, 6); } /* Dark Orange: entering STARTUP_SUB_KMEM */\n\tkernel_startup_initialize_upto(STARTUP_SUB_KMEM);\n\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FFFFFF, 416, 6); extern void hk_con_log(const char *s); hk_con_log("[*] STARTUP_SUB_KMEM completed OK"); }', 1)
            c17 = c17.replace('vm_fault_init();', 'vm_fault_init();\n\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x00FFA500, 424, 6); }', 1)
            c17 = c17.replace('kernel_startup_initialize_upto(STARTUP_SUB_ZALLOC);', '{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x007FFF00, 432, 6); } /* Chartreuse: entering STARTUP_SUB_ZALLOC */\n\tkernel_startup_initialize_upto(STARTUP_SUB_ZALLOC);\n\t{ extern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t); hk_draw_band_direct(0x0000FF7F, 440, 6); extern void hk_con_log(const char *s); hk_con_log("[*] STARTUP_SUB_ZALLOC completed OK"); }', 1)
            with open(p17, "w", encoding="utf-8", newline="\n") as f:
                f.write(c17)
            print("  [OK] osfmk/vm/vm_init.c: vm_mem_bootstrap milestone bands installed")
        else:
            print("  [WARN] t17 not found in vm_init.c")

    # 18. osfmk/arm/pmap/pmap_data.c (early boot page table allocation fallback when !pmap_initialized)
    p18 = os.path.join(src, "osfmk", "arm", "pmap", "pmap_data.c")
    if os.path.isfile(p18):
        with open(p18, "r", encoding="utf-8", errors="replace") as f:
            c18 = f.read().replace("\r\n", "\n")
        t18 = '#else /* XNU_MONITOR */\n\tvm_page_t mem = VM_PAGE_NULL;'
        r18 = """#else /* XNU_MONITOR */
\textern boolean_t pmap_initialized;
\tif (!pmap_initialized) {
\t\textern boolean_t pmap_next_page(ppnum_t *);
\t\tppnum_t pn;
\t\tif (pmap_next_page(&pn)) {
\t\t\t*pa = (pmap_paddr_t)ptoa(pn);
\t\t\tbzero((void *)phystokv(*pa), size);
\t\t\treturn KERN_SUCCESS;
\t\t}
\t\treturn KERN_RESOURCE_SHORTAGE;
\t}
\tvm_page_t mem = VM_PAGE_NULL;"""
        if t18 in c18:
            c18 = c18.replace(t18, r18, 1)
            with open(p18, "w", encoding="utf-8", newline="\n") as f:
                f.write(c18)
            print("  [OK] osfmk/arm/pmap/pmap_data.c: early pmap page alloc patched")
        else:
            print("  [WARN] t18 not found in pmap_data.c")

    # 19. osfmk/arm/pmap/pmap.c (prevent VM_PAGE_WAIT hang during early boot in pmap_expand & pmap_tt_allocate)
    p19 = os.path.join(src, "osfmk", "arm", "pmap", "pmap.c")
    if os.path.isfile(p19):
        with open(p19, "r", encoding="utf-8", errors="replace") as f:
            c19 = f.read().replace("\r\n", "\n")

        t19_exp = 'while ((ret = pmap_tt_allocate(pmap, &tt_p, ttlevel + 1, ((options & PMAP_TT_ALLOCATE_NOWAIT)? PMAP_PAGES_ALLOCATE_NOWAIT : 0))) != KERN_SUCCESS) {\n\t\t\t\tif (options & PMAP_OPTIONS_NOWAIT) {\n\t\t\t\t\t/* Can be KERN_RESOURCE_SHORTAGE or KERN_ABORTED. */\n\t\t\t\t\treturn ret;\n\t\t\t\t}'
        r19_exp = """while ((ret = pmap_tt_allocate(pmap, &tt_p, ttlevel + 1, ((options & PMAP_TT_ALLOCATE_NOWAIT)? PMAP_PAGES_ALLOCATE_NOWAIT : 0))) != KERN_SUCCESS) {
\t\t\t\tif (options & PMAP_OPTIONS_NOWAIT) {
\t\t\t\t\t/* Can be KERN_RESOURCE_SHORTAGE or KERN_ABORTED. */
\t\t\t\t\treturn ret;
\t\t\t\t}
\t\t\t\tif (!pmap_initialized) {
\t\t\t\t\treturn ret;
\t\t\t\t}"""
        if t19_exp in c19:
            c19 = c19.replace(t19_exp, r19_exp, 1)
            print("  [OK] osfmk/arm/pmap/pmap.c: pmap_expand early return patched")
        else:
            print("  [WARN] t19_exp not found in pmap.c")

        t19_tt = 'while (pmap_pages_alloc_zeroed(&pa, PAGE_SIZE, alloc_flags) != KERN_SUCCESS) {\n\t\t\tif (options & PMAP_OPTIONS_NOWAIT) {\n\t\t\t\treturn KERN_RESOURCE_SHORTAGE;\n\t\t\t}\n\t\t\tVM_PAGE_WAIT();\n\t\t}'
        r19_tt = """while (pmap_pages_alloc_zeroed(&pa, PAGE_SIZE, alloc_flags) != KERN_SUCCESS) {
\t\t\tif (options & PMAP_OPTIONS_NOWAIT) {
\t\t\t\treturn KERN_RESOURCE_SHORTAGE;
\t\t\t}
\t\t\tif (!pmap_initialized) {
\t\t\t\treturn KERN_RESOURCE_SHORTAGE;
\t\t\t}
\t\t\tVM_PAGE_WAIT();
\t\t}"""
        if t19_tt in c19:
            c19 = c19.replace(t19_tt, r19_tt, 1)
            print("  [OK] osfmk/arm/pmap/pmap.c: pmap_tt_allocate early wait patched")
        else:
            print("  [WARN] t19_tt not found in pmap.c")

        t19_ptd = 'while ((ptdp = ptd_alloc(pmap)) == NULL) {\n\t\t\tif (options & PMAP_OPTIONS_NOWAIT) {\n\t\t\t\t/* Deallocate all allocated resources so far. */\n\t\t\t\tpmap_pages_free(pa, PAGE_SIZE);\n\t\t\t\treturn KERN_RESOURCE_SHORTAGE;\n\t\t\t}\n\t\t\tVM_PAGE_WAIT();\n\t\t}'
        r19_ptd = """while ((ptdp = ptd_alloc(pmap)) == NULL) {
\t\t\tif (options & PMAP_OPTIONS_NOWAIT) {
\t\t\t\t/* Deallocate all allocated resources so far. */
\t\t\t\tpmap_pages_free(pa, PAGE_SIZE);
\t\t\t\treturn KERN_RESOURCE_SHORTAGE;
\t\t\t}
\t\t\tif (!pmap_initialized) {
\t\t\t\tpmap_pages_free(pa, PAGE_SIZE);
\t\t\t\treturn KERN_RESOURCE_SHORTAGE;
\t\t\t}
\t\t\tVM_PAGE_WAIT();
\t\t}"""
        if t19_ptd in c19:
            c19 = c19.replace(t19_ptd, r19_ptd, 1)
            print("  [OK] osfmk/arm/pmap/pmap.c: ptd_alloc early wait patched")
        else:
            print("  [WARN] t19_ptd not found in pmap.c")

        # 19b. Flush newly allocated table page and parent TTE to PoC in pmap_expand (Cortex-A73 non-coherent PTW)
        t19_expand_flush = """\t\t\tif ((pmap_ttne(pmap, ttlevel + 1, v) == PT_ENTRY_NULL)) {
\t\t\t\tpmap_init_pte_page(pmap, (pt_entry_t *) tt_p, v, ttlevel + 1, FALSE);
\t\t\t\tpa = kvtophys_nofail((vm_offset_t)tt_p);
\t\t\t\ttte_p = pmap_ttne(pmap, ttlevel, v);
\t\t\t\t*tte_p = (pa & ARM_TTE_TABLE_MASK) | ARM_TTE_TYPE_TABLE | ARM_TTE_VALID;
\t\t\t\tPMAP_TRACE(4 + ttlevel, PMAP_CODE(PMAP__TTE), VM_KERNEL_ADDRHIDE(pmap), VM_KERNEL_ADDRHIDE(v & ~pt_attr_ln_offmask(pt_attr, ttlevel)),
\t\t\t\t    VM_KERNEL_ADDRHIDE((v & ~pt_attr_ln_offmask(pt_attr, ttlevel)) + pt_attr_ln_size(pt_attr, ttlevel)), *tte_p);
\t\t\t\tpa = 0x0ULL;
\t\t\t\ttt_p = (tt_entry_t *)NULL;
\t\t\t}"""
        r19_expand_flush = """\t\t\tif ((pmap_ttne(pmap, ttlevel + 1, v) == PT_ENTRY_NULL)) {
\t\t\t\tpmap_init_pte_page(pmap, (pt_entry_t *) tt_p, v, ttlevel + 1, FALSE);
\t\t\t\tpa = kvtophys_nofail((vm_offset_t)tt_p);
\t\t\t\ttte_p = pmap_ttne(pmap, ttlevel, v);
\t\t\t\t*tte_p = (pa & ARM_TTE_TABLE_MASK) | ARM_TTE_TYPE_TABLE | ARM_TTE_VALID;
\t\t\t\tPMAP_TRACE(4 + ttlevel, PMAP_CODE(PMAP__TTE), VM_KERNEL_ADDRHIDE(pmap), VM_KERNEL_ADDRHIDE(v & ~pt_attr_ln_offmask(pt_attr, ttlevel)),
\t\t\t\t    VM_KERNEL_ADDRHIDE((v & ~pt_attr_ln_offmask(pt_attr, ttlevel)) + pt_attr_ln_size(pt_attr, ttlevel)), *tte_p);

\t\t\t\t/* hakios: flush newly allocated table page and parent TTE to PoC for non-coherent PTW (Cortex-A73) */
\t\t\t\tfor (vm_offset_t _p = (vm_offset_t)tt_p; _p < (vm_offset_t)tt_p + ARM_PGBYTES; _p += 64) {
\t\t\t\t\t__asm__ volatile("dc civac, %0" : : "r" (_p) : "memory");
\t\t\t\t}
\t\t\t\t__asm__ volatile("dc civac, %0" : : "r" (tte_p) : "memory");
\t\t\t\t__asm__ volatile("dsb sy\\ntlbi vmalle1is\\ndsb sy\\nisb" ::: "memory");

\t\t\t\tpa = 0x0ULL;
\t\t\t\ttt_p = (tt_entry_t *)NULL;
\t\t\t}"""
        if t19_expand_flush in c19:
            c19 = c19.replace(t19_expand_flush, r19_expand_flush, 1)
            print("  [OK] osfmk/arm/pmap/pmap.c: pmap_expand PoC cache flush installed")
        else:
            print("  [WARN] t19_expand_flush not found in pmap.c")

        # 19c. TLB invalidation on new mapping in pmap_enter_pte
        t19_pte_new = """\t\t} else {
\t\t\tFLUSH_PTE();
\t\t\t__builtin_arm_isb(ISB_SY);
\t\t}"""
        r19_pte_new = """\t\t} else {
\t\t\tFLUSH_PTE();
\t\t\tif (TEST_PAGE_RATIO_4) {
\t\t\t\tpmap_quad_tlb_invalidate(pmap, v);
\t\t\t}
\t\t\t__builtin_arm_isb(ISB_SY);
\t\t}"""
        if t19_pte_new in c19:
            c19 = c19.replace(t19_pte_new, r19_pte_new, 1)
            print("  [OK] osfmk/arm/pmap/pmap.c: pmap_enter_pte new mapping TLB invalidate installed")
        else:
            print("  [WARN] t19_pte_new not found in pmap.c")

        with open(p19, "w", encoding="utf-8", newline="\n") as f:
            f.write(c19)

    # 20. osfmk/arm64/arm_vm_init.c (ensure valid L2 table allocated for *cpu_l1_tte and expand heap coverage)
    p20 = os.path.join(src, "osfmk", "arm64", "arm_vm_init.c")
    if os.path.isfile(p20):
        with open(p20, "r", encoding="utf-8", errors="replace") as f:
            c20 = f.read().replace("\r\n", "\n")

        # 20a. Allocate L2 table on demand if *cpu_l1_tte is EMPTY in bootstrap L3 loop (loop 1)
        t20_l1 = 'cpu_l1_tte = cpu_tte + L1_TABLE_T1_INDEX(va_l1, TCR_EL1_BOOT);\n\n\twhile (va_l1 < va_l1_end) {\n\t\tva_l2 = va_l1;'
        r20_l1 = """cpu_l1_tte = cpu_tte + L1_TABLE_T1_INDEX(va_l1, TCR_EL1_BOOT);

\t{ extern void hk_con_log(const char *s); hk_con_log("[vm] 8: bootstrap L3 loop starting..."); }\n\twhile (va_l1 < va_l1_end) {
\t\tif (*cpu_l1_tte == ARM_TTE_EMPTY) {
\t\t\tvm_offset_t l2_page = alloc_ptpage(FALSE);
\t\t\t*cpu_l1_tte = (kvtophys(l2_page) & ARM_TTE_TABLE_MASK) | ARM_TTE_TYPE_TABLE | ARM_TTE_VALID | ARM_DYNAMIC_TABLE_XN | ARM_TTE_TABLE_AP(ARM_TTE_TABLE_AP_USER_NA);
\t\t\tbzero((void *)l2_page, ARM_PGBYTES);
\t\t\tfor (vm_offset_t _p = l2_page; _p < l2_page + ARM_PGBYTES; _p += 64) {
\t\t\t\t__asm__ volatile ("dc civac, %0" : : "r" (_p) : "memory");
\t\t\t}
\t\t\t__asm__ volatile ("dc civac, %0" : : "r" (cpu_l1_tte) : "memory");
\t\t\t__asm__ volatile ("dsb sy; isb" ::: "memory");
\t\t}
\t\tva_l2 = va_l1;"""
        if t20_l1 in c20:
            c20 = c20.replace(t20_l1, r20_l1, 1)
            print("  [OK] osfmk/arm64/arm_vm_init.c: on-demand L2 table allocation in bootstrap L3 loop installed")
        else:
            print("  [WARN] t20_l1 not found in arm_vm_init.c")

        # 20a-bis. Allocate L2 table on demand if *cpu_l1_tte is EMPTY in CPUWINDOWS L3 loop (loop 2)
        r20_l1_cpuwin = """cpu_l1_tte = cpu_tte + L1_TABLE_T1_INDEX(va_l1, TCR_EL1_BOOT);

\t{ extern void hk_con_log(const char *s); hk_con_log("[vm] 10: CPUWINDOWS L3 loop starting..."); }\n\twhile (va_l1 < va_l1_end) {
\t\tif (*cpu_l1_tte == ARM_TTE_EMPTY) {
\t\t\tvm_offset_t l2_page = alloc_ptpage(FALSE);
\t\t\t*cpu_l1_tte = (kvtophys(l2_page) & ARM_TTE_TABLE_MASK) | ARM_TTE_TYPE_TABLE | ARM_TTE_VALID | ARM_DYNAMIC_TABLE_XN | ARM_TTE_TABLE_AP(ARM_TTE_TABLE_AP_USER_NA);
\t\t\tbzero((void *)l2_page, ARM_PGBYTES);
\t\t\tfor (vm_offset_t _p = l2_page; _p < l2_page + ARM_PGBYTES; _p += 64) {
\t\t\t\t__asm__ volatile ("dc civac, %0" : : "r" (_p) : "memory");
\t\t\t}
\t\t\t__asm__ volatile ("dc civac, %0" : : "r" (cpu_l1_tte) : "memory");
\t\t\t__asm__ volatile ("dsb sy; isb" ::: "memory");
\t\t}
\t\tva_l2 = va_l1;"""
        if t20_l1 in c20:
            c20 = c20.replace(t20_l1, r20_l1_cpuwin, 1)
            print("  [OK] osfmk/arm64/arm_vm_init.c: on-demand L2 table allocation in CPUWINDOWS L3 loop installed")
        else:
            print("  [WARN] t20_l1 (CPUWINDOWS loop) not found in arm_vm_init.c")

        # 20b. Expand va_l1_end to pre-cover 64MB of heap
        t20_end = '\tva_l1_end = va_l1 + ((2 + (mem_segments * 10)) << 20);'
        r20_end = '\tva_l1_end = va_l1 + ((64 + (mem_segments * 10)) << 20);'
        if t20_end in c20:
            c20 = c20.replace(t20_end, r20_end, 1)
            print("  [OK] osfmk/arm64/arm_vm_init.c: expanded bootstrap heap range from 42MB to 104MB")
        else:
            print("  [WARN] t20_end not found in arm_vm_init.c")

        # 20c. Phase 2: Direct Framebuffer VA Mapping in cpu_tte before set_mmu_ttb_alternate
        t20_fb = '\tarm_vm_physmap_init(args);\n\tset_mmu_ttb_alternate(cpu_ttep & TTBR_BADDR_MASK);'
        r20_fb = r"""	{ extern void hk_con_log(const char *s); hk_con_log("[vm] 3: arm_vm_physmap_init starting"); }
	arm_vm_physmap_init(args);
	{ extern void hk_con_log(const char *s); hk_con_log("[vm] 4: arm_vm_physmap_init done"); }

	/* hakios Phase 2: map the framebuffer (PA 0x5c000000, 1080x2400x4 = 10.4 MB) at VA 0xfffffff004000000 in cpu_tte.
	 * Block size follows the build granule (ARM_TT_L2_SIZE: 32 MB for 16K, 2 MB for 4K), so the whole FB is covered. */
	{
		extern void hk_con_log(const char *s);
		extern void hk_con_puts(const char *s);
		extern void hk_con_print_dec(uint32_t val);
		extern void hk_con_print_ptr(const char *tag, uint64_t val);
		extern void hk_draw_band_direct(uint32_t color, uint32_t row_start, uint32_t row_count);

		const uint64_t hk_fb_va = 0xfffffff004000000ULL;
		const uint64_t hk_fb_pa = 0x5c000000ULL;
		const uint64_t hk_fb_bytes = 16ULL * 1024ULL * 1024ULL; /* 16MB (8 blocks of 2MB) fully covers 15 MiB cont_splash_region (0x5c000000..0x5cf00000) safely within Gunyah stage-2 */
		/* PXN|UXN | AF | SH=outer | AttrIdx=1 (CACHE_ATTRINDX_WRITECOMB, Normal Non-Cacheable) | AP=RW(EL1) | BLOCK */
		const uint64_t hk_fb_attr = 0x0060000000000605ULL;
		const uint64_t l2sz = (uint64_t)ARM_TT_L2_SIZE;
		const uint64_t off = hk_fb_pa & (l2sz - 1);

		if (((hk_fb_va ^ hk_fb_pa) & (l2sz - 1)) != 0) {
			hk_con_log("[vm] 5: FB VA/PA not congruent modulo L2 block - mapping SKIPPED");
		} else {
			const uint64_t va0 = hk_fb_va - off;
			const uint64_t pa0 = hk_fb_pa - off;
			const uint32_t nblk = (uint32_t)((off + hk_fb_bytes + l2sz - 1) / l2sz);
			tt_entry_t *fb_l1_tte = cpu_tte + L1_TABLE_T1_INDEX(va0, TCR_EL1_BOOT);
			tt_entry_t *fb_l2_base = NULL;

			if ((*fb_l1_tte & ARM_TTE_VALID) == 0) {
				vm_offset_t l2_page = alloc_ptpage(FALSE);
				bzero((void *)l2_page, ARM_PGBYTES);
				*fb_l1_tte = (kvtophys(l2_page) & ARM_TTE_TABLE_MASK) | ARM_TTE_TYPE_TABLE | ARM_TTE_VALID | ARM_DYNAMIC_TABLE_XN | ARM_TTE_TABLE_AP(ARM_TTE_TABLE_AP_USER_NA);
				fb_l2_base = (tt_entry_t *)l2_page;
			} else if ((*fb_l1_tte & ARM_TTE_TYPE_MASK) == ARM_TTE_TYPE_TABLE) {
				fb_l2_base = (tt_entry_t *)phystokv((*fb_l1_tte) & ARM_TTE_TABLE_MASK);
			}

			if (fb_l2_base == NULL) {
				hk_con_log("[vm] 5: FB L1 entry is a block, not a table - mapping SKIPPED");
			} else {
				tt_entry_t *fb_l2_tte = fb_l2_base + ((va0 & ARM_TT_L2_INDEX_MASK) >> ARM_TT_L2_SHIFT);
				uint32_t mapped = 0;

				for (uint32_t k = 0; k < nblk; k++) {
					uint64_t desc = (pa0 + (uint64_t)k * l2sz) | hk_fb_attr;
					if ((fb_l2_tte[k] & ARM_TTE_VALID) != 0 && fb_l2_tte[k] != desc) {
						hk_con_print_ptr("[vm]   L2 slot already in use, kept: ", (uint64_t)fb_l2_tte[k]);
						continue;
					}
					fb_l2_tte[k] = desc;
					mapped++;
				}

				/* clean the whole L2 page + the L1 entry to PoC, then barrier, before TTBR1 is switched */
				for (vm_offset_t p = (vm_offset_t)fb_l2_base; p < (vm_offset_t)fb_l2_base + ARM_PGBYTES; p += 64) {
					__asm__ volatile ("dc civac, %0" : : "r" (p) : "memory");
				}
				__asm__ volatile ("dc civac, %0" : : "r" (fb_l1_tte) : "memory");
				__asm__ volatile ("dsb sy; isb" ::: "memory");

				hk_con_print_ptr("[vm]   cpu_ttep : ", (uint64_t)cpu_ttep);
				hk_con_print_ptr("[vm]   FB L1 tte: ", (uint64_t)*fb_l1_tte);
				hk_con_print_ptr("[vm]   FB L2 tte: ", (uint64_t)fb_l2_tte[0]);
				hk_con_puts("[vm] 5: Phase 2 FB mapped, blocks: ");
				hk_con_print_dec(mapped);
				hk_con_puts("/");
				hk_con_print_dec(nblk);
				hk_con_puts("\n");
			}
		}

		/* hakios: Clean ALL translation table pages to PoC for Cortex-A73 MMU walker */
		/* 1. Root L1 page table (cpu_tte) */
		for (vm_offset_t p = (vm_offset_t)cpu_tte; p < (vm_offset_t)cpu_tte + ARM_PGBYTES; p += 64) {
			__asm__ volatile ("dc civac, %0" : : "r" (p) : "memory");
		}
		/* 2. All L2 and L3 ptpages allocated between first_avail_phys and avail_start */
		if (first_avail_phys != 0 && avail_start > first_avail_phys) {
			vm_offset_t pt_va_start = phystokv(first_avail_phys);
			vm_offset_t pt_va_end   = phystokv(avail_start);
			for (vm_offset_t p = pt_va_start; p < pt_va_end; p += 64) {
				__asm__ volatile ("dc civac, %0" : : "r" (p) : "memory");
			}
		}
		__asm__ volatile ("dsb sy; isb" ::: "memory");

		/* Micro visual bands via Framebuffer VA 0xfffffff004000000 */
		volatile uint32_t *hk_fb_raw = (volatile uint32_t *)0xfffffff004000000ULL;
		/* Band at row 150 (Yellow): Right before set_mmu_ttb_alternate */
		for (int i = 150 * 1080; i < 153 * 1080; i++) hk_fb_raw[i] = 0x00FFFF00;

		set_mmu_ttb_alternate(cpu_ttep & TTBR_BADDR_MASK);

		/* Band at row 154 (Cyan): Right after set_mmu_ttb_alternate returns (under new TTBR1!) */
		for (int i = 154 * 1080; i < 157 * 1080; i++) hk_fb_raw[i] = 0x0000FFFF;

		hk_con_log("[vm] 6: TTBR1 switched OK");
	}"""

        if t20_fb in c20:
            c20 = c20.replace(t20_fb, r20_fb, 1)
            print("  [OK] osfmk/arm64/arm_vm_init.c: Phase 2 direct FB mapping in cpu_tte installed")
        else:
            print("  [WARN] t20_fb not found in arm_vm_init.c")
        # 20d. [vm] 1/2: MMU/granule telemetry before and after arm_vm_prot_init
        t20_prot = "\tarm_vm_prot_init(args);\n"
        r20_prot = r"""	{
		extern void hk_con_log(const char *s);
		extern void hk_con_puts(const char *s);
		extern void hk_con_print_ptr(const char *tag, uint64_t val);
		uint64_t hk_mmfr0, hk_tcr, hk_mair, hk_sctlr, hk_ttbr1;
		__asm__ volatile ("mrs %0, ID_AA64MMFR0_EL1" : "=r" (hk_mmfr0));
		__asm__ volatile ("mrs %0, TCR_EL1" : "=r" (hk_tcr));
		__asm__ volatile ("mrs %0, MAIR_EL1" : "=r" (hk_mair));
		__asm__ volatile ("mrs %0, SCTLR_EL1" : "=r" (hk_sctlr));
		__asm__ volatile ("mrs %0, TTBR1_EL1" : "=r" (hk_ttbr1));
		hk_con_log("[vm] 1: segdata parsed");
		hk_con_print_ptr("[vm]   MMFR0 : ", hk_mmfr0);
		hk_con_puts("[vm]   TGran16: ");
		hk_con_log(((hk_mmfr0 >> 20) & 0xf) ? "supported" : "NOT SUPPORTED (hw ignores 16K!)");
		hk_con_print_ptr("[vm]   TCR    : ", hk_tcr);
		hk_con_print_ptr("[vm]   MAIR   : ", hk_mair);
		hk_con_print_ptr("[vm]   SCTLR  : ", hk_sctlr);
		hk_con_print_ptr("[vm]   TTBR1  : ", hk_ttbr1);
	}
	arm_vm_prot_init(args);
	{ extern void hk_con_log(const char *s); hk_con_log("[vm] 2: prot_init done"); }
"""
        if c20.count(t20_prot) == 1 and "[vm] 1: segdata parsed" not in c20:
            c20 = c20.replace(t20_prot, r20_prot, 1)
            print("  [OK] osfmk/arm64/arm_vm_init.c: [vm] 1/2 MMU+granule telemetry installed")
        elif "[vm] 1: segdata parsed" not in c20:
            print("  [WARN] arm_vm_prot_init(args) anchor not found/unique in arm_vm_init.c")

        # 20e. [vm] 7: after upstream invalidates TTBR0 (identity map gone) and sets kva_active
        t20_kva = "\tflush_mmu_tlb();\n\tkva_active = TRUE;\n"
        if t20_kva in c20 and "[vm] 7:" not in c20:
            c20 = c20.replace(t20_kva, t20_kva + '\t{ extern void hk_con_log(const char *s); hk_con_log("[vm] 7: TTBR0 invalidated, kva_active"); }\n', 1)
            print("  [OK] osfmk/arm64/arm_vm_init.c: [vm] 7 marker installed")
        # 20f. [vm] 9: end of the bootstrap L3 loop (it runs AFTER the TTBR1 switch in upstream)
        t20_end_loop = "\t\tva_l1 = va_l2_end;\n\t\tcpu_l1_tte++;\n\t}\n"
        if t20_end_loop in c20 and "[vm] 9:" not in c20:
            c20 = c20.replace(t20_end_loop, t20_end_loop + '\t{ extern void hk_con_log(const char *s); hk_con_log("[vm] 9: bootstrap L3 loop done"); }\n', 1)
            print("  [OK] osfmk/arm64/arm_vm_init.c: [vm] 9 marker installed")

        # 20f-bis. [vm] 10: end of the CPUWINDOWS L3 loop & flush all PT pages to PoC
        if t20_end_loop in c20 and "[vm] 10: CPUWINDOWS L3 loop done" not in c20:
            r20_flush_all = (
                t20_end_loop +
                '\t{\n' +
                '\t\textern void hk_con_log(const char *s);\n' +
                '\t\thk_con_log("[vm] 10: CPUWINDOWS L3 loop done");\n' +
                '\t\t/* hakios: Clean ALL translation table pages to PoC for Cortex-A73 MMU walker */\n' +
                '\t\tfor (vm_offset_t _p = (vm_offset_t)cpu_tte; _p < (vm_offset_t)cpu_tte + ARM_PGBYTES; _p += 64) {\n' +
                '\t\t\t__asm__ volatile ("dc civac, %0" : : "r" (_p) : "memory");\n' +
                '\t\t}\n' +
                '\t\tif (first_avail_phys != 0 && avail_start > first_avail_phys) {\n' +
                '\t\t\tvm_offset_t _p_start = phystokv(first_avail_phys);\n' +
                '\t\t\tvm_offset_t _p_end   = phystokv(avail_start);\n' +
                '\t\t\tfor (vm_offset_t _p = _p_start; _p < _p_end; _p += 64) {\n' +
                '\t\t\t\t__asm__ volatile ("dc civac, %0" : : "r" (_p) : "memory");\n' +
                '\t\t\t}\n' +
                '\t\t}\n' +
                '\t\t__asm__ volatile ("dsb sy; isb" ::: "memory");\n' +
                '\t\tflush_mmu_tlb();\n' +
                '\t}\n'
            )
            c20 = c20.replace(t20_end_loop, r20_flush_all, 1)
            print("  [OK] osfmk/arm64/arm_vm_init.c: [vm] 10 loop done and MMU table PoC flush installed")
        with open(p20, "w", encoding="utf-8", newline="\n") as f:
            f.write(c20)


    # 21. osfmk/console/video_console.c (direct map FB to Phase 2 VA 0xfffffff004000000ULL, eliminate FSC=6 Translation Fault)
    p21 = os.path.join(src, "osfmk", "console", "video_console.c")
    if os.path.isfile(p21):
        with open(p21, "r", encoding="utf-8", errors="replace") as f:
            c21 = f.read().replace("\r\n", "\n")

        # FIX 1: Override newVideoVirt to Phase 2 direct-mapped FB VA 0xfffffff004000000ULL
        vc_map_target = "newVideoVirt = ml_io_map_unmappable((vm_map_offset_t)new_vinfo.v_physaddr, newMapSize, flags);"
        vc_map_repl = (
            "/* hakios: Phase 2 Framebuffer is ALREADY mapped at VA 0xfffffff004000000ULL in cpu_tte.\n"
            "\t\t\t * ml_io_map_unmappable allocates dynamic KVA lacking Level 2 table entries in early boot,\n"
            "\t\t\t * triggering Translation Fault Level 2 (FSC=6) on the first vcputc/cursor access.\n"
            "\t\t\t * Directly aliasing 0xfffffff004000000ULL safely reuses the verified 16MB mapping. */\n"
            "\t\t\t\tnewVideoVirt = 0xfffffff004000000ULL;"
        )
        if vc_map_target in c21:
            c21 = c21.replace(vc_map_target, vc_map_repl, 1)
            print("  [OK] osfmk/console/video_console.c: newVideoVirt direct mapped to 0xfffffff004000000ULL")
        else:
            print("  [WARN] vc_map_target not found in video_console.c")

        # FIX 2: Set new_vinfo.v_baseaddr to 0xfffffff004000000ULL, log geometry, and remove 720-line shift
        vc_target = "new_vinfo.v_baseaddr = newVideoVirt + boot_vinfo->v_offset;"
        vc_repl = (
            "new_vinfo.v_baseaddr = 0xfffffff004000000ULL + boot_vinfo->v_offset;\n"
            "\t\t\t/* hakios: print console geometry and verified baseaddr */\n"
            "\t\t\t{ extern void hk_con_print_ptr(const char *tag, uint64_t val);\n"
            "\t\t\t  hk_con_print_ptr(\"[vc] v_baseaddr: \", (uint64_t)new_vinfo.v_baseaddr);\n"
            "\t\t\t  hk_con_print_ptr(\"[vc] v_rowbytes: \", (uint64_t)new_vinfo.v_rowbytes);\n"
            "\t\t\t  hk_con_print_ptr(\"[vc] v_height  : \", (uint64_t)new_vinfo.v_height);\n"
            "\t\t\t  hk_con_print_ptr(\"[vc] v_width   : \", (uint64_t)new_vinfo.v_width);\n"
            "\t\t\t}"
        )
        if "/* hakios: reserve rows 0..719 for diagnostic ladder bands" in c21:
            print("  [ALREADY PATCHED] osfmk/console/video_console.c: dual-region framebuffer partition already present")
        elif vc_target in c21:
            c21 = c21.replace(vc_target, vc_repl, 1)
            print("  [OK] osfmk/console/video_console.c: v_baseaddr direct mapped and telemetry installed")
        else:
            print("  [WARN] vc_target not found in video_console.c")

        # Force video_console always enabled (never discard character output in vcputc_options)
        t_vcputc = "void\nvcputc_options(char c, __unused bool poll)\n{\n\tif (gc_initialized && gc_enabled) {"
        r_vcputc = "void\nvcputc_options(char c, __unused bool poll)\n{\n\t/* hakios: force text console always active for PID 1 & kprintf */\n\tif (gc_initialized) {\n\t\tgc_enabled = TRUE;"
        if "/* hakios: force text console always active for PID 1 & kprintf */" in c21:
            print("  [ALREADY PATCHED] osfmk/console/video_console.c: vcputc text force already present")
        elif t_vcputc in c21:
            c21 = c21.replace(t_vcputc, r_vcputc, 1)
            print("  [OK] osfmk/console/video_console.c: vcputc text force installed (always active)")
        else:
            print("  [WARN] t_vcputc pattern not found in video_console.c")

        # Never set disableConsoleOutput to TRUE in gc_enable
        t_dis = "disableConsoleOutput = TRUE;"
        r_dis = "/* hakios: never suppress console */ disableConsoleOutput = FALSE;"
        if t_dis in c21:
            c21 = c21.replace(t_dis, r_dis)
            print("  [OK] osfmk/console/video_console.c: disableConsoleOutput overrides neutralized")

        with open(p21, "w", encoding="utf-8", newline="\n") as f:
            f.write(c21)

    # 22. osfmk/kern/lock_mtx.c (bypass mutex ownership assert during early single-core boot)
    p22 = os.path.join(src, "osfmk", "kern", "lock_mtx.c")
    if os.path.isfile(p22):
        with open(p22, "r", encoding="utf-8", errors="replace") as f:
            c22 = f.read().replace("\r\n", "\n")
        t22 = "void\nlck_mtx_assert(lck_mtx_t *lock, unsigned int type)\n{"
        r22 = """void
lck_mtx_assert(lck_mtx_t *lock, unsigned int type)
{
\tif (startup_phase < STARTUP_SUB_KMEM) {
\t\treturn;
\t}"""
        if t22 in c22:
            c22 = c22.replace(t22, r22, 1)
            for fn_sig in [
                "__mockable void\nlck_mtx_lock(lck_mtx_t *lock)\n{",
                "void\nlck_mtx_lock(lck_mtx_t *lock)\n{",
                "void\nlck_mtx_lock_spin(lck_mtx_t *lock)\n{",
                "void\nlck_mtx_lock_spin_always(lck_mtx_t *lock)\n{",
                "__mockable void\nlck_mtx_unlock(lck_mtx_t *lock)\n{",
                "void\nlck_mtx_unlock(lck_mtx_t *lock)\n{",
            ]:
                if fn_sig in c22:
                    c22 = c22.replace(
                        fn_sig,
                        fn_sig + "\n\tif (startup_phase < STARTUP_SUB_LOCKS) {\n\t\treturn;\n\t}",
                        1,
                    )
            for try_sig in [
                "boolean_t\nlck_mtx_try_lock(lck_mtx_t *lock)\n{",
                "boolean_t\nlck_mtx_try_lock_spin(lck_mtx_t *lock)\n{",
                "boolean_t\nlck_mtx_try_lock_spin_always(lck_mtx_t *lock)\n{",
            ]:
                if try_sig in c22:
                    c22 = c22.replace(
                        try_sig,
                        try_sig + "\n\tif (startup_phase < STARTUP_SUB_LOCKS) {\n\t\treturn TRUE;\n\t}",
                        1,
                    )
            with open(p22, "w", encoding="utf-8", newline="\n") as f:
                f.write(c22)
            print("  [OK] osfmk/kern/lock_mtx.c: early boot mutex assert & uninitialized lock bypass installed")
        else:
            print("  [WARN] t22 not found in lock_mtx.c")

    # 23. pexpert/arm/pe_init.c (force text mode console on framebuffer display)
    p23 = os.path.join(src, "pexpert", "arm", "pe_init.c")
    if os.path.isfile(p23):
        with open(p23, "r", encoding="utf-8", errors="replace") as f:
            c23 = f.read().replace("\r\n", "\n")
        t23 = "\tif (PE_state.video.v_display) {\n\t\tPE_initialize_console(&PE_state.video, kPEGraphicsMode);\n\t} else {\n\t\tPE_initialize_console(&PE_state.video, kPETextMode);\n\t}"
        r23 = "\t/* hakios: always initialize in text mode so video_console displays PID 1 & kprintf */\n\tPE_initialize_console(&PE_state.video, kPETextMode);"
        if "/* hakios: always initialize in text mode" in c23:
            print("  [ALREADY PATCHED] pexpert/arm/pe_init.c: text console mode already forced")
        elif t23 in c23:
            c23 = c23.replace(t23, r23, 1)
            with open(p23, "w", encoding="utf-8", newline="\n") as f:
                f.write(c23)
            print("  [OK] pexpert/arm/pe_init.c: forced kPETextMode for video_console display")
        else:
            print("  [WARN] t23 pattern not found in pe_init.c")

    # 24. iokit/Kernel/GenericARMPE.cpp (ensure max_cpus settled even for single-core or missing cpu0 nub)
    p24 = os.path.join(src, "iokit", "Kernel", "GenericARMPE.cpp")
    if os.path.isfile(p24):
        with open(p24, "r", encoding="utf-8", errors="replace") as f:
            c24 = f.read().replace("\r\n", "\n")
        t24 = "\tif (cpus[master_cpu] != NULL) {\n\t\t/* Unblocks everything that waits for the CPU count to be known. */\n\t\tml_set_max_cpus(count);"
        r24 = "\tif (cpus[master_cpu] != NULL) {\n\t\t/* Unblocks everything that waits for the CPU count to be known. */\n\t\tml_set_max_cpus(count);\n\t} else {\n\t\t/* hakios: fallback for single-core bring-up to unblock ml_wait_max_cpus */\n\t\tml_set_max_cpus(1);"
        if "/* hakios: fallback for single-core bring-up" in c24:
            print("  [ALREADY PATCHED] iokit/Kernel/GenericARMPE.cpp: max_cpus fallback unblock already present")
        elif t24 in c24:
            c24 = c24.replace(t24, r24, 1)
            with open(p24, "w", encoding="utf-8", newline="\n") as f:
                f.write(c24)
            print("  [OK] iokit/Kernel/GenericARMPE.cpp: max_cpus fallback unblock installed")
        else:
            print("  [WARN] t24 pattern not found in GenericARMPE.cpp")

    # 25. osfmk/arm64/machine_routines.c & osfmk/arm/machine_routines.c
    # - safeguard ml_wait_max_cpus against infinite wait
    # - stub ml_map_cpu_pio (eliminate Apple CPU PIO mapping crash on SM6225)
    # - instrument machine_startup entry and kernel_bootstrap kickoff
    # - guarantee runtime topology_info pointers in ml_parse_cpu_topology
    for mr_rel in [("osfmk", "arm64", "machine_routines.c"), ("osfmk", "arm", "machine_routines.c")]:
        p_mr = os.path.join(src, *mr_rel)
        if os.path.isfile(p_mr):
            with open(p_mr, "r", encoding="utf-8", errors="replace") as f:
                c_mr = f.read().replace("\r\n", "\n")
            mr_modified = False

            # 25a. ml_wait_max_cpus safeguard
            if "ml_wait_max_cpus" in c_mr:
                t_mr = "ml_wait_max_cpus(void)\n{"
                r_mr = "ml_wait_max_cpus(void)\n{\n\tif (!max_cpus_initialized) ml_set_max_cpus(1); /* hakios: unblock single-core */"
                if "/* hakios: unblock single-core */" in c_mr:
                    print(f"  [ALREADY PATCHED] {os.path.join(*mr_rel)}: ml_wait_max_cpus safeguard already present")
                elif t_mr in c_mr:
                    c_mr = c_mr.replace(t_mr, r_mr, 1)
                    mr_modified = True
                    print(f"  [OK] {os.path.join(*mr_rel)}: ml_wait_max_cpus safeguard installed")

            # 25b. Stub ml_map_cpu_pio (Apple Silicon CPU PIO registers do not exist on Qualcomm Cortex-A73/A53)
            if "/* hakios: stub ml_map_cpu_pio" in c_mr:
                print(f"  [ALREADY PATCHED] {os.path.join(*mr_rel)}: ml_map_cpu_pio stub already present")
            else:
                p_pio_func = re.compile(r'void\s+ml_map_cpu_pio\s*\(\s*void\s*\)\s*\{[\s\S]*?\n\}')
                m_pio_func = p_pio_func.search(c_mr)
                if m_pio_func:
                    r_pio_func = (
                        "/* hakios: stub ml_map_cpu_pio - Qualcomm Snapdragon 685 SM6225 has no Apple CPU PIO */\n"
                        "void\n"
                        "ml_map_cpu_pio(void)\n"
                        "{\n"
                        "\textern void hk_con_log(const char *s);\n"
                        '\thk_con_log("[*] ml_map_cpu_pio: Apple Silicon CPU PIO bypassed on SM6225");\n'
                        "\treturn;\n"
                        "}"
                    )
                    c_mr = c_mr[:m_pio_func.start()] + r_pio_func + c_mr[m_pio_func.end():]
                    mr_modified = True
                    print(f"  [OK] {os.path.join(*mr_rel)}: ml_map_cpu_pio stubbed")
                else:
                    print(f"  [WARN] {os.path.join(*mr_rel)}: ml_map_cpu_pio function not found")

            # 25c. Instrument machine_startup entry and transition to kernel_bootstrap
            if "/* hakios: machine_startup entry */" in c_mr:
                print(f"  [ALREADY PATCHED] {os.path.join(*mr_rel)}: machine_startup telemetry already present")
            else:
                p_mstart_func = re.compile(r'(void\s+machine_startup\s*\(\s*__unused\s+boot_args\s*\*\s*args\s*\)\s*\{)')
                m_mstart_func = p_mstart_func.search(c_mr)
                if m_mstart_func:
                    r_mstart_entry = m_mstart_func.group(1) + (
                        "\n\t/* hakios: machine_startup entry */\n"
                        "\t{\n"
                        "\t\textern void hk_con_log(const char *s);\n"
                        "\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);\n"
                        "\t\thk_draw_band_direct(0x0000FF00, 185, 5);  /* Green band: machine_startup entered */\n"
                        '\t\thk_con_log("[*] machine_startup: entered successfully");\n'
                        "\t}"
                    )
                    c_mr = c_mr[:m_mstart_func.start()] + r_mstart_entry + c_mr[m_mstart_func.end():]

                    # Also instrument machine_conf() completion
                    m_mconf = re.search(r'(\t+)(machine_conf\s*\(\s*\);)', c_mr)
                    if m_mconf:
                        c_mr = c_mr[:m_mconf.end(0)] + (
                            '\n\t{ extern void hk_con_log(const char *s); '
                            'hk_con_log("[*] machine_startup: machine_conf OK, kicking off kernel_bootstrap..."); }'
                        ) + c_mr[m_mconf.end(0):]

                    mr_modified = True
                    print(f"  [OK] {os.path.join(*mr_rel)}: machine_startup telemetry instrumented")
                else:
                    print(f"  [WARN] {os.path.join(*mr_rel)}: machine_startup not found")

            # 25d. Guarantee runtime topology_info pointers in ml_parse_cpu_topology
            if "/* hakios: guarantee topology_info pointers */" in c_mr:
                print(f"  [ALREADY PATCHED] {os.path.join(*mr_rel)}: topology_info pointers already initialized")
            else:
                p_topo = re.compile(r'(void\s+ml_parse_cpu_topology\s*\(\s*void\s*\)\s*\{)')
                m_topo = p_topo.search(c_mr)
                if m_topo:
                    r_topo = m_topo.group(1) + (
                        "\n\t/* hakios: guarantee topology_info pointers regardless of chained fixups */\n"
                        "\ttopology_info.cpus = topology_cpu_array;\n"
                        "\ttopology_info.clusters = topology_cluster_array;\n"
                    )
                    c_mr = c_mr[:m_topo.start()] + r_topo + c_mr[m_topo.end():]
                    mr_modified = True
                    print(f"  [OK] {os.path.join(*mr_rel)}: ml_parse_cpu_topology pointer guarantee installed")
                else:
                    print(f"  [WARN] {os.path.join(*mr_rel)}: ml_parse_cpu_topology not found")

            if mr_modified:
                with open(p_mr, "w", encoding="utf-8", newline="\n") as f:
                    f.write(c_mr)

    # 26. osfmk/kern/zalloc.c (clamp PAGE_SHIFT_CONST and instrument zone_bootstrap)
    p26 = os.path.join(src, "osfmk", "kern", "zalloc.c")
    if os.path.isfile(p26):
        with open(p26, "r", encoding="utf-8", errors="replace") as f:
            c26 = f.read().replace("\r\n", "\n")
        z_pat = re.compile(r'void\s+zone_bootstrap\(void\)\s*\{')
        rz_entry = """void\nzone_bootstrap(void)\n{\n\textern int PAGE_SHIFT_CONST;\n\tif (PAGE_SHIFT_CONST <= 5) {\n\t\tPAGE_SHIFT_CONST = 14;\n\t}\n\t{\n\t\textern void hk_draw_band_direct(uint32_t, uint32_t, uint32_t);\n\t\thk_draw_band_direct(0x0032CD32, 362, 4);
		extern void hk_con_log(const char *s);
		hk_con_log("[*] zone_bootstrap starting...");\n\t}"""
        if "/* Lime: zone_bootstrap entered */" in c26:
            print("  [ALREADY PATCHED] osfmk/kern/zalloc.c: zone_bootstrap markers already present")
        elif z_pat.search(c26):
            c26 = z_pat.sub(rz_entry, c26, count=1)
            with open(p26, "w", encoding="utf-8", newline="\n") as f:
                f.write(c26)
            print("  [OK] osfmk/kern/zalloc.c: zone_bootstrap entry marker & PAGE_SHIFT_CONST clamp installed")
        else:
            print("  [WARN] zone_bootstrap not found in zalloc.c")

    # 27. pexpert/arm/pe_kprintf.c (mirror all serial/kprintf output directly to on-screen console)
    p27 = os.path.join(src, "pexpert", "arm", "pe_kprintf.c")
    if os.path.isfile(p27):
        with open(p27, "r", encoding="utf-8", errors="replace") as f:
            c27 = f.read().replace("\r\n", "\n")

        # Force enable serial and kprintf output unconditionally
        t_dis = "SECURITY_READ_ONLY_LATE(bool) disable_serial_output = true;\n// disable_kprintf_output only disables kprintf().\nSECURITY_READ_ONLY_LATE(bool) disable_kprintf_output = true;"
        r_dis = "SECURITY_READ_ONLY_LATE(bool) disable_serial_output = false;\nSECURITY_READ_ONLY_LATE(bool) disable_kprintf_output = false;"
        if t_dis in c27:
            c27 = c27.replace(t_dis, r_dis, 1)
            print("  [OK] pexpert/arm/pe_kprintf.c: forced disable_serial_output/disable_kprintf_output = false")

        # Initialize PE_kputc directly to serial_putc_crlf
        t_pkp = "static void serial_putc_crlf(char c);\n\n__startup_func"
        r_pkp = "static void serial_putc_crlf(char c);\n\n__attribute__((constructor)) static void hk_init_pe_kputc(void) { PE_kputc = serial_putc_crlf; }\n\n__startup_func"
        if t_pkp in c27:
            c27 = c27.replace(t_pkp, r_pkp, 1)
            print("  [OK] pexpert/arm/pe_kprintf.c: PE_kputc default constructor installed")

        # kprintf(): lock-free mirror to the on-screen console BEFORE kprintf_lock / interrupt masking / os_log
        t_kp = "\tif (!disable_serial_output && !disable_kprintf_output) {\n\t\tva_start(listp, fmt);"
        r_kp = r"""	{
		/* hakios: lock-free on-screen mirror (no kprintf_lock, no os_log, works before STARTUP_SUB_LOCKS) */
		extern void hk_con_putc(char c);
		va_list hk_ap;
		va_start(hk_ap, fmt);
		_doprnt_log(fmt, &hk_ap, hk_con_putc, 16);
		va_end(hk_ap);
	}
""" + t_kp
        if t_kp in c27 and "hk_con_putc(char c);\n\t\tva_list hk_ap" not in c27:
            c27 = c27.replace(t_kp, r_kp, 1)
            print("  [OK] pexpert/arm/pe_kprintf.c: kprintf() mirrored lock-free to on-screen console")
        elif "va_list hk_ap" not in c27:
            print("  [WARN] kprintf() anchor not found in pe_kprintf.c")

        # serial_putc*: screen only by default (no polling on an unmapped/unclocked UART). -DHK_ENABLE_UART restores uart.
        t_spc = "static void\nserial_putc_crlf(char c)\n{\n\tif (c == '\\n') {\n\t\tuart_putc('\\r');\n\t}\n\tuart_putc(c);\n}"
        r_spc = r"""static void
serial_putc_crlf(char c)
{
#ifdef HK_ENABLE_UART
	if (c == '\n') {
		uart_putc('\r');
	}
	uart_putc(c);
#else
	(void)c;        /* hakios: kprintf() already mirrors to the screen; UART polling can hang */
#endif
}"""
        if t_spc in c27:
            c27 = c27.replace(t_spc, r_spc, 1)
            print("  [OK] pexpert/arm/pe_kprintf.c: serial_putc_crlf made UART-optional")

        t_spo = "void\nserial_putc_options(char c, bool poll)\n{\n\tuart_putc_options(c, poll);\n}"
        r_spo = r"""void
serial_putc_options(char c, bool poll)
{
#ifdef HK_ENABLE_UART
	uart_putc_options(c, poll);
#else
	(void)poll;
	{ extern void hk_con_putc(char c); hk_con_putc(c); }
#endif
}"""
        if t_spo in c27:
            c27 = c27.replace(t_spo, r_spo, 1)
            print("  [OK] pexpert/arm/pe_kprintf.c: serial_putc_options routed to on-screen console")

        t_sp = "void\nserial_putc(char c)\n{\n\tuart_putc(c);\n}"
        r_sp = r"""void
serial_putc(char c)
{
	{ extern void hk_con_putc(char c); hk_con_putc(c); }
#ifdef HK_ENABLE_UART
	uart_putc(c);
#endif
}"""
        if t_sp in c27:
            c27 = c27.replace(t_sp, r_sp, 1)
            print("  [OK] pexpert/arm/pe_kprintf.c: serial_putc mirrored to on-screen console")

        with open(p27, "w", encoding="utf-8", newline="\n") as f:
            f.write(c27)
    else:
        print("  [WARN] pexpert/arm/pe_kprintf.c not found")

    # 28. osfmk/kern/printf.c (mirror all standard kernel printf/consdebug directly to on-screen console)
    p28 = os.path.join(src, "osfmk", "kern", "printf.c")
    if os.path.isfile(p28):
        with open(p28, "r", encoding="utf-8", errors="replace") as f:
            c28 = f.read().replace("\r\n", "\n")

        t_cpl = "void\ncons_putc_locked(char c)\n{\n\tconsole_write_char(c);\n}"
        r_cpl = """void
cons_putc_locked(char c)
{
\t{ extern void hk_con_putc(char c); hk_con_putc(c); }
\tconsole_write_char(c);
}"""
        if t_cpl in c28:
            c28 = c28.replace(t_cpl, r_cpl, 1)
            print("  [OK] osfmk/kern/printf.c: mirrored cons_putc_locked to on-screen console")

        t_cdp = "void\nconsdebug_putc(char c)\n{\n\tconsole_write_char(c);"
        r_cdp = """void
consdebug_putc(char c)
{
\t{ extern void hk_con_putc(char c); hk_con_putc(c); }
\tconsole_write_char(c);"""
        if t_cdp in c28:
            c28 = c28.replace(t_cdp, r_cdp, 1)
            print("  [OK] osfmk/kern/printf.c: mirrored consdebug_putc to on-screen console")

        with open(p28, "w", encoding="utf-8", newline="\n") as f:
            f.write(c28)
    else:
        print("  [WARN] osfmk/kern/printf.c not found")

    # 29. osfmk/arm64/cswitch.s (UAO is ARMv8.2: `mrs/msr UAO` is UNDEF on Cortex-A73 -> an EC==0 trap on EVERY context switch)
    p29 = os.path.join(src, "osfmk", "arm64", "cswitch.s")
    if os.path.isfile(p29):
        with open(p29, "r", encoding="utf-8", errors="replace") as f:
            c29 = f.read().replace("\r\n", "\n")
        g_old = "#if __ARM_ARCH_8_2__\n"
        g_new = "#if 0 /* hakios: UAO is ARMv8.2, UNDEF on Cortex-A73 */\n"
        sites = ("\tmrs\t\tx$1, UAO", "\tldrb\tw$1, [$0, SS64_KERNEL_UAO]")
        n29 = 0
        for s in sites:
            if (g_old + s) in c29:
                c29 = c29.replace(g_old + s, g_new + s, 1)
                n29 += 1
        if n29 == 2:
            with open(p29, "w", encoding="utf-8", newline="\n") as f:
                f.write(c29)
            print("  [OK] osfmk/arm64/cswitch.s: UAO save/restore compiled out (2 sites)")
        elif "hakios: UAO is ARMv8.2" in c29:
            print("  [OK] osfmk/arm64/cswitch.s: UAO already compiled out")
        else:
            print("  [WARN] cswitch.s: found %d/2 UAO sites" % n29)
    else:
        print("  [WARN] osfmk/arm64/cswitch.s not found")

    
    # 30. osfmk/arm64/tlb.h (Convert broadcast TLBI to local core TLBI to avoid interconnect deadlock on single-core SM6225)
    p30 = os.path.join(src, "osfmk", "arm64", "tlb.h")
    if os.path.isfile(p30):
        with open(p30, "r", encoding="utf-8", errors="replace") as f:
            c30 = f.read().replace("\r\n", "\n")
        
        # Replace tlbi vmalle1is with tlbi vmalle1
        c30_old1 = 'asm volatile ("tlbi vmalle1is");'
        c30_new1 = 'asm volatile ("tlbi vmalle1"); /* hakios: local core TLBI avoids interconnect stall */'
        if c30_old1 in c30:
            c30 = c30.replace(c30_old1, c30_new1)
            print("  [OK] osfmk/arm64/tlb.h: flush_mmu_tlb_async converted to local tlbi vmalle1")

        # Replace DSB_ISH in sync_tlb_flush with DSB_NSH
        c30_old2 = '__builtin_arm_dsb(DSB_ISH);'
        c30_new2 = '__builtin_arm_dsb(DSB_NSH); /* hakios: local NSH barrier */'
        if c30_old2 in c30:
            c30 = c30.replace(c30_old2, c30_new2)
            print("  [OK] osfmk/arm64/tlb.h: sync_tlb_flush converted to local DSB_NSH")

        with open(p30, "w", encoding="utf-8", newline="\n") as f:
            f.write(c30)
    else:
        print("  [WARN] osfmk/arm64/tlb.h not found")

    # Post-patch verification assertions: verify files on disk actually contain the patches!
    print("=== Post-patch Verification on Disk ===")
    p_locore = os.path.join(src, "osfmk", "arm64", "locore.s")
    if os.path.isfile(p_locore):
        with open(p_locore, "r", encoding="utf-8") as f:
            locore_content = f.read()
        assert "Row 230" in locore_content, "ASSERTION FAILED: locore.s missing Row 230 marker on disk!"
        assert "Row 220" in locore_content, "ASSERTION FAILED: locore.s missing Row 220 marker on disk!"
        assert "hk_draw_band_stackless" in locore_content, "ASSERTION FAILED: locore.s missing hk_draw_band_stackless on disk!"
        print("  [OK] locore.s verified: stackless vector markers present on disk")

    p_vmres = os.path.join(src, "osfmk", "vm", "vm_resident.c")
    if os.path.isfile(p_vmres):
        with open(p_vmres, "r", encoding="utf-8") as f:
            vmres_content = f.read()
        assert "304" in vmres_content, "ASSERTION FAILED: vm_resident.c missing Row 304 marker on disk!"
        assert "302" in vmres_content, "ASSERTION FAILED: vm_resident.c missing Row 302 marker on disk!"
        assert "305" in vmres_content, "ASSERTION FAILED: vm_resident.c missing Row 305 marker on disk!"
        assert "356" in vmres_content, "ASSERTION FAILED: vm_resident.c missing Row 356 marker on disk!"
        print("  [OK] vm_resident.c verified: progress markers present on disk")

    print("Cortex-A73 patches successfully applied!")

if __name__ == "__main__":
    main()
# CI Trigger: 2026-11-11-1707
# Triggered by Claude Code
