#pragma once
/*
 * Board selection: build.sh passes -DBOARD_BERYLLIUM for the Poco F1
 * (xiaomi beryllium, SDM845); the default is sweet (SM7150).
 * Every value comes from the board's mainline/downstream device tree.
 */
#if defined(BOARD_SAPPHIRE)
#define BOARD_NAME		"sapphire"
/*
 * cont_splash_region left lit by ABL at 0x5c000000 (15 MiB, 1080x2400).
 * Verified from ios-sapphire (SM6225 khaje live FDT and shim).
 */
#define FB_BASE			0x5c000000UL
#define FB_W			1080
#define FB_H			2400
/* bengal/SM6225 imem@c125000, restart_reason@65c */
#define IMEM_RESTART_REASON	0x0c12565cUL
#define PSHOLD			0x0440b000UL
#define GICD_BASE		0x0f200000UL
#define GICR_BASE		0x0f300000UL
#define GICR_SIZE		0x100000UL
#define UFS_BASE		0x04804000UL	/* ufshc@4804000 */
#define UART_BASE		0x04a90000UL	/* SM6225 GENI UART (QUP0 SE1) */
#define DRAM_BASE		0x80000000UL	/* lowmem DDR bank */
#define DRAM_SIZE		0x80000000UL	/* sapphire DDR window covering up to 0x100000000 (0xb0000000..0xfd000000) */
#define DRAM_HIGHMEM		0x100000000UL	/* highmem DDR bank */
#elif defined(BOARD_XUN)
#define BOARD_NAME		"xun"
/*
 * cont_splash_region left lit by ABL at 0x5c000000 (15 MiB, 1200x1920).
 * The same SoC block as sapphire; the panel and its stride are the pad's.
 */
#define FB_BASE			0x5c000000UL
#define FB_W			1200
#define FB_H			1920
/* bengal/SM6225 imem@c125000, restart_reason@65c */
#define IMEM_RESTART_REASON	0x0c12565cUL
#define PSHOLD			0x0440b000UL
#define GICD_BASE		0x0f200000UL
#define GICR_BASE		0x0f300000UL
#define GICR_SIZE		0x100000UL
#define UFS_BASE		0x04804000UL	/* no UFS on the pad; unused */
#define EMMC_BASE		0x04744000UL	/* sdhc_1 @4744000, the eMMC */
#define UART_BASE		0x04a90000UL	/* SM6225 GENI UART (QUP0 SE1) */
#define DRAM_BASE		0x80000000UL
#define DRAM_SIZE		0x80000000UL
#define DRAM_HIGHMEM		0x100000000UL
#elif defined(BOARD_BERYLLIUM)
#define BOARD_NAME		"beryllium"
/* reserved-memory cont_splash_mem@9d400000, simple-framebuffer 1080x2246 */
#define FB_BASE			0x9d400000UL
#define FB_W			1080
#define FB_H			2246
/* sdm845 imem@146bf000, restart_reason@65c */
#define IMEM_RESTART_REASON	0x146bf65cUL
#define PSHOLD			0x0c264000UL
#define GICD_BASE		0x17a00000UL
#define GICR_BASE		0x17a60000UL
#define GICR_SIZE		0x100000UL
#define UFS_BASE		0x01d84000UL	/* ufshc@1d84000 */
#define UART_BASE		0x00a84000UL	/* SDM845 GENI UART */
#define DRAM_BASE		0x80000000UL
#else
#define BOARD_NAME		"sweet"
/* reserved-memory cont_splash_region@9c000000 */
#define FB_BASE			0x9c000000UL
#define FB_W			1080
#define FB_H			2400
/* qcom,msm-imem restart_reason@65c */
#define IMEM_RESTART_REASON	0x146aa65cUL
#define PSHOLD			0x0c264000UL
#define GICD_BASE		0x17a00000UL
#define GICR_BASE		0x17a60000UL
#define GICR_SIZE		0x100000UL
#define UFS_BASE		0x01d84000UL	/* ufshc@1d84000 */
#define UART_BASE		0x00a84000UL	/* SM7150 GENI UART */
#define DRAM_BASE		0x80000000UL
#endif

#define FB_STRIDE		(FB_W * 4)	/* a8r8g8b8, matches mainline simplefb */
#define FB_PHYS			FB_BASE
