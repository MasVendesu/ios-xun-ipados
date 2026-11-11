#!/usr/bin/env python3
"""Unpack an Android boot image header v0-v4 into kernel/ramdisk/second/dtb + header.txt."""
import struct, sys, os

if len(sys.argv) < 3 or sys.argv[1] in ('-h', '--help'):
    print(f"Usage: {sys.argv[0]} <boot.img> <output_dir>")
    sys.exit(0 if sys.argv[1:] and sys.argv[1] in ('-h', '--help') else 1)

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
b = open(src, 'rb').read()
assert b[:8] == b'ANDROID!', "Not a valid Android boot image magic"

ver = struct.unpack_from('<I', b, 40)[0]
rnd = lambda n, ps: (n + ps - 1) // ps * ps

if ver in (3, 4):
    ps = 4096
    ks, rs, osv, hs = struct.unpack_from('<4I', b, 8)
    cmd = b[44:44+1536].split(b'\0')[0]
    info = dict(kernel_size=ks, ramdisk_size=rs, page_size=ps, header_version=ver,
                os_version=hex(osv), header_size=hs, cmdline=cmd.decode('latin1', 'replace'))
    parts = [('kernel', ks), ('ramdisk', rs)]
    if ver == 4:
        sig_sz = struct.unpack_from('<I', b, 1580)[0]
        info['signature_size'] = sig_sz
        if sig_sz:
            parts.append(('signature', sig_sz))
    off = ps
else:
    (ks, ka, rs, ra, ss, sa, ta, ps, ver, osv) = struct.unpack_from('<10I', b, 8)
    if ps == 0:
        ps = 4096
    cmd = b[64:64+512].split(b'\0')[0] + b[608:608+1024].split(b'\0')[0]
    info = dict(kernel_size=ks, kernel_addr=hex(ka), ramdisk_size=rs, ramdisk_addr=hex(ra),
                second_size=ss, tags_addr=hex(ta), page_size=ps, header_version=ver,
                os_version=hex(osv), name=b[48:64].split(b'\0')[0].decode('latin1', 'replace'),
                cmdline=cmd.decode('latin1', 'replace'))
    off = ps
    parts = [('kernel', ks), ('ramdisk', rs), ('second', ss)]
    if ver >= 1:
        rds, rdo, hs = struct.unpack_from('<IQI', b, 1632)
        parts.append(('recovery_dtbo', rds)); info['header_size'] = hs
    if ver >= 2:
        ds, da = struct.unpack_from('<IQ', b, 1648)
        parts.append(('dtb', ds)); info['dtb_addr'] = hex(da)

for name, sz in parts:
    if sz:
        open(f'{out}/{name}', 'wb').write(b[off:off+sz])
    info[name + '_offset'] = off
    off += rnd(sz, ps)

with open(f'{out}/header.txt', 'w') as f:
    for k, v in info.items():
        f.write(f'{k}: {v}\n')
print(open(f'{out}/header.txt').read())
