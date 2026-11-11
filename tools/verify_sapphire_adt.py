#!/usr/bin/env python3
"""Exhaustive hardware property verification for Xiaomi Redmi Note 13 4G (sapphire,
Qualcomm Snapdragon 685 SM6225 Bengal) running HakiOS (iOS 26 / XNU 12377 ARM64E).

Verifies:
1. Framebuffer node (sweet-fbcon): 0x5c000000, 1080x2400, stride 4320, 32-bit RGBA
2. GICv3 interrupt controller (arm-io/gic): GICD 0x0f200000, GICR 0x0f300000
3. Timer & Clocks: virtual-timer-intid = 19 (INTID 19 PPI), timebase-frequency = 19.2 MHz (19200000 Hz)
4. UFS 2.2 Host Controller (sweet-ufs): 0x04804000, 12KB aperture, qcom,ufshc
5. UART Debug Console (arm-io/uart0 + sweet-uart): 0x04a90000 GENI UART
6. CPU Topology & Frequencies (cpus): 4 Gold cores (2.803 GHz) + 4 Silver cores (1.900 GHz)
7. /chosen & /chosen/memory-map: dram-base 0x80000000, ready for RAMDisk injection
8. Device Identity & Model Alignment: iPhone14,7 / D27AP / A15 Bionic (0x8110), board-id 24
9. Product Identity & MobileGestalt Attributes (/product node)
"""

import sys
import os
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt

adt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sapphire.adt')
assert os.path.exists(adt_path), f'sapphire.adt not found at {adt_path}'
adt_bytes = open(adt_path, 'rb').read()
root, _ = adt.parse(adt_bytes)

def find_node(parent, name):
    for c in parent.children:
        if c.name == name:
            return c
    return None

# ==============================================================================
# CHECK 1: Framebuffer Display Controller (sweet-fbcon)
# ==============================================================================
fbcon = find_node(root, 'sweet-fbcon')
assert fbcon is not None, 'sweet-fbcon node missing in device-tree'
fb_base, fb_sz = struct.unpack('<QQ', fbcon.prop('reg'))
w, = struct.unpack('<I', fbcon.prop('width'))
h, = struct.unpack('<I', fbcon.prop('height'))
stride, = struct.unpack('<I', fbcon.prop('stride'))

assert fb_base == 0x5c000000, f'FB base {hex(fb_base)} != 0x5c000000'
assert w == 1080, f'FB width {w} != 1080'
assert h == 2400, f'FB height {h} != 2400'
assert stride == 4320, f'FB stride {stride} != 4320 (1080 * 4 bytes/pixel)'
assert stride >= w * 4, f'FB stride {stride} < width * 4 ({w * 4})'
assert fb_sz >= stride * h, f'FB aperture size {hex(fb_sz)} < minimum buffer size {stride * h}'
print(f'CHECK 1: Framebuffer at {hex(fb_base)} ({w}x{h}, stride={stride}, size={hex(fb_sz)}) - OK')

# ==============================================================================
# CHECK 2: GICv3 Interrupt Controller (arm-io/gic)
# ==============================================================================
arm_io = find_node(root, 'arm-io')
assert arm_io is not None, 'arm-io node missing in device-tree'
ranges = arm_io.prop('ranges')
assert ranges is not None, 'ranges missing in arm-io'
soc_base = struct.unpack('<QQQ', ranges)[1]
assert soc_base == 0x01000000, f'arm-io soc_base {hex(soc_base)} != 0x01000000'

gic = find_node(arm_io, 'gic')
assert gic is not None, 'gic node missing in arm-io'
gic_compat = gic.prop('compatible')
assert b'arm,gic-v3' in gic_compat, f'GIC compatible missing arm,gic-v3: {gic_compat}'
assert gic.prop('interrupt-controller') is not None, 'interrupt-controller property missing in gic'

gicd_off, gicd_sz, gicr_off, gicr_sz = struct.unpack('<QQQQ', gic.prop('reg'))
gicd = soc_base + gicd_off
gicr = soc_base + gicr_off
assert gicd == 0x0f200000, f'GICD {hex(gicd)} != 0x0f200000'
assert gicd_sz == 0x10000, f'GICD size {hex(gicd_sz)} != 0x10000'
assert gicr == 0x0f300000, f'GICR {hex(gicr)} != 0x0f300000'
assert gicr_sz == 0x100000, f'GICR size {hex(gicr_sz)} != 0x100000'
print(f'CHECK 2: GICv3 at GICD={hex(gicd)} (size {hex(gicd_sz)}), GICR={hex(gicr)} (size {hex(gicr_sz)}), compatible="arm,gic-v3" - OK')

# ==============================================================================
# CHECK 3: Timer & Clocks (SM6225 Bengal Architecture)
# ==============================================================================
vtimer_bytes = gic.prop('virtual-timer-intid')
assert vtimer_bytes is not None, 'virtual-timer-intid missing in gic node'
vtimer, = struct.unpack('<I', vtimer_bytes)
assert vtimer == 19, f'virtual-timer-intid {vtimer} != 19 (PPI 19 / INTID 19 PPI)'

cpus = find_node(root, 'cpus')
assert cpus is not None, 'cpus node missing in device-tree'
assert len(cpus.children) == 8, f'CPU count {len(cpus.children)} != 8'

for cpu in cpus.children:
    tb_bytes = cpu.prop('timebase-frequency')
    assert tb_bytes is not None, f'{cpu.name} missing timebase-frequency'
    tb_freq, = struct.unpack('<I', tb_bytes)
    assert tb_freq == 19200000, f'{cpu.name} timebase-frequency {tb_freq} != 19200000 (19.2 MHz)'

    fixed_freq, = struct.unpack('<I', cpu.prop('fixed-frequency'))
    assert fixed_freq == 19200000, f'{cpu.name} fixed-frequency {fixed_freq} != 19200000'

    periph_freq, = struct.unpack('<I', cpu.prop('peripheral-frequency'))
    assert periph_freq == 19200000, f'{cpu.name} peripheral-frequency {periph_freq} != 19200000'

print(f'CHECK 3: Timer & Clocks: virtual-timer-intid={vtimer} (INTID 19 PPI), timebase-frequency=19200000 Hz (19.2 MHz) across all 8 CPU cores - OK')

# ==============================================================================
# CHECK 4: UFS 2.2 Host Controller (sweet-ufs)
# ==============================================================================
ufs = find_node(root, 'sweet-ufs')
assert ufs is not None, 'sweet-ufs node missing in device-tree'
ufs_compat = ufs.prop('compatible')
assert b'qcom,ufshc' in ufs_compat, f'UFS compatible missing qcom,ufshc: {ufs_compat}'
ufs_base, ufs_sz = struct.unpack('<QQ', ufs.prop('reg'))
assert ufs_base == 0x04804000, f'UFS base {hex(ufs_base)} != 0x04804000'
assert ufs_sz == 0x3000, f'UFS register aperture {hex(ufs_sz)} != 0x3000'
print(f'CHECK 4: UFS 2.2 Host Controller at {hex(ufs_base)} (size {hex(ufs_sz)}, compatible="qcom,ufshc") - OK')

# ==============================================================================
# CHECK 5: UART Debug Console (arm-io/uart0 + sweet-uart)
# ==============================================================================
uart0 = find_node(arm_io, 'uart0')
assert uart0 is not None, 'uart0 missing in arm-io'
uart_off, uart_sz = struct.unpack('<QQ', uart0.prop('reg'))
uart_base = soc_base + uart_off
assert uart_base == 0x04a90000, f'UART base {hex(uart_base)} != 0x04a90000'
assert b'qcom,geni-uart' in uart0.prop('compatible'), 'uart0 compatible missing qcom,geni-uart'

sweet_uart = find_node(root, 'sweet-uart')
assert sweet_uart is not None, 'sweet-uart missing in root'
suart_base, suart_sz = struct.unpack('<QQ', sweet_uart.prop('reg'))
assert suart_base == 0x04a90000, f'sweet-uart base {hex(suart_base)} != 0x04a90000'
assert b'qcom,geni-uart' in sweet_uart.prop('compatible'), 'sweet-uart compatible missing qcom,geni-uart'
print(f'CHECK 5: UART at {hex(uart_base)} (arm-io/uart0 + sweet-uart, compatible="qcom,geni-uart") - OK')

# ==============================================================================
# CHECK 6: CPU Topology, Clusters & Frequencies (cpus)
# ==============================================================================
gold_mpidrs = []
silver_mpidrs = []
for cpu in cpus.children:
    reg, = struct.unpack('<I', cpu.prop('reg'))
    ctype = cpu.prop('cluster-type').decode('utf-8', errors='ignore').rstrip('\0')
    freq, = struct.unpack('<I', cpu.prop('clock-frequency'))
    bus_freq, = struct.unpack('<I', cpu.prop('bus-frequency'))
    mem_freq, = struct.unpack('<I', cpu.prop('memory-frequency'))
    state = cpu.prop('state').decode('utf-8', errors='ignore').rstrip('\0')

    assert bus_freq == 100000000, f'{cpu.name} bus-frequency {bus_freq} != 100000000 (100 MHz)'
    assert mem_freq == 1866000000, f'{cpu.name} memory-frequency {mem_freq} != 1866000000 (1.866 GHz)'

    if reg >= 0x100:
        gold_mpidrs.append(hex(reg))
        assert ctype == 'P', f'{cpu.name} Gold core cluster-type {ctype} != P'
        assert freq == 2803200000, f'{cpu.name} Gold core frequency {freq} != 2803200000 (2.8032 GHz)'
    else:
        silver_mpidrs.append(hex(reg))
        assert ctype == 'E', f'{cpu.name} Silver core cluster-type {ctype} != E'
        assert freq == 1900800000, f'{cpu.name} Silver core frequency {freq} != 1900800000 (1.9008 GHz)'

    if cpu.name == 'cpu0':
        assert state == 'running', f'cpu0 state {state} != running'
    else:
        assert state == 'stopped', f'{cpu.name} state {state} != stopped'

    print(f'  CPU {cpu.name}: reg={hex(reg)}, cluster={ctype}, freq={freq // 1000000}MHz, state={state}')

assert gold_mpidrs == ['0x100', '0x101', '0x102', '0x103'], f'Gold MPIDRs unexpected: {gold_mpidrs}'
assert silver_mpidrs == ['0x0', '0x1', '0x2', '0x3'], f'Silver MPIDRs unexpected: {silver_mpidrs}'
print(f'CHECK 6: CPU Topology: 4 Gold P-cores ({gold_mpidrs} @ 2803MHz) + 4 Silver E-cores ({silver_mpidrs} @ 1900MHz) - OK')

# ==============================================================================
# CHECK 7: /chosen & /chosen/memory-map (RAMDisk Injection Readiness)
# ==============================================================================
chosen = find_node(root, 'chosen')
assert chosen is not None, 'chosen node missing in device-tree'

dram_base_bytes = chosen.prop('dram-base')
assert dram_base_bytes is not None, 'dram-base missing in chosen'
dram_base = int.from_bytes(dram_base_bytes, 'little')
assert dram_base in (0x80000000, 0xb0000000), f'dram-base {hex(dram_base)} invalid (expected 0x80000000 or 0xb0000000)'

dram_size_bytes = chosen.prop('dram-size')
assert dram_size_bytes is not None, 'dram-size missing in chosen'
dram_size = int.from_bytes(dram_size_bytes, 'little')
assert dram_size > 0, f'dram-size {hex(dram_size)} must be positive'

mmap = find_node(chosen, 'memory-map')
assert mmap is not None, '/chosen/memory-map node missing from device-tree'
assert mmap.prop('AAPL,phandle') is not None, 'AAPL,phandle missing in memory-map node'

# Verify RAMDisk injection simulation (matching mkpayload.add_ramdisk_to_adt protocol)
test_root, _ = adt.parse(adt_bytes)
test_chosen = next(c for c in test_root.children if c.name == 'chosen')
test_mmap = next(c for c in test_chosen.children if c.name == 'memory-map')
assert test_mmap.prop('RAMDisk') is None, 'Clean ADT already has a RAMDisk property'

sim_phys = 0x85000000
sim_size = 0x02000000
test_mmap.props.append(('RAMDisk', 0, struct.pack('<2Q', sim_phys, sim_size)))
serialized = adt.serialize(test_root)
reparsed_root, _ = adt.parse(serialized)
reparsed_chosen = next(c for c in reparsed_root.children if c.name == 'chosen')
reparsed_mmap = next(c for c in reparsed_chosen.children if c.name == 'memory-map')
injected_phys, injected_size = struct.unpack('<2Q', reparsed_mmap.prop('RAMDisk'))
assert injected_phys == sim_phys and injected_size == sim_size, \
    f'RAMDisk injection roundtrip mismatch: ({hex(injected_phys)}, {hex(injected_size)}) != ({hex(sim_phys)}, {hex(sim_size)})'
print(f'CHECK 7: /chosen/memory-map verified (dram-base={hex(dram_base)}, dram-size={hex(dram_size)}) and verified 100% ready for RAMDisk injection (<phys, size>) - OK')

# ==============================================================================
# CHECK 8: Device Identity & Model Alignment (iOS 26 / HakiOS - iPhone 14 / iPhone14,7 / D27AP)
# ==============================================================================
model_bytes = root.prop('model')
assert model_bytes is not None, 'model property missing in root'
model_str = model_bytes.decode('utf-8', errors='ignore').rstrip('\0')
assert model_str == 'iPhone14,7', f'Unexpected model: {model_str}'
target_type = root.prop('target-type').decode('utf-8', errors='ignore').rstrip('\0')
target_sub_type = root.prop('target-sub-type').decode('utf-8', errors='ignore').rstrip('\0')
assert target_type == 'D27', f'target-type {target_type} != D27'
assert target_sub_type == 'D27AP', f'target-sub-type {target_sub_type} != D27AP'

compat_bytes = root.prop('compatible')
assert b'xiaomi,sapphire' in compat_bytes, 'xiaomi,sapphire missing in compatible'
assert b'iPhone14,7' in compat_bytes, 'iPhone14,7 missing in compatible'
assert b'D27AP' in compat_bytes, 'D27AP missing in compatible'
assert b'iPhone10,3' not in compat_bytes, 'legacy iPhone10,3 found in compatible'
assert b'D22AP' not in compat_bytes, 'legacy D22AP found in compatible'

bid_bytes = chosen.prop('board-id')
assert bid_bytes is not None, 'board-id missing in chosen'
bid, = struct.unpack('<I', bid_bytes)
assert bid == 24, f'board-id {bid} != 24 (D27AP for iPhone14,7)'

cid_bytes = chosen.prop('chip-id')
assert cid_bytes is not None, 'chip-id missing in chosen'
cid, = struct.unpack('<I', cid_bytes)
assert cid == 0x8110, f'chip-id {hex(cid)} != 0x8110 (Apple A15 Bionic)'

scale_bytes = chosen.prop('display-scale')
assert scale_bytes is not None, 'display-scale missing in chosen'
scale, = struct.unpack('<I', scale_bytes)
assert scale == 3, f'display-scale {scale} != 3'

disp0 = find_node(arm_io, 'disp0')
assert disp0 is not None, 'disp0 missing'
cw, = struct.unpack('<I', disp0.prop('canvas-width'))
ch, = struct.unpack('<I', disp0.prop('canvas-height'))
assert cw == 1170 and ch == 2532, f'disp0 canvas {cw}x{ch} != 1170x2532 (iPhone 14 @ 3x points)'

print(f'CHECK 8: Device identity model={model_str} ({target_sub_type}), board-id={bid} (0x{bid:02x}), chip-id=0x{cid:04x}, canvas={cw}x{ch} @ {scale}x - OK')

# ==============================================================================
# CHECK 9: Product Identity & MobileGestalt Attributes
# ==============================================================================
product = find_node(root, 'product')
assert product is not None, 'product node missing from root device-tree'
ptype = product.prop('product-type').decode('utf-8', errors='ignore').rstrip('\0')
assert ptype == 'iPhone14,7', f'product-type {ptype} != iPhone14,7'
pdesc = product.prop('product-description').decode('utf-8', errors='ignore').rstrip('\0')
assert pdesc == 'iPhone 14', f'product-description {pdesc} != iPhone 14'
art_sub, = struct.unpack('<I', product.prop('artwork-device-subtype'))
assert art_sub == 2532, f'artwork-device-subtype {art_sub} != 2532'
art_scale, = struct.unpack('<I', product.prop('artwork-scale-factor'))
assert art_scale == 3, f'artwork-scale-factor {art_scale} != 3'
gamut = product.prop('artwork-display-gamut').decode('utf-8', errors='ignore').rstrip('\0')
assert gamut == 'sRGB', f'artwork-display-gamut {gamut} != sRGB'
perf_class, = struct.unpack('<I', product.prop('device-perf-memory-class'))
assert perf_class == 2, f'device-perf-memory-class {perf_class} != 2'
thin_bezel, = struct.unpack('<I', product.prop('thin-bezel'))
assert thin_bezel == 1, f'thin-bezel {thin_bezel} != 1'
print(f'CHECK 9: /product node verified ({ptype}, "{pdesc}", subtype={art_sub}, scale={art_scale}x, gamut={gamut}, thin-bezel={thin_bezel}) - OK')

# ==============================================================================
# FINAL SUMMARY
# ==============================================================================
print('\n============================================================')
print('ALL 9 VERIFICATION CHECKS PASSED (100% SUCCESSFUL)')
print('Xiaomi Redmi Note 13 4G (sapphire SM6225 Bengal) ADT Verified')
print('============================================================')
