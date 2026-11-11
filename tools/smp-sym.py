#!/usr/bin/env python3
# Symbolicate one process from a SWEET stack sample (SMP lines, sweetsample=):
#   tools/smp-sym.py CLEAN_LOG PROCESS SAMPLE_NO   (needs research/dsc/dyld_shared_cache_arm64 and ipsw)
import os, re, subprocess, sys
if len(sys.argv) < 4 or sys.argv[1] in ('-h', '--help'):
    print('Usage: tools/smp-sym.py CLEAN_LOG PROCESS SAMPLE_NO')
    sys.exit(0 if len(sys.argv) > 1 and sys.argv[1] in ('-h', '--help') else 1)
L, want, sample = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(L).read()
blk = text.split('SMP --- sample %s' % sample, 1)[1].split('SMP --- sample', 1)[0]
ents = ['SMP ' + e for e in blk.replace('\n', ' ').split('SMP ') if e.strip()]
slide = None; threads = []
for e in ents:
    m = re.match(r'SMP (\S+)\[(\d+)\] slide (0x[0-9a-f]+)', e)
    if m and m.group(1) == want: slide = int(m.group(3), 16); continue
    m = re.match(r'SMP (\S+) t(\d+) pc (0x[0-9a-f]+) lr (0x[0-9a-f]+) sp \S+ bt(.*)', e)
    if m and m.group(1) == want:
        bt = [int(x, 16) for x in re.findall(r'0x[0-9a-f]{6,}', m.group(5))]
        threads.append((int(m.group(2)), [int(m.group(3), 16), int(m.group(4), 16)] + bt))
cache = {}
def sym(a):
    if a in cache: return cache[a]
    if not (0x180000000 <= a - slide < 0x300000000): cache[a] = '(main binary or unknown)'; return cache[a]
    cache_candidates = [
        os.environ.get('DYLD_CACHE', ''),
        'research/ios26/dsc/dyld_shared_cache_arm64e',
        'research/dsc/dyld_shared_cache_arm64',
    ]
    cache_path = next((p for p in cache_candidates if p and os.path.exists(p)), 'research/dsc/dyld_shared_cache_arm64')
    r = subprocess.run(['ipsw', 'dyld', 'a2s', cache_path, hex(a - slide)], capture_output=True, text=True)
    ls = [l for l in r.stdout.splitlines() if l.startswith('0x')]
    cache[a] = ls[-1].split(': ', 1)[1][:150] if ls else '?'
    return cache[a]
print(want, 'slide', hex(slide), len(threads), 'threads')
for tid, addrs in threads:
    print('-- t%d' % tid)
    for a in addrs[:14]:
        print('   ', sym(a))
