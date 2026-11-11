/*
 * Milestone 4: act as iBoot for xnu.
 *
 * mkpayload.py already resolved the kernel's chained fixups and built the
 * ADT, so this only has to: find the payload in the ABL ramdisk, copy the
 * segments to phys_base + (vmaddr - virt_base), place the ADT and
 * boot_args behind the kernel, and jump to the entry point with the MMU
 * and caches off, x0 = boot_args (physical).
 *
 * The copy/inflate runs with the MMU and D-cache on (payload, kernel
 * destination and our image write-back, everything else Device as before):
 * the bit-at-a-time inflater was ~10 MiB/min on uncached Device memory.
 * mmu_off_jump cleans it all to memory before xnu starts.
 */
#include <stdint.h>
#include "bootui.h"
#include "board.h"
#include "charger.h"
#include "console.h"
#include "fdt.h"
#include "inflate.h"
#include "loader.h"
#include "mmu.h"

#ifndef BOOT_LINE_LENGTH
#define BOOT_LINE_LENGTH	1024
#endif
#define PAGE_16K		0x4000UL

/*
 * version 1: 4 u64 per segment. version 2 (mkpayload.py --compress): a fifth u64, raw_size; when it is not 0 the
 * segment is raw DEFLATE data of file_size bytes that inflates to raw_size bytes (docs/IOS18_PORT.md section 11:
 * ABL places the boot.img ramdisk just below 0x85600000, so the payload must stay small).
 */
struct payload_seg {
	uint64_t vmaddr, vmsize, file_off, file_size;
	uint64_t raw_size;	/* version 2 only */
};

struct payload {
	char magic[8];
	uint32_t version, nsegs;
	uint64_t virt_base, entry_va, adt_off, adt_size, kernel_end;
	uint64_t phys_base, mem_size;
	char cmdline[BOOT_LINE_LENGTH];
	uint8_t segs[];		/* nsegs x (32 bytes v1, 40 bytes v2) */
};

/* pexpert/pexpert/arm64/boot.h, Revision/Version 2 */
struct boot_video {
	uint64_t v_baseAddr, v_display, v_rowBytes, v_width, v_height, v_depth;
};

struct boot_args {
	uint16_t Revision, Version;
	uint64_t virtBase, physBase, memSize, topOfKernelData;
	struct boot_video Video;
	uint32_t machineType;
	uint64_t deviceTreeP;
	uint32_t deviceTreeLength;
	char CommandLine[BOOT_LINE_LENGTH];
	uint64_t bootFlags, memSizeActual;
};

_Static_assert(__builtin_offsetof(struct payload, segs) == 0x448, "payload header layout");
_Static_assert(sizeof(struct payload_seg) == 40, "payload segment layout");
_Static_assert(__builtin_offsetof(struct boot_args, Video) == 40, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, CommandLine) == 108, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, bootFlags) == 1136, "boot_args bootFlags offset");
_Static_assert(__builtin_offsetof(struct boot_args, memSizeActual) == 1144, "boot_args memSizeActual offset");
_Static_assert(sizeof(struct boot_args) == 1152, "boot_args layout");

extern char _head[], _end[];

/* Copy in aligned 8-byte words; safely handle unaligned head and tail bytes. */
static void copy64(uintptr_t dst, uintptr_t src, uint64_t n)
{
	volatile uint8_t *d8 = (volatile uint8_t *)dst;
	volatile const uint8_t *s8 = (volatile const uint8_t *)src;

	if (((dst ^ src) & 7) != 0) {
		for (uint64_t i = 0; i < n; i++)
			d8[i] = s8[i];
		return;
	}

	while (n != 0 && ((uintptr_t)d8 & 7) != 0) {
		*d8++ = *s8++;
		n--;
	}
	if (n == 0)
		return;

	volatile uint64_t *d64 = (volatile uint64_t *)d8;
	volatile const uint64_t *s64 = (volatile const uint64_t *)s8;
	for (uint64_t i = 0; i < n / 8; i++)
		d64[i] = s64[i];

	d8 = (volatile uint8_t *)(d64 + n / 8);
	s8 = (volatile const uint8_t *)(s64 + n / 8);
	for (uint64_t i = 0; i < (n & 7ULL); i++)
		d8[i] = s8[i];
}

static void zero64(uintptr_t dst, uint64_t n)
{
	volatile uint8_t *bytes = (volatile uint8_t *)dst;
	while (n != 0 && ((uintptr_t)bytes & 7) != 0) {
		*bytes++ = 0;
		n--;
	}
	if (n == 0)
		return;
	volatile uint64_t *words = (volatile uint64_t *)bytes;
	for (uint64_t i = 0; i < n / 8; i++)
		words[i] = 0;
	bytes = (volatile uint8_t *)(words + n / 8);
	for (uint64_t i = 0; i < (n & 7ULL); i++)
		bytes[i] = 0;
}

static int magic_ok(const char *m)
{
	static const char want[] = "SWEETXNU";
	for (int i = 0; i < 8; i++)
		if (m[i] != want[i])
			return 0;
	return 1;
}

/* Boot screen progress: INFLATE spans permille 100..900 over all segments' output bytes. */
#define PM_INITRD	40
#define PM_PAYLOAD	70
#define PM_MMU		100
#define PM_INFLATE_END	900
static uint64_t infl_base, infl_total;

static void inflate_progress(uint64_t done)
{
	bootui_mib("INFLATE", (infl_base + done) >> 20, infl_total >> 20, PM_MMU, PM_INFLATE_END);
}

/*
 * Side-effect-free validation of a payload at [rd_start, rd_end): header sane, every
 * file range inside the payload, every destination inside the payload's own RAM window.
 * Validate every payload before using its header, segment table, or destination ranges.
 */
const char *loader_check(uintptr_t rd_start, uintptr_t rd_end)
{
	if (rd_end < rd_start || rd_end - rd_start < sizeof(struct payload))
		return "payload too short";
	uint64_t size = rd_end - rd_start;
	const struct payload *p = (const struct payload *)rd_start;
	if (rd_start & 7)
		return "bad payload alignment";
	if (!magic_ok(p->magic))
		return "no SWEETXNU payload";
	if ((p->version != 1 && p->version != 2) || (p->adt_off & 7))
		return "bad payload header";
	const unsigned stride = p->version == 2 ? 40 : 32;
	if (p->nsegs == 0 || p->nsegs > 64 || (uint64_t)p->nsegs * stride > size - sizeof(struct payload))
		return "bad segment table";
#if defined(BOARD_SAPPHIRE)
	uint64_t dram_lo = DRAM_BASE;
	if (DRAM_SIZE > UINT64_MAX - dram_lo)
		return "invalid DRAM configuration";
	uint64_t dram_hi = dram_lo + DRAM_SIZE;
	if (p->mem_size == 0 || p->phys_base < dram_lo || p->phys_base >= dram_hi || p->mem_size > dram_hi - p->phys_base)
		return "RAM window outside sapphire DRAM";
#endif
	uint64_t ram_lo = p->phys_base;
	if (p->mem_size == 0 || p->mem_size > UINT64_MAX - ram_lo)
		return "bad RAM window";
	uint64_t ram_hi = ram_lo + p->mem_size;
	if (p->adt_off > size || p->adt_size > size - p->adt_off)
		return "ADT outside payload";
	if (p->kernel_end < p->virt_base || p->kernel_end - p->virt_base > p->mem_size)
		return "kernel end outside RAM";
	uint64_t kend_offset = p->kernel_end - p->virt_base;
	uint64_t kend_pa = ram_lo + kend_offset;
	uint64_t loader_lo = (uintptr_t)_head;
	uint64_t loader_hi = (uintptr_t)_end;
	if (loader_hi <= loader_lo)
		return "kernel end outside RAM";
	if (kend_pa < ram_lo || kend_pa >= ram_hi || p->adt_size > ram_hi - kend_pa ||
	    ram_hi - kend_pa - p->adt_size < 2 * PAGE_16K)
		return "kernel end outside RAM";
	if (rd_end > loader_lo && rd_start < loader_hi)
		return "payload overlaps Enxerto image";
	uint64_t hdr_end = sizeof(struct payload) + (uint64_t)p->nsegs * stride;
	if (p->adt_size > 0 && p->adt_off < hdr_end)
		return "ADT overlaps payload header";
	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = (const struct payload_seg *)(p->segs + (uint64_t)i * stride);
		if (s->vmsize == 0)
			return "bad segment table";
		if (s->vmaddr < p->virt_base || s->vmaddr - p->virt_base > p->mem_size ||
		    s->vmsize > p->mem_size - (s->vmaddr - p->virt_base))
			return "segment outside RAM";
		uint64_t pa = ram_lo + (s->vmaddr - p->virt_base);
		if (s->file_off > size || s->file_size > size - s->file_off)
			return "segment outside payload";
		if (s->file_size > 0 && s->file_off < hdr_end)
			return "segment overlaps payload header";
		if (pa < ram_lo || pa > kend_pa || s->vmsize > kend_pa - pa)
			return "segment outside RAM";
		if (rd_end > pa && rd_start < pa + s->vmsize)
			return "payload overlaps kernel segment destination";
		if (pa < loader_hi && loader_lo < pa + s->vmsize)
			return "kernel segment overlaps Enxerto image";
		if (p->version == 2 && s->raw_size) {
			if (s->file_size == 0)
				return "bad compressed segment";
			if (s->raw_size > s->vmsize)
				return "segment inflates past its size";
		} else if (s->file_size > s->vmsize) {
			return "segment larger than its size";
		}
	}
	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *a = (const struct payload_seg *)(p->segs + (uint64_t)i * stride);
		uint64_t a_start = ram_lo + (a->vmaddr - p->virt_base);
		uint64_t a_end = a_start + a->vmsize;
		for (uint32_t j = i + 1; j < p->nsegs; j++) {
			const struct payload_seg *b = (const struct payload_seg *)(p->segs + (uint64_t)j * stride);
			uint64_t b_start = ram_lo + (b->vmaddr - p->virt_base);
			uint64_t b_end = b_start + b->vmsize;
			if (a_start < b_end && b_start < a_end)
				return "kernel segment destinations overlap";
		}
	}
	uint64_t adt_end_pa = kend_pa + p->adt_size;
	if (adt_end_pa > UINT64_MAX - (PAGE_16K - 1))
		return "boot data range overflow";
	uint64_t ba_pa = (adt_end_pa + PAGE_16K - 1) & ~(PAGE_16K - 1);
	if (ba_pa > UINT64_MAX - PAGE_16K)
		return "boot data range overflow";
	uint64_t ba_end_pa = ba_pa + PAGE_16K;
	if (ba_end_pa > ram_hi)
		return "boot data outside RAM";
	if ((p->adt_size != 0 && rd_end > kend_pa && rd_start < adt_end_pa) ||
	    (rd_end > ba_pa && rd_start < ba_end_pa))
		return "payload overlaps boot data destination";
	if ((p->adt_size != 0 && kend_pa < loader_hi && loader_lo < adt_end_pa) ||
	    (ba_pa < loader_hi && loader_lo < ba_end_pa))
		return "boot data overlaps Enxerto image";
	if (p->entry_va & 3)
		return "entry point outside kernel segments";
	if (p->entry_va < p->virt_base || p->entry_va - p->virt_base > p->mem_size ||
	    p->mem_size - (p->entry_va - p->virt_base) < 4)
		return "entry point outside kernel segments";
	int entry_in_segment = 0;
	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = (const struct payload_seg *)(p->segs + (uint64_t)i * stride);
		if (s->vmsize >= 4 && p->entry_va >= s->vmaddr &&
		    (p->entry_va - s->vmaddr) <= s->vmsize - 4) {
			entry_in_segment = 1;
			break;
		}
	}
	if (!entry_in_segment)
		return "entry point outside kernel segments";
	uint64_t entry_pa = ram_lo + (p->entry_va - p->virt_base);
	if (entry_pa < ram_lo || entry_pa >= kend_pa || kend_pa - entry_pa < 4)
		return "entry point outside kernel segments";
	return 0;
}

int loader_has_payload(uintptr_t dtb)
{
	uint64_t rd_start, rd_end;
	uintptr_t emb = ((uintptr_t)_end + 0xfff) & ~0xfffUL;

	if (magic_ok((const char *)emb))
		return 1;
	if (!fdt_chosen_u64(dtb, "linux,initrd-start", &rd_start) ||
	    !fdt_chosen_u64(dtb, "linux,initrd-end", &rd_end) ||
	    rd_end < rd_start || rd_end - rd_start < 8)
		return 0;
	return magic_ok((const char *)rd_start);
}

const char *loader_boot(uintptr_t dtb)
{
	uint64_t rd_start, rd_end;
	uintptr_t emb = ((uintptr_t)_end + 0xfff) & ~0xfffUL;

	if (magic_ok((const char *)emb)) {
		const struct payload *p = (const struct payload *)emb;
		rd_start = emb;
		uint64_t plen = (p->adt_off + p->adt_size + 15) & ~15ULL;
		if (plen < sizeof(struct payload))
			plen = 0x8000000;
		rd_end = emb + plen;
		con_hex("EMBED", emb);
		return loader_boot_payload(rd_start, rd_end, 0);
	}
	if (!fdt_chosen_u64(dtb, "linux,initrd-start", &rd_start) ||
	    !fdt_chosen_u64(dtb, "linux,initrd-end", &rd_end))
		return "no initrd in /chosen";
	return loader_boot_payload(rd_start, rd_end, 0);
}

/*
 * Boot the payload in [rd_start, rd_end) (ABL ramdisk or the fastboot download); returns only on error.
 * verify: retained for callers; structural and bounds validation always runs before use.
 */
const char *loader_boot_payload(uintptr_t rd_start, uintptr_t rd_end, int verify)
{
	const struct payload *p = (const struct payload *)rd_start;
	(void)verify;
	if (rd_end < rd_start || rd_end - rd_start < 8 || (rd_start & 7) || !magic_ok(p->magic))
		return "no SWEETXNU payload";
	/* A real payload: switch to the branded boot screen (con_* output becomes its log window). */
	bootui_init();
	bootui_progress(PM_INITRD, "INITRD FOUND");
	con_hex2("INITRD", "lo=", rd_start, "hi=", rd_end);
	const char *bad = loader_check(rd_start, rd_end);
	if (bad)
		return bad;
	if ((p->version != 1 && p->version != 2) || (p->adt_off & 7) || (rd_start & 7))
		return "bad payload header";
	const unsigned stride = p->version == 2 ? 40 : 32;

	uint64_t pa_of_va_base = p->phys_base - p->virt_base;	/* pa = va + this */
	con_hex2("PHYS", "base=", p->phys_base, "size=", p->mem_size);
	con_hex("VIRT", p->virt_base);
	con_hex("ENTRY", p->entry_va);
	bootui_progress(PM_PAYLOAD, "PAYLOAD OK");

	/* Kernel destination through boot_args (see below), and the payload: cacheable while we work. */
	uint64_t kend_pa = p->kernel_end + pa_of_va_base;
	uint64_t top_pa = ((kend_pa + p->adt_size + PAGE_16K - 1) & ~(PAGE_16K - 1)) + PAGE_16K;
	struct mmu_range wb[3] = {
		{ p->phys_base, top_pa },
		{ rd_start, rd_end },
		{ 0, 0 },	/* the image: filled in from mmu_info */
	};
	struct mmu_info mi = { 0 };
	int mmu_rc = mmu_enable_ranges(&mi, wb, 2);
	con_hex2("MMU", "rc=", (uint32_t)mmu_rc, "l3=", (uint32_t)mi.n_l3);
	if (mmu_rc != 0) {
		return "MMU setup failed";
	}
	wb[2] = (struct mmu_range){ mi.img_lo, mi.img_hi };
	bootui_progress(PM_MMU, "MMU ON");

	infl_base = 0;
	infl_total = 0;
	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = (const struct payload_seg *)(p->segs + (uint64_t)i * stride);
		infl_total += (p->version == 2 && s->raw_size) ? s->raw_size : s->file_size;
	}

	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = (const struct payload_seg *)(p->segs + (uint64_t)i * stride);
		uintptr_t dst = s->vmaddr + pa_of_va_base;
		uint64_t raw = s->file_size;
		if (p->version == 2 && s->raw_size) {
			uint64_t cap = s->raw_size;
			con_hex2("INFLATE", "in=", (uint32_t)s->file_size, "out=", (uint32_t)s->raw_size);
			int rc = inflate_raw((const uint8_t *)(rd_start + s->file_off), s->file_size,
					     (volatile uint8_t *)dst, &cap, inflate_progress);
			if (rc || cap != s->raw_size) {
				con_hex2("INFLATE", "rc=", (uint32_t)rc, "got=", (uint32_t)cap);
				return "inflate failed";
			}
			raw = cap;
		} else {
			copy64(dst, rd_start + s->file_off, s->file_size);
		}
		infl_base += raw;
		bootui_mib("INFLATE", infl_base >> 20, infl_total >> 20, PM_MMU, PM_INFLATE_END);
		if (s->vmsize > raw)
			zero64(dst + raw, s->vmsize - raw);
	}
	con_hex2("SEGS", "n=", p->nsegs, "end=", p->kernel_end + pa_of_va_base);
	bootui_progress(920, "SEGMENTS READY");

	/* ADT, then boot_args, behind the kernel; everything below topOfKernelData is ours. */
	uintptr_t adt_pa = p->kernel_end + pa_of_va_base;
	copy64(adt_pa, rd_start + p->adt_off, p->adt_size);
	uintptr_t ba_pa = (adt_pa + p->adt_size + PAGE_16K - 1) & ~(PAGE_16K - 1);
	uintptr_t top = ba_pa + PAGE_16K;
	if (top != top_pa)
		return "boot_args placement mismatch";

	zero64(ba_pa, PAGE_16K);
	volatile struct boot_args *ba = (volatile struct boot_args *)ba_pa;
	ba->Revision = 2;
	ba->Version = 2;
	ba->virtBase = p->virt_base;
	ba->physBase = p->phys_base;
	ba->memSize = p->mem_size;
	ba->topOfKernelData = top;
	ba->Video.v_baseAddr = FB_BASE;
	ba->Video.v_display = 1;
	ba->Video.v_rowBytes = FB_STRIDE;
	ba->Video.v_width = FB_W;
	ba->Video.v_height = FB_H;
	ba->Video.v_depth = 32;
	ba->deviceTreeP = adt_pa - pa_of_va_base;	/* xnu reads it with the MMU on: a VA */
	ba->deviceTreeLength = (uint32_t)p->adt_size;
	for (int i = 0; i < BOOT_LINE_LENGTH; i++)
		ba->CommandLine[i] = p->cmdline[i];
	ba->CommandLine[BOOT_LINE_LENGTH - 1] = '\0';
	ba->bootFlags = 0;
	ba->memSizeActual = p->mem_size;
	con_hex2("BOOTARG", "pa=", ba_pa, "top=", top);
	bootui_progress(960, "BOOT ARGS");

	uintptr_t entry_pa = p->entry_va + pa_of_va_base;
	con_hex("JUMP", entry_pa);
#define STR_HELPER(x) #x
#define STR(x) STR_HELPER(x)
#ifndef ENXERTO_GIT_SHA
#define ENXERTO_GIT_SHA unknown
#endif
	con_str("BUILD", STR(ENXERTO_GIT_SHA));
	bootui_progress(1000, "STARTING IOS");
	enxerto_charger_handoff();	/* the kernel's sweetcharger=2 finds the charger registers as ABL left them */
	con_str("XNU", "jumping (fbcon clears screen)");
	bootui_handoff();

	/* Ground-truth physical PA calibration markers: */
	con_rect(0, 50, FB_W, 2, 0x00FFA500);  /* Row 50: Orange (2 rows) */
	con_rect(0, 288, FB_W, 2, 0x00FF8C00); /* Row 288: Dark Orange (2 rows) */
	con_rect(0, 304, FB_W, 2, 0x00FF00FF); /* Row 304: Vivid Magenta (2 rows) */

	/* Clean to memory, MMU and caches off, invalidate, jump. */
#ifdef BOOT_PAUSE_MS
	/* debug builds (BOOTPAUSE=ms build.sh): leave the log on screen long enough for a photo */
	{
		uint64_t f, t0, t;
		__asm__ volatile("mrs %0, cntfrq_el0; mrs %1, cntvct_el0" : "=r"(f), "=r"(t0));
		do
			__asm__ volatile("mrs %0, cntvct_el0" : "=r"(t));
		while (t - t0 < f / 1000 * BOOT_PAUSE_MS);
	}
#endif
	mmu_off_jump(entry_pa, ba_pa, wb);
}
