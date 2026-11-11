# Membuat image root iPadOS 26 (iPad8,1)

Root iPadOS hidup di partisi `userdata`. Image dibuat di komputer, bukan di pad.

## Sumber IPSW

IPSW iPad Pro 11" gen-1 (`iPad8,1`) versi **26.0 (23A341)** — build yang sama
dengan yang dipakai port ini (`tools/renegade/fetch_root.py --device iPad8,1`).

Tiga volume diambil dari IPSW dan **didekripsi** (bagian ini butuh `ipsw` CLI
dari blacktop, kuncinya diambil otomatis dari api.ipsw.me):

| Manifest | Isi |
| --- | --- |
| `OS` | root volume sistem |
| `Cryptex1,SystemOS` | cryptex sistem (termasuk dyld shared cache) |
| `Cryptex1,AppOS` | cryptex aplikasi |

`tools/renegade/fetch_root.py` melakukan ini dan menaruh hasilnya di
`research/ios26/ipsw/`.

## Menyusun volume

```bash
# DMG terdekripsi -> satu image HFSX case-sensitive
sudo tools/mkiosroot.sh OS.dmg SystemOS.dmg AppOS.dmg ios-root.img 16G

# SETELAH mount, jalankan tweak root: lewati Setup, aktivasi, container, keybag
sudo python3 tools/sweet-root-tweaks.py /mnt/iosroot

# selesai
img2simg ios-root.img userdata.simg
```

`tools/sweet-root-tweaks.py` yang membuat iPadOS mau jalan tanpa SEP/baseband:
`/defaults/no-effaceable-storage` (keybag plist biasa), `SetupDone`, path
container, dan flag activation di ADT (`/chosen certificate-*`).

## Ukuran

16 GiB cukup untuk sistem + beberapa aplikasi. Partisi `userdata` di pad ±105 GiB,
jadi sisa ruang bebas dipakai `/private/var`.

## Kalau root rusak

Image tidak journaled bersih setelah panic. Dari recovery:
`fsck.hfsplus -fy` pada image lokal, lalu flash ulang.
