# Driver root eMMC (`bsd/dev/xun_emmc.c`)

Kernel xnu-12377 tidak punya driver untuk eMMC, dan partisi root di pad ada di
eMMC. Driver ini mengisinya: SDHCI yang ditinggal ABL dalam keadaan siap
(clock, DLL, bus mode) dipakai apa adanya — **tidak** di-reset, tidak di-clock
ulang.

## Aturan keselamatan

1. **Satu partisi.** Hanya GPT entry yang namanya diberikan lewat
   `emmcroot=<nama>` (default pemakaian: `userdata`) yang dipakai. Extent-nya
   divalidasi (di dalam kartu, first ≤ last).
2. **Batas tulis ganda.** Setiap request diperiksa terhadap `[first, last]`
   partisi itu, selain itu `EIO`. GPT, area boot/RPMB, dan partisi lain tidak
   pernah ditulisi.
3. **Read-only kalau diminta.** `emmcrw=0` menolak `open(FWRITE)` dan semua
   tulis (`EROFS`). Nama partisi selain `userdata` juga dipaksa read-only.

## Jalur I/O

Polled PIO, tanpa interupsi:

- baca: CMD17 (satu blok) / CMD18 (multi, auto CMD12)
- tulis: CMD24 / CMD25
- perintah diagnostik: CMD8 (EXT_CSD), CMD12 (abort), CMD13 (status), CMD7 (select)

Data 512 byte per blok lewat register `SDHCI_BUFFER`. Sebelum tulis dan sesudah
baca, baris cache di-flush (`FlushPoC_DcacheRegion`) — controller tidak koheren
dengan CPU.

## Bring-up yang belum ada

`semmc_hw_bringup()` masih minimal (reset lewat `SW_RST` di blok core + `hc_mode`).
Kalau ABL ternyata mematikan clock SDCC1, di sinilah port `msm_sdhci.c` dari
u-boot dipasang: rate clock, urutan `CORE_DLL_CONFIG`, `CORE_VENDOR_SPEC`,
regulator, power-on.

Driver sengaja **tidak** memakai CMD6 (switch bus width/timing): kartu dibiarkan
pada mode yang ABL sudah set.

## Log

Semua lewat `XUN EMMC: ...` ke konsol kernel, termasuk dump GPT saat
`emmcprobe=1`.

## Di luar driver ini

XNU memanggil driver lewat hook `rd=emmcroot0` di
`iokit/bsddev/IOKitBSDInit.cpp` (patch `0105`), yang mendaftarkan `bdevsw` dan
mengembalikan `dev_t` root.
