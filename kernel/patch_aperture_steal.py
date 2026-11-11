#!/usr/bin/env python3
"""
patch_aperture_steal.py - Aperture-Based Early Memory Steal for HakiOS XNU 12377 (ARM64E).

On Qualcomm Snapdragon SM6225 (Bengal / Kryo 260 Cortex-A73), during early boot
before pmap is initialized (!pmap_initialized), calling pmap_enter() inside
pmap_steal_memory_internal() causes an infinite retry loop or hang:
  do { ... } while (kr == KERN_RESOURCE_SHORTAGE);
because page table pools (L3 tables) and PV pools are not yet initialized,
and zero pages have been freed into the VM queues.

Since all physical RAM is already mapped 1:1 in the kernel physical aperture (phystokv),
this patch modifies pmap_steal_memory_internal() in osfmk/vm/vm_resident.c to directly
advance the early physical bump pointer (first_avail / avail_start) when !pmap_initialized,
zero the memory in the physical aperture, and immediately return its virtual address.
"""

import os
import sys
import re

def patch_vm_resident(src_dir):
    candidates = [
        os.path.join(src_dir, "osfmk", "vm", "vm_resident.c"),
        os.path.join(src_dir, "vm_resident.c"),
    ]
    target_path = None
    for c in candidates:
        if os.path.isfile(c):
            target_path = c
            break

    if not target_path:
        print(f"[ERROR] Cannot find vm_resident.c in '{src_dir}'", file=sys.stderr)
        return 1

    print(f"Target file: {target_path}")

    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Check for existing patch (idempotency)
    if "/* hakios: aperture-based early memory steal via pmap_next_page */" in content or \
       ("first_pn" in content and "!pmap_initialized" in content and "steal_pages" in content) or \
       ("first_avail = pa + size;" in content and "!pmap_initialized" in content):
        print("  [OK] osfmk/vm/vm_resident.c is already patched for aperture steal, skipping.")
        return 0

    # Pattern to match the pmap_steal_memory_internal function definition
    pattern = re.compile(
        r'(pmap_steal_memory_internal\s*\([^{]+\)\s*\{)',
        re.MULTILINE
    )

    m = pattern.search(content)
    if not m:
        print("[ERROR] Could not find 'pmap_steal_memory_internal' definition in vm_resident.c", file=sys.stderr)
        return 1

    steal_patch = """
	/* hakios: aperture-based early memory steal via pmap_next_page */
	extern boolean_t pmap_initialized;
	extern vm_offset_t first_avail;
	extern pmap_paddr_t avail_start, avail_end;
	extern unsigned long gPhysBase;

	if (!pmap_initialized) {
		ppnum_t first_pn = 0, pn;
		vm_size_t steal_size;
		uint64_t steal_pages, allocated_pages = 0;
		pmap_paddr_t first = (pmap_paddr_t)first_avail;
		pmap_paddr_t end = avail_end;

		/* hakios: reject invalid or overflowing requests without a pre-console panic. */
		if (size == 0 || size > (vm_size_t)(~(vm_size_t)0 - PAGE_MASK)) {
			extern void hk_draw_band(uint32_t, uint32_t, uint32_t);
			hk_draw_band(0x00FF0000, 266, 2); /* Red: invalid early steal size */
			for (;;) {
				__asm__ volatile("wfe");
			}
		}
		steal_size = round_page(size);
		steal_pages = (uint64_t)(steal_size >> PAGE_SHIFT);

		/* Preflight the contiguous aperture so a short range cannot partially steal then panic. */
		if (first < (pmap_paddr_t)gPhysBase || end <= first || steal_size > end - first) {
			extern void hk_draw_band(uint32_t, uint32_t, uint32_t);
			hk_draw_band(0x00FF0000, 266, 2); /* Red: insufficient early aperture */
			for (;;) {
				__asm__ volatile("wfe");
			}
		}

		for (uint64_t p = 0; p < steal_pages; p++) {
			if (!pmap_next_page(&pn)) {
				/* The preflight should make this unreachable; keep the failure non-panicking. */
				vm_page_wire_count += (unsigned int)allocated_pages;
				vm_page_stolen_count += (unsigned int)allocated_pages;
				avail_start = first_avail;      /* keep both in sync */
				extern void hk_draw_band(uint32_t, uint32_t, uint32_t);
				hk_draw_band(0x00FF0000, 266, 2); /* Red: aperture changed during early steal */
				for (;;) {
					__asm__ volatile("wfe");
				}
			}
			if (p == 0) {
				first_pn = pn;
			}
			allocated_pages++;
		}
		vm_page_wire_count += (unsigned int)allocated_pages;
		vm_page_stolen_count += (unsigned int)allocated_pages;
		avail_start = first_avail;      /* keep both in sync */
		pmap_paddr_t pa = ptoa(first_pn);
		bzero((void *)phystokv(pa), steal_size);
		{
			extern void hk_draw_band(uint32_t, uint32_t, uint32_t);
			hk_draw_band(0x00FF1493, 264, 2); /* Deep Pink: aperture steal */
		}
		return (void *)(vm_offset_t)phystokv(pa);
	}"""

    patched_content = pattern.sub(lambda m: m.group(1) + steal_patch, content, count=1)

    if patched_content == content:
        print("[ERROR] Substitution failed in vm_resident.c", file=sys.stderr)
        return 1

    with open(target_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(patched_content)

    print("  [OK] Successfully patched pmap_steal_memory_internal in osfmk/vm/vm_resident.c with aperture bump pointer steal.")
    return 0

def main():
    if len(sys.argv) < 2:
        print("Usage: patch_aperture_steal.py <xnu_src_dir>", file=sys.stderr)
        sys.exit(1)

    xnu_dir = sys.argv[1]
    ret = patch_vm_resident(xnu_dir)
    sys.exit(ret)

if __name__ == "__main__":
    main()
