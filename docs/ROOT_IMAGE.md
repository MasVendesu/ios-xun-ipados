# Membuat image root iPadOS 26 (iPad8,1)

Root iPadOS hidup di partisi `userdata`. Image dibuat di komputer, bukan di pad.

## Sumber IPSW

IPSW iPad Pro 11" gen-1 (`iPad8,1`) versi **26.0 (23A341)** — build yang sama
dengan yang dipakai port ini (`tools/renegade/fetch_root.py --device iPad8,1`).

Tiga volume diambil dari IPSW. Di 26.0, `OS` dan `Cryptex1,SystemOS` berformat
**AEA1** (Apple Encrypted Archive, header WKMS) dan didekripsi oleh `ipsw`
(blacktop) saat mount; `Cryptex1,AppOS` masih DMG biasa:

| Manifest | Isi |
| --- | --- |
| `OS` | root volume sistem |
| `Cryptex1,SystemOS` | cryptex sistem (termasuk dyld shared cache) |
| `Cryptex1,AppOS` | cryptex aplikasi |

`tools/renegade/fetch_root.py` melakukan ini dan menaruh hasilnya di
`research/ios26/ipsw/`.

AEA butuh file IPSW utuh, bukan potongan range: `ipsw mount fs` (dan
`ipsw mount sys`) memilih volume dari IPSW dan memasangnya; salin isinya keluar
sebelum menyusun image. Ruang disk: IPSW 9,8 GB + volume terpasang + image
16 GB.

## Menyusun volume

Cara pendek (skrip ini yang dipakai untuk membangun image yang diserahkan):

```bash
sudo tools/xun-build-ipados-root.sh iPad8,1_26.0_23A341_Restore.ipsw userdata.simg
```

Skrip itu memount OS/SystemOS lewat `ipsw`, memount AppOS lewat `apfs-fuse`,
menyusun volume dengan urutan yang benar (cryptex dulu, root OS di atasnya),
menautkan shared cache, menulis keybag, menjalankan tweak root, fsck, lalu
mengubahnya menjadi `.simg` + sha256.

Urutan manual yang sama (bila ingin mengubah sesuatu di tengah):

```bash
truncate -s 22G ios-root.img && mkfs.hfsplus -s -J -v iOSRoot ios-root.img
mount -o loop,force ios-root.img /mnt/iosroot
# cryptex OS + App ke /private/preboot/Cryptexes/, lalu isi OS root
rsync -aH sysos/root/ /mnt/iosroot/private/preboot/Cryptexes/OS/
rsync -aH appos/root/ /mnt/iosroot/private/preboot/Cryptexes/App/
rsync -aH os/root/    /mnt/iosroot/
# cache, symlink, keybag, tweak — lihat skripnya
```

`tools/sweet-root-tweaks.py` yang membuat iPadOS mau jalan tanpa SEP/baseband:
`/defaults/no-effaceable-storage` (keybag plist biasa), `SetupDone`, path
container, dan flag activation di ADT (`/chosen certificate-*`).

## Kebutuhan host

Image HFS+ **wajib di-mount** saat disusun (rsync ke dalamnya). Yang dibutuhkan:

- kernel host dengan modul `hfsplus` (Ubuntu: `linux-modules-extra-$(uname -r)`),
  atau host dengan dukungan HFS+ bawaan
- `hfsprogs` (mkfs/fsck), `apfs-fuse` (volume IPSW), `rsync`
- `losetup` + `/dev/loop*`

Container yang hanya punya `hfsfuse` (proyek read-only) tidak cukup untuk
menyusun image, walau cukup untuk memeriksa isinya.

## Ukuran

16 GiB cukup untuk sistem + beberapa aplikasi. Partisi `userdata` di pad ±105 GiB,
jadi sisa ruang bebas dipakai `/private/var`.

## Kalau root rusak

Image tidak journaled bersih setelah panic. Dari recovery:
`fsck.hfsplus -fy` pada image lokal, lalu flash ulang.
