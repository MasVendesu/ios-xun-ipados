/*
 * Stage-1 MMU with 4KB translation granule (TG0 = 0b00).
 *
 * T0SZ=25 gives a 39-bit VA (512GB space) whose walk starts at Level 1.
 * Level 1 table has 512 entries of 1GB each (4KB table).
 * Level 2 tables have 512 entries of 2MB blocks each (4KB table).
 * Level 3 tables have 512 entries of 4KB pages each (4KB table).
 *
 * 16 Level 2 tables cover the first 16GB (0x00000000 to 0x3ffffffff),
 * identity-mapped (VA == PA).
 *
 * Qualcomm XPUs fault on speculative reads of protected carve-outs (TZ,
 * hyp, modem...), so nothing is mapped cacheable except our own image,
 * payload, and kernel destination:
 *   - default: Device-nGnRnE, execute-never (same as MMU off)
 *   - splash framebuffer 2MB blocks are Normal non-cacheable (write-combining)
 *   - whole 2MB blocks in Write-Back ranges become Normal WB cacheable blocks
 *   - partially-covered 2MB blocks get an L3 table with 4KB pages:
 *     contained pages are Normal WB, uncontained stay Device
 */
#include <stdint.h>
#include "mmu.h"

#define PG_SHIFT	12
#define PG_SIZE		(1UL << PG_SHIFT)		/* 4KB */
#define BLK_SHIFT	21
#define BLK_SIZE	(1UL << BLK_SHIFT)		/* 2MB level-2 block */
#define L1_SHIFT	30
#define L1_SIZE		(1UL << L1_SHIFT)		/* 1GB level-1 entry */
#define NENT		512				/* 512 entries per 4KB table */

#define NUM_L2		16				/* 16 L2 tables cover 16GB */
#define MAX_L3		64				/* up to 64 L3 tables (128MB fine pages) */

#define ATTR_DEV	0	/* MAIR attr0 = 0x00 Device-nGnRnE */
#define ATTR_WB		1	/* MAIR attr1 = 0xff Normal WB RW-alloc */
#define ATTR_NC		2	/* MAIR attr2 = 0x44 Normal non-cacheable */
#define MAIR_VAL	0x44ff00UL

#define D_BLOCK		1UL
#define D_TABLE		3UL
#define D_PAGE		3UL
#define D_ATTR(i)	((uint64_t)(i) << 2)
#define D_SH_OUTER	(2UL << 8)
#define D_SH_INNER	(3UL << 8)
#define D_AF		(1UL << 10)
#define D_PXN		(1UL << 53)
#define D_UXN		(1UL << 54)

#define DEV_FLAGS	(D_ATTR(ATTR_DEV) | D_AF | D_PXN | D_UXN)
#define WB_FLAGS	(D_ATTR(ATTR_WB) | D_SH_INNER | D_AF | D_UXN)
#define NC_FLAGS	(D_ATTR(ATTR_NC) | D_SH_OUTER | D_AF | D_PXN | D_UXN)

/*
 * TCR_EL1:
 * - T0SZ = 25 (39-bit VA, 512GB space, walk starts at Level 1)
 * - IRGN0 = 0b01 (Inner WBWA)
 * - ORGN0 = 0b01 (Outer WBWA)
 * - SH0 = 0b11 (Inner Shareable)
 * - TG0 = 0b00 (4KB translation granule)
 * - T1SZ = 25
 * - EPD1 = 1 (Disable TTBR1 walks)
 * - TG1 = 0b10 (4KB translation granule for TG1)
 */
#define TCR_VAL_BASE	(25UL | (1UL << 8) | (1UL << 10) | (3UL << 12) | (0UL << 14) | \
			 (25UL << 16) | (1UL << 23) | (2UL << 30))

#define SCTLR_M		(1UL << 0)
#define SCTLR_A		(1UL << 1)
#define SCTLR_C		(1UL << 2)
#define SCTLR_I		(1UL << 12)
#define SCTLR_WXN	(1UL << 19)

#define rsr(r) ({ uint64_t _v; __asm__ volatile("mrs %0, " r : "=r"(_v)); _v; })
#define wsr(r, v) __asm__ volatile("msr " r ", %0" : : "r"((uint64_t)(v)))

extern char _head[], _end[];

/* 1 L1 + NUM_L2 L2 + MAX_L3 L3 tables, 4096-byte aligned */
static uint64_t pt_pool[(1 + NUM_L2 + MAX_L3) * NENT] __attribute__((aligned(4096)));

static void dcache_inval_range(uintptr_t start, uintptr_t end)
{
	uint64_t line = 4UL << ((rsr("ctr_el0") >> 16) & 0xf);

	for (uintptr_t a = start & ~(line - 1); a < end; a += line)
		__asm__ volatile("dc ivac, %0" : : "r"(a) : "memory");
	__asm__ volatile("dsb sy" ::: "memory");
}

static void dcache_clean_range(uintptr_t start, uintptr_t end)
{
	uint64_t line = 4UL << ((rsr("ctr_el0") >> 16) & 0xf);

	for (uintptr_t a = start & ~(line - 1); a < end; a += line)
		__asm__ volatile("dc cvac, %0" : : "r"(a) : "memory");
	__asm__ volatile("dsb sy" ::: "memory");
}

static void zero_table(uint64_t *t)
{
	for (int i = 0; i < NENT; i++)
		t[i] = 0;
}

static int in_ranges(uint64_t pa, const struct mmu_range *r, int n)
{
	for (int i = 0; i < n; i++)
		if (pa >= r[i].lo && pa < r[i].hi)
			return 1;
	return 0;
}

int mmu_enable(struct mmu_info *info)
{
	return mmu_enable_ranges(info, 0, 0);
}

int mmu_enable_ranges(struct mmu_info *info, const struct mmu_range *extra, int n_extra)
{
	uintptr_t img_lo = (uintptr_t)_head & ~(PG_SIZE - 1);
	uintptr_t img_hi = ((uintptr_t)_end + PG_SIZE - 1) & ~(PG_SIZE - 1);
	uintptr_t pool = ((uintptr_t)pt_pool + PG_SIZE - 1) & ~(PG_SIZE - 1);
	uint64_t *l1 = (uint64_t *)pool;
	struct mmu_range wb[4];
	int n = 0, n_l3 = 0;

	info->img_lo = img_lo;
	info->img_hi = img_hi;
	info->ttbr = pool;
	if (n_extra > 3)
		return -1;
	wb[n++] = (struct mmu_range){ img_lo, img_hi };
	for (int i = 0; i < n_extra; i++)
		wb[n++] = (struct mmu_range){ extra[i].lo & ~(PG_SIZE - 1),
					      (extra[i].hi + PG_SIZE - 1) & ~(PG_SIZE - 1) };

	/* Zero the L1 table */
	zero_table(l1);

	/* Set up NUM_L2 Level-2 tables (covering 16GB), default Device-nGnRnE */
	for (uint64_t i = 0; i < NUM_L2; i++) {
		uint64_t *l2 = (uint64_t *)(pool + (1 + i) * PG_SIZE);
		l1[i] = (uintptr_t)l2 | D_TABLE;

		uint64_t gb_base = i << L1_SHIFT;
		for (int j = 0; j < NENT; j++) {
			uint64_t pa = gb_base + ((uint64_t)j << BLK_SHIFT);
			l2[j] = pa | DEV_FLAGS | D_BLOCK;
		}
	}

	/* Splash framebuffer: Normal non-cacheable (write-combining) 2MB blocks */
	uint64_t fb_start = FB_PHYS & ~(BLK_SIZE - 1);
	uint64_t fb_end = (FB_PHYS + (uint64_t)FB_STRIDE * FB_H + BLK_SIZE - 1) & ~(BLK_SIZE - 1);
	for (uint64_t pa = fb_start; pa < fb_end; pa += BLK_SIZE) {
		uint64_t l1_idx = pa >> L1_SHIFT;
		uint64_t l2_idx = (pa >> BLK_SHIFT) & (NENT - 1);
		if (l1_idx < NUM_L2) {
			uint64_t *l2 = (uint64_t *)(pool + (1 + l1_idx) * PG_SIZE);
			l2[l2_idx] = pa | NC_FLAGS | D_BLOCK;
		}
	}

	/* Write-back: whole 2MB blocks inside a range, L3 table for partially-covered blocks */
	for (int r = 0; r < n; r++) {
		uint64_t first_blk = wb[r].lo >> BLK_SHIFT;
		uint64_t last_blk = (wb[r].hi - 1) >> BLK_SHIFT;
		for (uint64_t blk = first_blk; blk <= last_blk; blk++) {
			uint64_t base = blk << BLK_SHIFT;
			uint64_t l1_idx = base >> L1_SHIFT;
			uint64_t l2_idx = (base >> BLK_SHIFT) & (NENT - 1);
			if (l1_idx >= NUM_L2)
				continue;
			uint64_t *l2 = (uint64_t *)(pool + (1 + l1_idx) * PG_SIZE);

			if ((l2[l2_idx] & 3) == D_TABLE)
				continue;	/* already split into L3 */

			if (wb[r].lo <= base && wb[r].hi >= base + BLK_SIZE) {
				l2[l2_idx] = base | WB_FLAGS | D_BLOCK;
				continue;
			}

			if ((l2[l2_idx] & 3) == D_BLOCK && ((l2[l2_idx] >> 2) & 7) == ATTR_WB)
				continue;	/* already fully write-back */

			if (n_l3 >= MAX_L3)
				return -1;
			uint64_t *l3 = (uint64_t *)(pool + (1 + NUM_L2 + n_l3++) * PG_SIZE);
			for (int i = 0; i < NENT; i++) {
				uint64_t pa = base + ((uint64_t)i << PG_SHIFT);
				l3[i] = pa | (in_ranges(pa, wb, n) ? WB_FLAGS : DEV_FLAGS) | D_PAGE;
			}
			l2[l2_idx] = (uintptr_t)l3 | D_TABLE;
		}
	}
	info->n_l3 = n_l3;

	/* Clean page tables to PoC so hardware table walker sees them */
	dcache_clean_range(pool, pool + (1 + NUM_L2 + n_l3) * PG_SIZE);

	/* Lines may be stale in the cache from ABL; we wrote memory directly. */
	for (int r = 0; r < n; r++)
		dcache_inval_range(wb[r].lo, wb[r].hi);
	__asm__ volatile("ic iallu; dsb sy; isb" ::: "memory");

	/* Configure TCR_EL1: match supported PARange (36-bit or 40-bit) */
	uint64_t parange = rsr("id_aa64mmfr0_el1") & 0x7;
	if (parange > 2)
		parange = 2;
	if (parange < 1)
		parange = 1;
	uint64_t tcr = TCR_VAL_BASE | (parange << 32);

	wsr("mair_el1", MAIR_VAL);
	wsr("tcr_el1", tcr);
	wsr("ttbr0_el1", pool);
	__asm__ volatile("isb; tlbi vmalle1; dsb sy; isb" ::: "memory");

	uint64_t sctlr = rsr("sctlr_el1");
	info->sctlr_before = sctlr;
	sctlr &= ~(SCTLR_A | SCTLR_WXN);
	sctlr |= SCTLR_M | SCTLR_C | SCTLR_I;
	wsr("sctlr_el1", sctlr);
	__asm__ volatile("isb" ::: "memory");
	info->sctlr_after = rsr("sctlr_el1");
	return 0;
}

/* Translate a VA with AT S1E1R; returns PAR_EL1. */
uint64_t mmu_probe(uintptr_t va)
{
	__asm__ volatile("at s1e1r, %0; isb" : : "r"(va) : "memory");
	return rsr("par_el1");
}
