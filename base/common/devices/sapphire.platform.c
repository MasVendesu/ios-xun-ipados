/*
 * sapphire (Xiaomi Redmi Note 13 4G, Snapdragon 685 / SM6225-AD Bengal):
 * The live copy is in xnuloader/loader.c next to platform_dada and platform_redwood,
 * with &platform_sapphire in platforms[].
 *
 * Hardware parameters:
 * - Framebuffer: 1080x2400 AMOLED continuous splash at 0x5c000000 (stride 4320 bytes, 32bpp)
 * - Interrupt Controller: ARM GICv3 with GICD at 0x0f200000, GICR at 0x0f300000 (size 1MB, stride 128KB)
 * - Cores: 8 total across 2 clusters -- 4 Gold Cortex-A73 (MPIDR 0x100..0x103) + 4 Silver Cortex-A53 (MPIDR 0x000..0x003)
 * - Small cores: Cores 0-3 (Cortex-A53 Silver) are the slow cluster (small_cores = 0x0f)
 * - Peripherals: All SoC MMIO peripherals (UFS 0x04804000, UART 0x04a90000, PSHOLD 0x0440b000,
 *   IMEM 0x0c12565c, GIC 0x0f200000/0x0f300000) fall inside 0x00400000..0x40000000 (io_base 0x00400000, io_size 0x3FC00000).
 * - UART: Qualcomm GENI/QUP UART at 0x04a90000 (not an ARM PL011, uart = 0).
 */
static const struct platform platform_sapphire = {
	.name = "sapphire",
	.io_base = 0x00400000, .io_size = 0x3FC00000,       /* SoC peripherals below DRAM (0x80000000) */
	.gic_dist = 0x0f200000, .gic_redist = 0x0f300000,   /* GICv3 Distributor and Redistributors */
	.gic_redist_size = 0x100000, .gic_redist_stride = 0x20000, /* 8 redistributor frames of 128 KB */
	.uart = 0,                                          /* GENI/QUP UART (0x04a90000), not PL011: log via xl log */
	.log_other_cpu = 1,
	.cores = 8, .core_shift = 8,                        /* Qualcomm numbers clusters in MPIDR Aff1 (shift 8) */
	.small_cores = 0x0f,                                /* Cores 0-3 (Cortex-A53 Silver) are the small cores */
	.fb_base = 0x5c000000,                              /* GOP framebuffer (1080x2400 AMOLED continuous splash) */
};
