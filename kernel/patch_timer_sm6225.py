#!/usr/bin/env python3
"""
kernel/patch_timer_sm6225.py

HakiOS XNU 12377 (iOS 26 ARM64E) Timer & Clock Subsystem Patch
Target Device: Xiaomi Redmi Note 13 4G (Qualcomm Snapdragon SM6225 Bengal)
Target Cores: Kryo 260 Gold (Cortex-A73) + Silver (Cortex-A53), ARMv8.0-A

This standalone patch script configures and guards the timer subsystem:
1. Fallback from Apple proprietary hardware timer reads (such as S3_4_C15_C10_6 /
   ACNTVCT_EL0 / Apple generic timer sysregs) to standard ARM architected virtual
   counter reads: `isb; mrs x0, cntvct_el0` in machine_routines.c and rtclock.c.
2. Neutralize Apple user_timebase_type to USER_TIMEBASE_SPEC for generic ARM64.
3. Guard absolutetime_to_nanoseconds in pmap_startup (osfmk/vm/vm_resident.c)
   against divide-by-zero / uncalibrated timebase conversions (setting start_ns = 0
   if timebase denominator is 0).
4. Guard absolutetime_to_nanoseconds, nanoseconds_to_absolutetime, and related
   conversion functions in osfmk/arm/rtclock.c when rtclock_sec_divisor == 0.
5. Ensure default CNTFRQ_EL0 frequency is 19200000 (19.2 MHz) for SM6225 Bengal
   in pexpert/arm/pe_identify_machine.c, pe_init.c, and BCM2837.h.
"""

import os
import sys
import re

def patch_vm_resident(src):
    candidates = [
        os.path.join(src, "osfmk", "vm", "vm_resident.c"),
        os.path.join(src, "vm_resident.c"),
    ]
    target_path = None
    for c in candidates:
        if os.path.isfile(c):
            target_path = c
            break

    if not target_path:
        print(f"  [WARN] vm_resident.c not found in {src}")
        return False

    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    rel_path = os.path.relpath(target_path, src).replace("\\", "/")

    if "/* hakios: guard against uncalibrated early timebase divide-by-zero */" in content:
        print(f"  [ALREADY PATCHED] {rel_path}: early timebase guard already installed")
        return True

    content = content.replace("\r\n", "\n")

    # 1. Header check: ensure <kern/clock.h> is included for clock_timebase_info & mach_timebase_info_data_t
    if "<kern/clock.h>" not in content:
        for inc_target in ["#include <mach/clock_types.h>", "#include <mach/mach_types.h>", "#include <vm/pmap.h>"]:
            if inc_target in content:
                content = content.replace(
                    inc_target,
                    f"{inc_target}\n#include <kern/clock.h>",
                    1
                )
                print(f"  [OK] {rel_path}: included <kern/clock.h>")
                break

    # 2. Guard start_ns in pmap_startup
    p_start = re.compile(r'(\tabsolutetime_to_nanoseconds\s*\(\s*(?:mach_absolute_time\(\)|t)\s*,\s*&start_ns\s*\);)')
    m_start = p_start.search(content)
    if m_start:
        inner_call = m_start.group(1).strip()
        repl_start = (
            "\t/* hakios: guard against uncalibrated early timebase divide-by-zero */\n"
            "\t{\n"
            "\t\tmach_timebase_info_data_t _tb = { 0, 0 };\n"
            "\t\tclock_timebase_info(&_tb);\n"
            "\t\tif (_tb.denom == 0) {\n"
            "\t\t\tstart_ns = 0;\n"
            "\t\t} else {\n"
            f"\t\t\t{inner_call}\n"
            "\t\t}\n"
            "\t}"
        )
        content = p_start.sub(lambda m: repl_start, content, count=1)
        print(f"  [OK] {rel_path}: pmap_startup start_ns divide-by-zero guard installed")
    else:
        print(f"  [WARN] {rel_path}: start_ns pattern not found")

    # 3. Guard now_ns in pmap_startup
    p_now = re.compile(
        r'(\tabsolutetime_to_nanoseconds\s*\(\s*mach_absolute_time\(\)\s*,\s*&now_ns\s*\);\s*'
        r'printf\s*\(\s*"pmap_startup\(\) init/release time: %lld microsec\\n",\s*'
        r'\(now_ns - start_ns\) / NSEC_PER_USEC\);)'
    )
    m_now = p_now.search(content)
    if m_now:
        repl_now = (
            "\t/* hakios: guard against uncalibrated timebase in now_ns */\n"
            "\t{\n"
            "\t\tmach_timebase_info_data_t _tb = { 0, 0 };\n"
            "\t\tclock_timebase_info(&_tb);\n"
            "\t\tif (_tb.denom == 0 || start_ns == 0) {\n"
            "\t\t\tnow_ns = 0;\n"
            "\t\t} else {\n"
            "\t\t\tabsolutetime_to_nanoseconds(mach_absolute_time(), &now_ns);\n"
            "\t\t\tprintf(\"pmap_startup() init/release time: %lld microsec\\n\",\n"
            "\t\t\t    (now_ns - start_ns) / NSEC_PER_USEC);\n"
            "\t\t}\n"
            "\t}"
        )
        content = p_now.sub(lambda m: repl_now, content, count=1)
        print(f"  [OK] {rel_path}: pmap_startup now_ns guard installed")
    else:
        print(f"  [WARN] {rel_path}: now_ns pattern not found")

    with open(target_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

    return True

def patch_rtclock(src):
    candidates = [
        os.path.join(src, "osfmk", "arm", "rtclock.c"),
        os.path.join(src, "osfmk", "arm64", "rtclock.c"),
    ]
    found = False
    for target_path in candidates:
        if not os.path.isfile(target_path):
            continue
        found = True
        rel_path = os.path.relpath(target_path, src).replace("\\", "/")

        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if "/* hakios: SM6225 safe non-zero divisor */" in content:
            print(f"  [ALREADY PATCHED] {rel_path}: safe divisor guards already installed")
            continue

        c = content.replace("\r\n", "\n")

        # 1. In timebase_callback: fallback to 19.2 MHz (19200000) if invalid / 0
        p_cb = (
            "\tif (freq->timebase_den < 1 || freq->timebase_den > 4 ||\n"
            "\t    freq->timebase_num < freq->timebase_den) {\n"
            "\t\tpanic(\"rtclock timebase_callback: invalid constant %ld / %ld\",\n"
            "\t\t    freq->timebase_num, freq->timebase_den);\n"
            "\t}"
        )
        r_cb = (
            "\t/* hakios: SM6225 fallback to 19.2 MHz CNTFRQ_EL0 if invalid */\n"
            "\tif (freq->timebase_den < 1 || freq->timebase_den > 4 ||\n"
            "\t    freq->timebase_num < freq->timebase_den) {\n"
            "\t\tfreq->timebase_num = 19200000;\n"
            "\t\tfreq->timebase_den = 1;\n"
            "\t}"
        )
        if p_cb in c:
            c = c.replace(p_cb, r_cb, 1)
            print(f"  [OK] {rel_path}: timebase_callback invalid constant panic replaced with 19.2 MHz fallback")

        # 2. In absolutetime_to_nanoseconds: guard against rtclock_sec_divisor == 0
        p_atn = (
            "void\n"
            "absolutetime_to_nanoseconds(uint64_t   abstime,\n"
            "    uint64_t * result)\n"
            "{\n"
            "\tuint64_t        t64;\n"
        )
        r_atn = (
            "void\n"
            "absolutetime_to_nanoseconds(uint64_t   abstime,\n"
            "    uint64_t * result)\n"
            "{\n"
            "\t/* hakios: SM6225 safe non-zero divisor */\n"
            "\tuint64_t        t64;\n"
            "\tif (rtclock_sec_divisor == 0) {\n"
            "\t\t*result = 0;\n"
            "\t\treturn;\n"
            "\t}\n"
        )
        if p_atn in c:
            c = c.replace(p_atn, r_atn, 1)
            print(f"  [OK] {rel_path}: absolutetime_to_nanoseconds divide-by-zero guard installed")

        # 3. In nanoseconds_to_absolutetime: guard against rtclock_sec_divisor == 0
        p_nta = (
            "void\n"
            "nanoseconds_to_absolutetime(uint64_t   nanosecs,\n"
            "    uint64_t * result)\n"
            "{\n"
            "\tuint64_t        t64;\n"
        )
        r_nta = (
            "void\n"
            "nanoseconds_to_absolutetime(uint64_t   nanosecs,\n"
            "    uint64_t * result)\n"
            "{\n"
            "\tuint64_t        t64;\n"
            "\tif (rtclock_sec_divisor == 0) {\n"
            "\t\t*result = 0;\n"
            "\t\treturn;\n"
            "\t}\n"
        )
        if p_nta in c:
            c = c.replace(p_nta, r_nta, 1)
            print(f"  [OK] {rel_path}: nanoseconds_to_absolutetime divide-by-zero guard installed")

        # 4. In absolutetime_to_microtime: guard against rtclock_sec_divisor / usec_divisor == 0
        p_atm = (
            "\t*secs = t64 = abstime / rtclock_sec_divisor;\n"
            "\tabstime -= (t64 * rtclock_sec_divisor);\n"
            "\n"
            "\t*microsecs = (uint32_t)(abstime / rtclock_usec_divisor);\n"
        )
        r_atm = (
            "\tif (rtclock_sec_divisor == 0 || rtclock_usec_divisor == 0) {\n"
            "\t\t*secs = 0;\n"
            "\t\t*microsecs = 0;\n"
            "\t\treturn;\n"
            "\t}\n"
            "\t*secs = t64 = abstime / rtclock_sec_divisor;\n"
            "\tabstime -= (t64 * rtclock_sec_divisor);\n"
            "\n"
            "\t*microsecs = (uint32_t)(abstime / rtclock_usec_divisor);\n"
        )
        if p_atm in c:
            c = c.replace(p_atm, r_atm, 1)
            print(f"  [OK] {rel_path}: absolutetime_to_microtime divide-by-zero guard installed")

        # 5. In clock_get_system_nanotime: guard against rtclock_sec_divisor == 0
        p_cgsn = (
            "\tabstime = mach_absolute_time();\n"
            "\t*secs = (t64 = abstime / rtclock_sec_divisor);\n"
        )
        r_cgsn = (
            "\tif (rtclock_sec_divisor == 0) {\n"
            "\t\t*secs = 0;\n"
            "\t\t*nanosecs = 0;\n"
            "\t\treturn;\n"
            "\t}\n"
            "\tabstime = mach_absolute_time();\n"
            "\t*secs = (t64 = abstime / rtclock_sec_divisor);\n"
        )
        if p_cgsn in c:
            c = c.replace(p_cgsn, r_cgsn, 1)
            print(f"  [OK] {rel_path}: clock_get_system_nanotime divide-by-zero guard installed")

        # 6. Neutralize timebase_validation to avoid assertion panics
        if "timebase_validation = ml_get_topology_info()->chip_revision == CPU_VERSION_A0;" in c:
            c = c.replace(
                "timebase_validation = ml_get_topology_info()->chip_revision == CPU_VERSION_A0;",
                "timebase_validation = 0; /* hakios: disabled on Cortex-A73 generic ARM64 */"
            )
            print(f"  [OK] {rel_path}: timebase_validation disabled")

        # 7. Replace any Apple proprietary system register reads
        if "S3_4_c15_c10_6" in c or "S3_4_C15_C10_6" in c:
            c = c.replace("S3_4_c15_c10_6", "cntvct_el0").replace("S3_4_C15_C10_6", "cntvct_el0")
            print(f"  [OK] {rel_path}: replaced S3_4_C15_C10_6 with cntvct_el0")

        with open(target_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(c)

    return found

def patch_machine_routines(src):
    candidates = [
        os.path.join(src, "osfmk", "arm64", "machine_routines.c"),
        os.path.join(src, "osfmk", "arm", "machine_routines.c"),
    ]
    found = False
    for target_path in candidates:
        if not os.path.isfile(target_path):
            continue
        found = True
        rel_path = os.path.relpath(target_path, src).replace("\\", "/")

        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if "/* hakios: on Cortex-A73 (generic ARM64), Apple proprietary timer sysregs do not exist */" in content:
            print(f"  [ALREADY PATCHED] {rel_path}: timer sysregs already patched")
            continue

        c = content.replace("\r\n", "\n")

        # 1. Replace Apple proprietary timer sysregs with standard ARM
        p_timer_regs = (
            "#if HAS_APPLE_GENERIC_TIMER\n"
            "/* The kernel timer APIs always use the Apple timebase */\n"
            "#define KERNEL_CNTV_TVAL_EL0 \"S3_1_C15_C15_4\"\n"
            "#define KERNEL_CNTVCT_EL0    \"S3_4_C15_C11_7\"\n"
            "#define KERNEL_CNTVCTSS_EL0  \"S3_4_C15_C10_6\"\n"
            "#define KERNEL_CNTV_CTL_EL0  \"S3_1_C15_C0_5\"\n"
            "#define KERNEL_CNTKCTL_EL1   \"S3_4_C15_C9_6\"\n"
            "#else\n"
            "#define KERNEL_CNTV_TVAL_EL0 \"CNTV_TVAL_EL0\"\n"
            "#define KERNEL_CNTVCT_EL0    \"CNTVCT_EL0\"\n"
            "#define KERNEL_CNTVCTSS_EL0  \"CNTVCTSS_EL0\"\n"
            "#define KERNEL_CNTV_CTL_EL0  \"CNTV_CTL_EL0\"\n"
            "#define KERNEL_CNTKCTL_EL1   \"CNTKCTL_EL1\"\n"
            "#endif"
        )
        r_timer_regs = (
            "/* hakios: on Cortex-A73 (generic ARM64), Apple proprietary timer sysregs do not exist */\n"
            "#define KERNEL_CNTV_TVAL_EL0 \"CNTV_TVAL_EL0\"\n"
            "#define KERNEL_CNTVCT_EL0    \"CNTVCT_EL0\"\n"
            "#define KERNEL_CNTVCTSS_EL0  \"CNTVCT_EL0\"\n"
            "#define KERNEL_CNTV_CTL_EL0  \"CNTV_CTL_EL0\"\n"
            "#define KERNEL_CNTKCTL_EL1   \"CNTKCTL_EL1\""
        )
        if p_timer_regs in c:
            c = c.replace(p_timer_regs, r_timer_regs, 1)
            print(f"  [OK] {rel_path}: replaced KERNEL_CNTV* macros with generic ARM64 registers")
        else:
            # Fallback: replace individual proprietary register strings
            c = c.replace('"S3_4_C15_C11_7"', '"CNTVCT_EL0"')
            c = c.replace('"S3_4_C15_C10_6"', '"CNTVCT_EL0"')
            c = c.replace('"S3_1_C15_C15_4"', '"CNTV_TVAL_EL0"')
            c = c.replace('"S3_1_C15_C0_5"', '"CNTV_CTL_EL0"')
            c = c.replace('"S3_4_C15_C9_6"', '"CNTKCTL_EL1"')
            c = c.replace('"CNTVCTSS_EL0"', '"CNTVCT_EL0"')
            print(f"  [OK] {rel_path}: replaced proprietary sysreg strings individually")

        # 2. speculative_timebase
        p_spec = (
            "static inline uint64_t\n"
            "speculative_timebase(void)\n"
            "{\n"
            "\treturn __builtin_arm_rsr64(KERNEL_CNTVCT_EL0);\n"
            "}"
        )
        r_spec = (
            "static inline uint64_t\n"
            "speculative_timebase(void)\n"
            "{\n"
            "\tuint64_t val;\n"
            "\t__asm__ volatile(\"mrs %0, cntvct_el0\" : \"=r\"(val));\n"
            "\treturn val;\n"
            "}"
        )
        if p_spec in c:
            c = c.replace(p_spec, r_spec, 1)
            print(f"  [OK] {rel_path}: speculative_timebase -> mrs cntvct_el0")

        # 3. nonspeculative_timebase
        p_nonspec = (
            "static inline uint64_t\n"
            "nonspeculative_timebase(void)\n"
            "{\n"
            "#if   __ARM_ARCH_8_6__\n"
            "\treturn __builtin_arm_rsr64(KERNEL_CNTVCTSS_EL0);\n"
            "#else\n"
            "\t// ISB required by ARMV7C.b section B8.1.2 & ARMv8 section D6.1.2\n"
            "\t// \"Reads of CNT[PV]CT[_EL0] can occur speculatively and out of order relative\n"
            "\t// to other instructions executed on the same processor.\"\n"
            "\t__builtin_arm_isb(ISB_SY);\n"
            "\treturn speculative_timebase();\n"
            "#endif\n"
            "}"
        )
        r_nonspec = (
            "static inline uint64_t\n"
            "nonspeculative_timebase(void)\n"
            "{\n"
            "\tuint64_t val;\n"
            "\t__asm__ volatile(\"isb\\n\\tmrs %0, cntvct_el0\" : \"=r\"(val));\n"
            "\treturn val;\n"
            "}"
        )
        if p_nonspec in c:
            c = c.replace(p_nonspec, r_nonspec, 1)
            print(f"  [OK] {rel_path}: nonspeculative_timebase -> isb; mrs cntvct_el0")

        # 4. ml_get_hwclock
        p_hwclock = (
            "uint64_t\n"
            "ml_get_hwclock()\n"
            "{\n"
            "\tuint64_t timebase = nonspeculative_timebase();\n"
            "\treturn timebase;\n"
            "}"
        )
        r_hwclock = (
            "uint64_t\n"
            "ml_get_hwclock()\n"
            "{\n"
            "\tuint64_t timebase;\n"
            "\t__asm__ volatile(\"isb\\n\\tmrs %0, cntvct_el0\" : \"=r\"(timebase));\n"
            "\treturn timebase;\n"
            "}"
        )
        if p_hwclock in c:
            c = c.replace(p_hwclock, r_hwclock, 1)
            print(f"  [OK] {rel_path}: ml_get_hwclock -> isb; mrs cntvct_el0")

        # 5. ml_get_hwclock_speculative
        p_hwclock_spec = (
            "uint64_t\n"
            "ml_get_hwclock_speculative()\n"
            "{\n"
            "\tuint64_t timebase = speculative_timebase();\n"
            "\treturn timebase;\n"
            "}"
        )
        r_hwclock_spec = (
            "uint64_t\n"
            "ml_get_hwclock_speculative()\n"
            "{\n"
            "\tuint64_t timebase;\n"
            "\t__asm__ volatile(\"mrs %0, cntvct_el0\" : \"=r\"(timebase));\n"
            "\treturn timebase;\n"
            "}"
        )
        if p_hwclock_spec in c:
            c = c.replace(p_hwclock_spec, r_hwclock_spec, 1)
            print(f"  [OK] {rel_path}: ml_get_hwclock_speculative -> mrs cntvct_el0")

        # 6. user_timebase_type
        p_ut = (
            "uint8_t\n"
            "user_timebase_type(void)\n"
            "{\n"
            "#if HAS_ACNTVCT\n"
            "\treturn USER_TIMEBASE_NOSPEC_APPLE;\n"
            "#elif HAS_APPLE_GENERIC_TIMER\n"
            "\t// Conveniently, S3_4_C15_C10_6 and ACNTVCT_EL0 have identical encodings\n"
            "\treturn USER_TIMEBASE_NOSPEC_APPLE;\n"
            "#elif __ARM_ARCH_8_6__\n"
            "\treturn USER_TIMEBASE_NOSPEC;\n"
            "#else\n"
            "\treturn USER_TIMEBASE_SPEC;\n"
            "#endif\n"
            "}"
        )
        r_ut = (
            "uint8_t\n"
            "user_timebase_type(void)\n"
            "{\n"
            "\t/* hakios: generic ARM64 Cortex-A73 has standard ARM timebase */\n"
            "\treturn USER_TIMEBASE_SPEC;\n"
            "}"
        )
        if p_ut in c:
            c = c.replace(p_ut, r_ut, 1)
            print(f"  [OK] {rel_path}: user_timebase_type -> USER_TIMEBASE_SPEC")

        with open(target_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(c)

    return found

def patch_pe_identify_machine(src):
    p_pe = os.path.join(src, "pexpert", "arm", "pe_identify_machine.c")
    if not os.path.isfile(p_pe):
        print(f"  [WARN] pe_identify_machine.c not found in {src}")
        return False

    with open(p_pe, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    rel_path = os.path.relpath(p_pe, src).replace("\\", "/")

    if "/* hakios: SM6225 Bengal ARM Generic Timer default: 19.2 MHz (CNTFRQ_EL0) */" in content:
        print(f"  [ALREADY PATCHED] {rel_path}: 19.2 MHz default already installed")
        return True

    c = content.replace("\r\n", "\n")

    # 1. Default frequency
    p_freq = "gPEClockFrequencyInfo.timebase_frequency_hz = 24000000;"
    r_freq = (
        "/* hakios: SM6225 Bengal ARM Generic Timer default: 19.2 MHz (CNTFRQ_EL0) */\n"
        "\tgPEClockFrequencyInfo.timebase_frequency_hz = 19200000;"
    )
    if p_freq in c:
        c = c.replace(p_freq, r_freq, 1)
        print(f"  [OK] {rel_path}: set default timebase_frequency_hz to 19200000 (19.2 MHz)")

    # 2. Query hardware cntfrq_el0 if DT property unset or zero
    p_dec = re.compile(r'(\t+)(gPEClockFrequencyInfo\.dec_clock_rate_hz\s*=\s*gPEClockFrequencyInfo\.timebase_frequency_hz;)')
    m_dec = p_dec.search(c)
    if m_dec:
        indent = m_dec.group(1)
        r_dt_query = (
            f"{indent}/* hakios SM6225 Bengal: fallback to CNTFRQ_EL0 sysreg or 19.2 MHz */\n"
            f"{indent}if (gPEClockFrequencyInfo.timebase_frequency_hz == 0 || gPEClockFrequencyInfo.timebase_frequency_hz == 24000000) {{\n"
            f"{indent}\tuint64_t hw_cntfrq = 0;\n"
            f"{indent}\t__asm__ volatile(\"mrs %0, cntfrq_el0\" : \"=r\"(hw_cntfrq));\n"
            f"{indent}\tif (hw_cntfrq != 0) {{\n"
            f"{indent}\t\tgPEClockFrequencyInfo.timebase_frequency_hz = (unsigned long)hw_cntfrq;\n"
            f"{indent}\t}} else {{\n"
            f"{indent}\t\tgPEClockFrequencyInfo.timebase_frequency_hz = 19200000;\n"
            f"{indent}\t}}\n"
            f"{indent}}}\n"
            f"{indent}{m_dec.group(2)}"
        )
        c = p_dec.sub(lambda m: r_dt_query, c, count=1)
        print(f"  [OK] {rel_path}: installed CNTFRQ_EL0 sysreg / 19.2 MHz query fallback")
    else:
        print(f"  [WARN] {rel_path}: dec_clock_rate_hz not found for CNTFRQ fallback")

    # 3. Patch pe_arm_init_interrupts: guard against failing when AIC/timer are missing on GICv3 platforms
    p_pic_block = (
        'kprintf("pe_arm_map_interrupt_controller: failed to find the interrupt-controller.\\n");\n'
        '\t\treturn 0;'
    )
    if p_pic_block in c:
        r_pic_fail = (
            '#if defined(GENERIC_GIC_V3_IRQ)\n'
            '\t\tkprintf("[gic] pe_arm_map_interrupt_controller: GICv3 platform (SM6225 Bengal), AIC not present\\n");\n'
            '#else\n'
            '\t\tkprintf("pe_arm_map_interrupt_controller: failed to find the interrupt-controller.\\n");\n'
            '\t\treturn 0;\n'
            '#endif'
        )
        c = c.replace(p_pic_block, r_pic_fail, 1)
        print(f"  [OK] {rel_path}: patched pe_arm_init_interrupts AIC fallback for GICv3")

    p_tmr_block = (
        'kprintf("pe_arm_map_interrupt_controller: failed to find the timer.\\n");\n'
        '\t\treturn 0;'
    )
    if p_tmr_block in c:
        r_tmr_fail = (
            '#if defined(GENERIC_GIC_V3_IRQ)\n'
            '\t\tkprintf("[gic] pe_arm_map_interrupt_controller: ARM Generic Timer active, MMIO timer not present\\n");\n'
            '#else\n'
            '\t\tkprintf("pe_arm_map_interrupt_controller: failed to find the timer.\\n");\n'
            '\t\treturn 0;\n'
            '#endif'
        )
        c = c.replace(p_tmr_block, r_tmr_fail, 1)
        print(f"  [OK] {rel_path}: patched pe_arm_init_interrupts timer fallback for GICv3")

    with open(p_pe, "w", encoding="utf-8", newline="\n") as f:
        f.write(c)

    # Also patch pe_init.c if present
    p_init = os.path.join(src, "pexpert", "arm", "pe_init.c")
    if os.path.isfile(p_init):
        with open(p_init, "r", encoding="utf-8", errors="replace") as f:
            c_init = f.read().replace("\r\n", "\n")
        rel_init = os.path.relpath(p_init, src).replace("\\", "/")

        p_cb_call = (
            "void\n"
            "PE_call_timebase_callback(void)\n"
            "{\n"
            "\tstruct timebase_freq_t timebase_freq;\n"
            "\n"
            "\ttimebase_freq.timebase_num = gPEClockFrequencyInfo.timebase_frequency_hz;\n"
        )
        r_cb_call = (
            "void\n"
            "PE_call_timebase_callback(void)\n"
            "{\n"
            "\tstruct timebase_freq_t timebase_freq;\n"
            "\n"
            "\t/* hakios SM6225: ensure non-zero 19.2 MHz default */\n"
            "\tif (gPEClockFrequencyInfo.timebase_frequency_hz == 0 || gPEClockFrequencyInfo.timebase_frequency_hz == 24000000) {\n"
            "\t\tgPEClockFrequencyInfo.timebase_frequency_hz = 19200000;\n"
            "\t}\n"
            "\ttimebase_freq.timebase_num = gPEClockFrequencyInfo.timebase_frequency_hz;\n"
        )
        if p_cb_call in c_init and "/* hakios SM6225: ensure non-zero 19.2 MHz default */" not in c_init:
            c_init = c_init.replace(p_cb_call, r_cb_call, 1)
            with open(p_init, "w", encoding="utf-8", newline="\n") as f:
                f.write(c_init)
            print(f"  [OK] {rel_init}: PE_call_timebase_callback 19.2 MHz fallback installed")

    return True

def patch_board_config(src):
    candidates = [
        os.path.join(src, "pexpert", "pexpert", "arm64", "BCM2837.h"),
        os.path.join(src, "pexpert", "pexpert", "arm64", "board_config.h"),
    ]
    for target_path in candidates:
        if not os.path.isfile(target_path):
            continue
        rel_path = os.path.relpath(target_path, src).replace("\\", "/")
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if "/* hakios: SM6225 Bengal 19.2 MHz timer definitions */" in content:
            print(f"  [ALREADY PATCHED] {rel_path}: CNTFRQ definitions already present")
            continue

        c = content.replace("\r\n", "\n")
        extra_defs = (
            "\n/* hakios: SM6225 Bengal 19.2 MHz timer definitions */\n"
            "#ifndef CNTFRQ_EL0_DEFAULT\n"
            "#define CNTFRQ_EL0_DEFAULT 19200000ULL\n"
            "#endif\n"
            "#ifndef TIMEBASE_FREQUENCY_DEFAULT\n"
            "#define TIMEBASE_FREQUENCY_DEFAULT 19200000ULL\n"
            "#endif\n"
        )
        c += extra_defs
        with open(target_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(c)
        print(f"  [OK] {rel_path}: added CNTFRQ_EL0_DEFAULT (19200000)")

    return True

def main():
    if len(sys.argv) < 2:
        print("Usage: patch_timer_sm6225.py <xnu_src_dir>")
        sys.exit(1)

    src = sys.argv[1]
    if not os.path.isdir(src):
        print(f"Error: Directory '{src}' does not exist.")
        sys.exit(1)

    print(f"=== Applying SM6225 Timer & Clock Subsystem Patch to {src} ===")
    ok_vm = patch_vm_resident(src)
    ok_rtc = patch_rtclock(src)
    ok_mr = patch_machine_routines(src)
    ok_pe = patch_pe_identify_machine(src)
    ok_board = patch_board_config(src)

    print("=== Summary of Results ===")
    print(f"  osfmk/vm/vm_resident.c:           {'OK' if ok_vm else 'SKIPPED/FAILED'}")
    print(f"  osfmk/arm/rtclock.c:              {'OK' if ok_rtc else 'SKIPPED/FAILED'}")
    print(f"  osfmk/arm64/machine_routines.c:   {'OK' if ok_mr else 'SKIPPED/FAILED'}")
    print(f"  pexpert/arm/pe_identify_machine.c: {'OK' if ok_pe else 'SKIPPED/FAILED'}")
    print(f"  board headers:                    {'OK' if ok_board else 'SKIPPED/FAILED'}")

    print("=== SM6225 Timer & Clock Subsystem Patch completed! ===")
    sys.exit(0)

if __name__ == "__main__":
    main()
