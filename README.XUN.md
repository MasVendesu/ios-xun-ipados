# iPadOS 26 di Xiaomi Redmi Pad SE (xun) — port

Port iPadOS 26 (kernel XNU 12377, arm64e) ke tablet **Xiaomi Redmi Pad SE**
(codename `xun`, Snapdragon 680 / SM6225 "khaje").

Basis: [HouseOfBalance/ios26-sapphire](https://github.com/HouseOfBalance/ios26-sapphire)
(adaptasi HakiOS untuk SM6225) dan [MakrSas/ios-topaz](https://github.com/MakrSas/ios-topaz).
Bedanya di sini:

| Hal | Status |
| --- | --- |
| Shim / boot RAM-only (`fastboot boot`) | dipakai, tidak menulis partisi boot |
| XNU 12377 (arm64e) di SM6225 | dari basis: PAC/LSE trap, pmap 4 KB, timer |
| **Penyimpanan: eMMC (SDHCI 0x04744000)** | **ditambahkan di port ini** — root di partisi `userdata`, baca/tulis |
| **Filesystem root: HFS+ (Apple OSS hfs-715)** | **ditambahkan di port ini**, HFS dibangun ke dalam kernel |
| ADT tablet 1200×1920 + identitas iPad | ditambahkan (`--board xun`) |
| Identitas iPadOS | `iPad8,1` / J317AP (iPad Pro 11" gen-1), artwork 2388, sRGB |
| Layar / sentuh / GPU / Wi-Fi | belum diadaptasi untuk tablet ini |

> Kernel xnu-12377 **belum punya driver eMMC** dan **belum punya HFS**: itu dua
> hal yang port ini isi (`bsd/dev/xun_emmc.c`, `bsd/xun_hfs/`).

## Hardware yang dipakai

Dari DTS perangkat (`xun`, `sm6225-xiaomi-xun.dts`):

- Panel 1200×1920, splash ABL di `0x5c000000` (stride 4800, 32 bpp)
- eMMC pada `sdhc_1` @ `0x04744000` (SDHCI standar, host `sdhci-msm-v5`)
- Sentuh FocalTech FT8203 di SPI `qupv3_se0` (`0x04a80000`), IRQ GPIO 80, reset GPIO 86
- GICv3 `0x0f200000` / GICR `0x0f300000`, UART GENI `0x04a90000`
- Inti: 4× Cortex-A73 (`0x100..0x103`) + 4× Cortex-A53 (`0x0..0x3`)

## Boot arg

```
rd=emmcroot0 emmcroot=userdata emmcrw=1 rootrw=1
```

- `rd=emmcroot0` — root lewat driver eMMC port ini
- `emmcroot=userdata` — partisi GPT bernama `userdata` jadi root
- `emmcrw=0` — jalankan read-only (default tulis; hanya partisi `userdata` yang
  pernah ditulisi)

## Bangun

Kernel dibangun di CI (macos-15, Xcode), bukan di mesin ini:

```bash
gh workflow run build-xnu-12377.yml -R <akun>/ios26-xun
gh run download <run-id> -R <akun>/ios26-xun -n ios26-xun -D out
```

Artefak: `enxerto-boot.img` (root eMMC), `enxerto-boot-md0.img` (root RAM,
smoke test), `mach.development.bcm2837`, `xun.adt`.

Build lokal (Linux, tanpa CI) — sudah terbukti di sini:

```bash
bash kernel/apply.sh            # ke tree XNU; lihat build.sh untuk langkah penuh
bash base/xnu-build.sh -j4 -k   # menghasilkan mach.development.bcm2837
tools/xun-mkboot.sh out/mach.development.bcm2837 out/enxerto-boot.img
tools/xun-mkboot.sh out/mach.development.bcm2837 out/enxerto-boot-md0.img --md0
```

## Pasang

1. Buka bootloader: `adb reboot bootloader`
2. Cek kernel dulu tanpa menyentuh penyimpanan:
   `fastboot boot out/enxerto-boot-md0.img`
3. Siapkan image root iPadOS di PC (lihat `docs/ROOT_IMAGE.md`):
   `sudo tools/xun-build-ipados-root.sh iPad8,1_26.0_23A341_Restore.ipsw out/userdata.simg`
4. Flash: `fastboot flash -S 512M userdata out/userdata.simg`
5. Boot: `fastboot boot out/enxerto-boot.img`

`userdata` Android akan terhapus. Partisi boot/recovery/modem/persist tidak
disentuh.

## Keadaan jujur

- **Kernel sudah dibangun dan ditautkan** dari tree bersih (arm64e Mach-O,
  19,4 MB), dan driver eMMC serta HFS terbukti ada di dalamnya.
- **Belum diuji di perangkat keras**: tidak ada pad yang tersambung ke sesi ini.
  Yang sudah: kompilasi bersih tanpa error, verifikasi marker kernel, jalur
  baca/tulis SDHCI PIO, batas tulis dikunci ke satu partisi, cache
  di-flush sebelum/sesudah transfer.
- Belum ada: sentuh, brightness, baterai, GPU, Wi-Fi/Bluetooth, audio.
- Root image butuh IPSW iPad8,1 26.0 (23A341); OS dan SystemOS di dalamnya
  berformat AEA dan harus dimount lewat `ipsw mount`. Lihat
  `docs/ROOT_IMAGE.md`.
- CI GitHub Actions repo ini sedang terblokir (akun terkunci karena masalah
  billing), jadi artefak dibuat dari build lokal.
