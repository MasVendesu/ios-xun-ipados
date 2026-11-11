#!/usr/bin/env python3
"""Build the minimal Apple Device Tree that the SWEET xnu needs to boot.

Only what the early boot path actually reads (see NOTES.md); every MMIO
address comes from the sweet live FDT. Usage:  mkadt.py OUT.adt [--dump] [--qemu] [--highmem] [--board sweet|beryllium] [--display-scale 3|2] [--points WxH] [--lowpool BASE:SIZE]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402

# Kernel-managed RAM: above every Qualcomm XPU carve-out (see NOTES.md)
PHYS_BASE = 0xb0000000
MEM_SIZE = 0x4d000000
# --highmem (phone only): the whole upper DDR bank. The live FDT memory node is
# <0x80000000 2G>, <0x1_00000000 0xfcc00000>; every fixed reserved-memory
# carve-out is in the low bank and the dynamic pools are limited below 4G.
HIGH_PHYS_BASE = 0x100000000
HIGH_MEM_SIZE = 0xfcc00000
# /arm-io children are relative to this; it must not be 0 (serial_init bails)
SOC_BASE = 0x01000000

GICD, GICD_SIZE = 0x17a00000, 0x10000
GICR, GICR_SIZE = 0x17a60000, 0x100000
FB, FB_SIZE = 0x9c000000, 0x1700000
SWEET_ECID = 0x5357454554000001  # 'SWEET' + 1, synthetic
FB_W, FB_H, FB_STRIDE = 1080, 2400, 1080 * 4
# Internal UFS host (ufshc@1d84000 "ufs_mem"); QEMU virt has none
UFS, UFS_SIZE = 0x01d84000, 0x3000
TIMEBASE = 19200000
VTIMER_INTID = 19
# QEMU virt (-M virt,gic-version=3 -cpu cortex-a76): GICD/GICR and the generic timer's CNTFRQ
QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE = 0x08000000, 0x080a0000, 62500000
QEMU_VTIMER_INTID = 27

# Per-board differences; GIC, UFS, timer and the SoC window are the same on
# SM7150 (sweet) and SDM845 (beryllium, Poco F1).
# Synthetic locally administered Bluetooth address (not the phone's): /chosen
# mac-address-bluetooth0 and /arm-io/bluetooth local-mac-address, and what the
# kernel writes into the WCN3990 with the QCA set-BD_ADDR vendor command.
BT_MAC = bytes([0x02, 0x00, 0x5e, 0x77, 0xb0, 0x01])

BOARDS = {
    'sweet': dict(
        fb=(0x9c000000, 0x1700000), fb_wh=(1080, 2400),
        compat=['xiaomi,sweet', 'qcom,sm7150'],
        # 0-5 Cortex-A55, 6-7 Cortex-A76
        cpu_compat='qcom,kryo470', big_from=6, freqs=(1804800000, 2304000000)),
    'beryllium': dict(
        # cont_splash_mem@9d400000, simple-framebuffer 1080x2246 (mainline beryllium-common)
        fb=(0x9d400000, 0x2400000), fb_wh=(1080, 2246),
        # 'xiaomi,sweet' stays: SweetPlatformExpert matches it
        compat=['xiaomi,beryllium', 'xiaomi,sweet', 'qcom,sdm845'],
        # 0-3 Kryo 385 Silver (A55), 4-7 Gold (A75)
        cpu_compat='qcom,kryo385', big_from=4, freqs=(1766400000, 2803200000),
        # Without --highmem: low bank between cont_splash (ends 0x9f800000)
        # and ramoops@b0000000. --highmem: same window as sweet; the live
        # FDT memory is <0x80000000 3G>, <0x1_40000000 0xbd4a0000> and every
        # fixed carve-out ends below 0xb0400000.
        mem=(0x9f800000, 0x0f800000),
        # MobileGestalt's Wi-Fi address: /chosen mac-address-wifi0 (set by
        # iBoot on real devices), else ADT sdio/wlan/marconi-wifi nodes,
        # else it WAITS for an IONetworkController and times out, once per
        # question -> lsd's first-boot database seed takes ages (boot 2).
        # Synthetic locally administered addresses, not the phone's.
        chosen_extra=[
            ('mac-address-wifi0', bytes([0x02, 0x00, 0x5e, 0x77, 0xe0, 0x01])),
            ('mac-address-bluetooth0', BT_MAC),
        ],
        # /arm-io/bluetooth for bluetoothd/BlueTool (docs/BT_IOS_TRANSPORT.md 3.2). Sweet has no
        # Bluetooth driver: the node is beryllium only.
        bluetooth=True),
    'sapphire': dict(
        # cont_splash_region left lit by ABL at 0x5c000000 (15 MiB, 1080x2400)
        fb=(0x5c000000, 0x00f00000), fb_wh=(1080, 2400),
        # 'xiaomi,sweet' stays: SweetPlatformExpert matches it
        compat=['xiaomi,sapphire', 'xiaomi,sweet', 'qcom,sm6225', 'qcom,khaje'],
        # SM6225 / Snapdragon 685: 4x Cortex-A73 Gold (MPIDR 0x100..0x103).
        # Silver cluster (Cortex-A53) has no 16K granule (TGran16 == 0), so on 16K XNU
        # it is parked; with --amp (4K kernel) all 8 cores are listed.
        cpu_compat='qcom,kryo260', big_from=0, freqs=(1900800000, 2803200000),
        mem=(0xb0000000, 0x4d000000),
        gic=(0x0f200000, 0x10000, 0x0f300000, 0x100000),
        ufs=(0x04804000, 0x3000),
        uart=(0x04a90000, 0x1000),
        ecid=0x5341505048000001,
        wlan='topaz,wcn3988',
        baseband='baseband,n41',
        chosen_extra=[
            ('mac-address-wifi0', bytes([0x02, 0x00, 0x5e, 0x77, 0xe0, 0x02])),
            ('mac-address-bluetooth0', bytes([0x02, 0x00, 0x5e, 0x77, 0xb0, 0x02])),
        ],
        bluetooth=False),
    'xun': dict(
        # cont_splash_region left lit by ABL at 0x5c000000 (15 MiB, 1200x1920).
        # The panel is 1200x1920 portrait; iOS runs landscape on it (1920x1200).
        fb=(0x5c000000, 0x00f00000), fb_wh=(1200, 1920),
        # 'xiaomi,sweet' stays: SweetPlatformExpert matches it
        compat=['xiaomi,xun', 'xiaomi,sweet', 'qcom,sm6225', 'qcom,khaje'],
        # SM6225: 4x Cortex-A73 Gold (MPIDR 0x100..0x103), 4x A53 Silver parked
        # (no 16K granule: TGran16 == 0).
        cpu_compat='qcom,kryo260', big_from=0, freqs=(1900800000, 2806400000),
        # Without --highmem: low bank between cont_splash (ends 0x5cf00000) and
        # the 0x80000000 I/O window. --highmem: the upper bank from 4 GiB.
        mem=(0x6b000000, 0x15000000),
        gic=(0x0f200000, 0x10000, 0x0f300000, 0x100000),
        # eMMC (SDHCI) instead of UFS: no ufs entry, /xun-emmc below.
        emmc=(0x04744000, 0x1000),
        uart=(0x04a90000, 0x1000),
        ecid=0x58554e0000000001,          # 'XUN' + 1, synthetic
        wlan='topaz,wcn3988',
        chosen_extra=[
            ('mac-address-wifi0', bytes([0x02, 0x00, 0x5e, 0x77, 0xe2, 0x01])),
            ('mac-address-bluetooth0', bytes([0x02, 0x00, 0x5e, 0x77, 0xb2, 0x01])),
        ],
        bluetooth=False),
}


# Apple device profiles for iOS userland matching
APPLE_DEVICES = {
    'iPhone14,7': {
        'model': 'iPhone14,7',
        'target_type': 'D27',
        'target_sub_type': 'D27AP',
        'chip_id': 0x8110,   # Apple A15 Bionic (T8110)
        'board_id': 24,      # 0x18
        'default_points': (390, 844),
        'default_scale': 3,
    },
    'iPhone11,8': {
        'model': 'iPhone11,8',
        'target_type': 'N104',
        'target_sub_type': 'N104AP',
        'chip_id': 0x8020,   # Apple A12 Bionic (T8020)
        'board_id': 12,      # 0x0C
        'default_points': (414, 896),
        'default_scale': 2,
    },
    'iPad8,1': {
        'model': 'iPad8,1',
        'target_type': 'J317',
        'target_sub_type': 'J317AP',
        'chip_id': 0x8027,   # Apple A12X (T8027)
        'board_id': 26,
        'default_points': (600, 960),
        'default_scale': 2,
    },
    'iPad7,3': {
        'model': 'iPad7,3',
        'target_type': 'J207',
        'target_sub_type': 'J207AP',
        'chip_id': 0x8011,   # Apple A10X (T8011)
        'board_id': 22,
        'default_points': (556, 834),
        'default_scale': 2,
    },
    'iPhone18,1': {
        'model': 'iPhone18,1',
        'target_type': 'V53',
        'target_sub_type': 'V53AP',
        'chip_id': 0x8150,   # Apple A19 Pro
        'board_id': 10,
        'default_points': (402, 874),
        'default_scale': 3,
    },
}

BOOT_MANIFEST_HASH = hashlib.sha384(b'sweet boot manifest').digest()

# The iOS userland identity default is iPhone 14 (iPhone14,7 / D27AP) for iOS 26. MobileGestalt
# derives product type, device class, home-button-less UI, artwork etc. from
# the root identity and /product of the device tree.


u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def device_product(sub_type='D27AP'):
    """/product properties of the real device tree, without syscfg placeholders."""
    paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), f'../research/ios26/{sub_type.lower()}.adt'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), f'../research/ios26/{sub_type}.adt'),
    ]
    for p in paths:
        if os.path.exists(p):
            root, _ = adt.parse(open(p, 'rb').read())
            for c in root.children:
                if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
                    return [(k, v) for k, f, v in c.props if not f and k not in ('name', 'AAPL,phandle')]
    # Synthetic standard properties for iPhone 14 (D27AP) under iOS 26
    return [
        ('product-type', cstr('iPhone14,7')),
        ('product-name', cstr('iPhone')),
        ('product-description', cstr('iPhone 14')),
        ('artwork-device-subtype', u32(2532)),
        ('artwork-scale-factor', u32(3)),
        ('artwork-display-gamut', cstr('sRGB')),
        ('device-perf-memory-class', u32(2)),
        ('allow-32bit-apps', u32(0)),
        ('thin-bezel', u32(1)),
    ]


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


# SWEET low pool for zero-copy scanout (kernel boot-arg sweetfb_zc=1, docs/DISPLAY_HW.md
# section 3): a window of the low DDR bank that the kernel does not manage. The kernel
# has the same default built in (0xb0400000 + 252 MiB) and validates whatever it is given
# against the live reserved-memory regions; --lowpool BASE:SIZE (hex) writes
# /arm-io/disp0 "sweet-lowpool" = <u64 base, u64 size> to override it.
LOWPOOL_MIN, LOWPOOL_MAX = 0x9f800000, 0xfd000000


def parse_lowpool(text):
    base, size = (int(v, 0) for v in text.split(':'))
    return base, size


def bluetooth_node(ph):
    """Stock iPhone (D27AP): /arm-io/uart1/bluetooth (compatible bluetooth,wcn3990). bluetoothd finds it by
    IOServiceNameMatching("bluetooth") and reads local-mac-address, transport-encoding and
    transport-speed from the registry entry; BlueTool does the same. Our transport is the
    kernel control socket com.apple.uart.bluetooth (bsd/dev/beryllium_bt_hci.c).
    transport-encoding 3 = H4BC, the stock value and the path the BT_HCI_TRANSPORT
    override (H4BC:POSIX:com.apple.uart.bluetooth,3000000) selects anyway; 1 = plain H4.
    No function-* properties: they are consumed by the AppleBluetooth BTReset kext, which this
    kernel does not have (the kernel owns power and reset); without function-power_enable no
    /dev/btpoweroff exists and BlueTool's power/reset commands are no-ops."""
    return node('bluetooth', [
        ph(),
        ('device_type', cstr('bluetooth')),
        ('compatible', cstr('bluetooth,wcn3990')),
        ('local-mac-address', BT_MAC),
        ('transport-encoding', u32(3)),
        ('transport-speed', u32(3000000)),
        ('vendor-id', u32(0x05ac)),
        ('product-id', u32(0x12a0)),
    ])


def build(seed=None, qemu=False, highmem=False, board='sweet', display_scale=None, points=None, lowpool=None, amp=False, device='iPhone14,7'):
    """qemu=True: same tree, but with QEMU virt's GIC addresses and CNTFRQ (tools/qemu-sweet.py)."""
    b = BOARDS[board]
    dev = APPLE_DEVICES.get(device, APPLE_DEVICES['iPhone14,7'])
    display_scale = display_scale if display_scale is not None else dev.get('default_scale', 3)
    points = points if points is not None else dev.get('default_points', (390, 844))
    phys_base, mem_size = (HIGH_PHYS_BASE, HIGH_MEM_SIZE) if highmem else (PHYS_BASE, MEM_SIZE)
    if board != 'sweet' and not qemu:
        if not highmem:
            phys_base, mem_size = b['mem']
    fb, fb_size = b['fb']
    fb_w, fb_h = b['fb_wh']
    if lowpool is not None:
        lp_base, lp_size = lowpool
        if board != 'beryllium' or qemu:
            raise SystemExit('--lowpool: Poco F1 only (--board beryllium, not --qemu)')
        if lp_base < LOWPOOL_MIN or lp_base + lp_size > LOWPOOL_MAX or lp_base & 0x3fff or lp_size & 0x3fff:
            raise SystemExit(f'--lowpool {lp_base:#x}:{lp_size:#x}: must be 16K aligned inside {LOWPOOL_MIN:#x}..{LOWPOOL_MAX:#x}')
        if lp_base < phys_base + mem_size and phys_base < lp_base + lp_size:
            raise SystemExit(f'--lowpool {lp_base:#x}:{lp_size:#x} overlaps the kernel RAM {phys_base:#x}+{mem_size:#x}')
    b_gic = b.get('gic')
    if qemu:
        gicd, gicd_sz, gicr, gicr_sz, timebase = QEMU_GICD, GICD_SIZE, QEMU_GICR, GICR_SIZE, QEMU_TIMEBASE
    elif b_gic:
        gicd, gicd_sz, gicr, gicr_sz, timebase = b_gic[0], b_gic[1], b_gic[2], b_gic[3], TIMEBASE
    else:
        gicd, gicd_sz, gicr, gicr_sz, timebase = GICD, GICD_SIZE, GICR, GICR_SIZE, TIMEBASE
    ufs_base, ufs_size = b.get('ufs', (UFS, UFS_SIZE))
    ecid = b.get('ecid', SWEET_ECID)
    seed = seed if seed is not None else os.urandom(256)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('firmware-version', cstr('enxerto')),
        ('system-firmware-version', cstr('enxerto')),
        # iBoot's SHA-384 of the boot manifest; /sbin/mount -P 1 exits unless
        # it exists (bind mounts /private/preboot/<hex>/usr/standalone/firmware,
        # tolerating a missing directory). Fixed stand-in value.
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        # libimg4 (cryptexd) aborts "failed to get crypto algorithm" without
        # it; real A11+ device trees say sha2-384.
        ('crypto-hash-method', cstr('sha2-384')),
        # MobileGestalt's screen class: display-scale (u32, default 0) comes
        # from /chosen, the pixel size from IOMobileFramebuffer. Scale 0 makes
        # SpringBoard's FBSDisplayMode "infxinf@0x" invalid. 1080x2400 @3x =
        # 360x800 points (like iPhone X class devices).
        ('display-scale', u32(display_scale)),
        # XNU derives IOPlatformUUID (gethostuuid) from these on arm; without
        # them CFPreferences cannot use kCFPreferencesCurrentHost (no ByHost
        # files), so e.g. DataMigration never records LastSystemVersion and
        # migrates on every boot. Synthetic ECID (not the phone's); chip-id
        # and board-id matching the target iOS userland device identity.
        ('unique-chip-id', struct.pack('<Q', ecid)),
        ('chip-id', u32(dev['chip_id'])),
        ('board-id', u32(dev['board_id'])),
        # Security status as on a dev-fused, undemoted board (tuple 0,0,1,1):
        # MobileGestalt reads these from /chosen, and mobileactivationd's
        # DeviceType then takes its native "Hactivation" branch (state
        # Activated without an activation record; no SEP, no baseband here).
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ] + b.get('chosen_extra', []), [node('memory-map', [ph()]),
        # Local boot policy (kern_csr.c): SIP relaxed like `csrutil disable`
        # (lp-sip0: untrusted kexts, unrestricted FS, task_for_pid, kernel
        # debugger, dtrace, nvram), lp-sip1: unauthenticated (unsealed) root.
        # The iOS shared cache is in no trust cache, so dyld can only map it
        # with CSR_ALLOW_UNRESTRICTED_FS (vm_unix.c shared region checks).
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    # CPU topology:
    # On sapphire/xun (SM6225): 4 Gold cores (MPIDR 0x100..0x103) + 4 Silver cores (MPIDR 0x0..0x3)
    # On sweet / beryllium: all 8 cores in physical-core order, one homogeneous cluster.
    cpu_nodes = []
    if board in ('sapphire', 'xun'):
        for core in range(8):
            silver = core >= 4
            mpidr = (core & 3) if silver else (0x100 | core)
            freq = 1900800000 if silver else 2803200000
            cpu_nodes.append(node(f'cpu{core}', [
                ph(),
                ('device_type', cstr('cpu')),
                ('compatible', cstr('qcom,kryo260') + cstr('qcom,kryo470') + cstr('ARM,v8')),
                ('reg', u32(mpidr)),
                ('cpu-id', u32(core)),
                ('cluster-id', u32(1 if silver else 0)),
                ('cluster-type', u32(ord('E') if silver else ord('P'))),
                ('die-cluster-id', u32(0)),
                ('cluster-core-id', u32(core & 3)),
                ('l2-cache-id', u32(1 if silver else 0)),
                ('l3-cache-id', u32(0)),
                ('state', cstr('running' if core == 0 else 'stopped')),
                ('timebase-frequency', u32(timebase)),
                ('clock-frequency', u32(freq)),
                ('bus-frequency', u32(100000000)),
                ('memory-frequency', u32(1866000000)),
                ('peripheral-frequency', u32(timebase)),
                ('fixed-frequency', u32(timebase)),
            ]))
    else:
        for core in range(1 if qemu else 8):
            cpu_nodes.append(node(f'cpu{core}', [
                ph(),
                ('device_type', cstr('cpu')),
                # SweetCPU matches IONameMatch 'qcom,kryo470': keep it after the real core name
                ('compatible', cstr(b['cpu_compat']) + (cstr('qcom,kryo470') if b['cpu_compat'] != 'qcom,kryo470' else b'') + cstr('ARM,v8')),
                ('reg', u32(core << 8)),  # actual MPIDR Aff1:Aff0, NOT logical ID
                ('cpu-id', u32(core)),   # descriptive: XNU assigns IDs by DT order
                ('cluster-id', u32(0)),  # descriptive; see ml_parse_cpu_topology
                ('cluster-type', u32(0)),  # homogeneous CLUSTER_TYPE_SMP, not AMP
                ('die-cluster-id', u32(0)),
                ('cluster-core-id', u32(core)),
                ('l2-cache-id', u32(core)),  # distinct per-core L2 nodes in live FDT
                ('l3-cache-id', u32(0)),     # shared L3
                ('state', cstr('running' if core == 0 else 'stopped')),
                ('timebase-frequency', u32(timebase)),
                ('clock-frequency', u32(b['freqs'][core >= b['big_from']])),
                ('bus-frequency', u32(100000000)),
                ('memory-frequency', u32(1866000000)),
                ('peripheral-frequency', u32(timebase)),
                ('fixed-frequency', u32(timebase)),
            ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], cpu_nodes)

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', cstr('master')),
        ('virtual-timer-intid', u32(QEMU_VTIMER_INTID if qemu else VTIMER_INTID)),
        ('reg', u64(gicd - SOC_BASE, gicd_sz, gicr - SOC_BASE, gicr_sz)),
    ])
    timer = node('timer', [
        ph(),
        ('device_type', cstr('timer')),
        ('compatible', cstr('arm,armv8-timer') + cstr('arm,generic-timer')),
        ('reg', u64(gicd - SOC_BASE, 0x1000)),
    ])
    arm_io_children = [gic, timer]
    if b.get('bluetooth') and not qemu:
        arm_io_children.append(bluetooth_node(ph))
    arm_io_children.append(node('wdt', [ph(), ('no-pmu', u32(1))]))
    if b.get('wlan') and not qemu:
        arm_io_children.append(node('wlan', [
            ph(),
            ('device_type', cstr('wlan')),
            ('compatible', cstr(b['wlan'])),
        ]))
    if b.get('uart') and not qemu:
        uart_base, uart_sz = b['uart']
        arm_io_children.append(node('uart0', [
            ph(),
            ('device_type', cstr('uart')),
            ('compatible', cstr('qcom,geni-uart') + cstr('arm,pl011')),
            ('reg', u64(uart_base - SOC_BASE, uart_sz)),
        ]))
    arm_io_children.append(node('disp0', [
        ph(),
        ('device_type', cstr('display-subsystem')),
        ('compatible', cstr('sweet,display')),
        ('canvas-width', u32(points[0] * display_scale)),
        ('canvas-height', u32(points[1] * display_scale)),
    ] + ([('sweet-lowpool', u64(lp_base, lp_size))] if lowpool is not None else [])))
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('sweet-io')),
        ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], arm_io_children)

    fbcon = node('sweet-fbcon', [
        ph(),
        ('reg', u64(fb, fb_size)),
        ('width', u32(fb_w)),
        ('height', u32(fb_h)),
        ('stride', u32(fb_w * 4)),
    ])

    # bsd/dev/sweet_ufs.c: the UFS host ABL leaves running (absolute physical reg).
    ufs = node('sweet-ufs', [
        ph(),
        ('compatible', cstr('qcom,ufshc')),
        ('reg', u64(ufs_base, ufs_size)),
    ])

    # bsd/dev/xun_emmc.c: the SDHCI controller ABL leaves running (absolute physical reg).
    # The driver falls back to this address when the node is absent.
    emmc_base, emmc_size = b.get('emmc', (0x04744000, 0x1000))
    emmc = node('xun-emmc', [
        ph(),
        ('compatible', cstr('qcom,sdhci-msm-v5')),
        ('reg', u64(emmc_base, emmc_size)),
    ])

    # pmap_compute_max_asids requires the node even when no overrides are set.
    # An absent pmap-max-asids property selects XNU's own MAX_ASIDS default.
    # No effaceable storage: keybagd then reads /private/var/keybags/systembag.kb
    # as a plain plist instead of decrypting it with the effaceable key.
    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])

    # MobileGestalt HomeButtonType = IODeviceTree:/buttons home-button-type
    # (D27AP / iPhone 14: 2, no Home button; absent = 0 = Home-button device).
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])

    baseband = [node('baseband', [
        ph(),
        ('device_type', cstr('baseband')),
        ('compatible', cstr(b['baseband'])),
        ('class', u32(3)),
        ('device-imei', bytes(32)),
        ('imeisv', u32(0x39)),
        ('region-sku', cstr('syscfg/RSKU/0x40,zeroes/0x40')),
    ])] if b.get('baseband') and not qemu else []

    product_props = device_product(dev['target_sub_type'])
    # Our framebuffer is sRGB 8-bit. With extended color 'P3' MobileGestalt says
    # HasExtendedColorDisplay and WebKit renders page tiles into 10-bit surfaces
    # ('w30r', 'b3a8') this kernel does not provide: Safari pages stay blank.
    if product_props:
        product_props = [(k, cstr('sRGB') if k == 'artwork-display-gamut' else v) for k, v in product_props]
        # The /product of a real device tree carries no product-type: hw.product is
        # the root node's model. MobileGestalt reads ProductType from hw.product, and
        # without a product-type next to the artwork keys it falls back to iPod.
        if not any(k == 'product-type' for k, _ in product_props):
            product_props = [('product-type', cstr(dev['model']))] + product_props
    product = [node('product', [ph()] + product_props)] if product_props else []

    sweet_uart = [node('sweet-uart', [
        ph(),
        ('compatible', cstr('qcom,geni-uart')),
        ('reg', u64(b['uart'][0], b['uart'][1])),
    ])] if b.get('uart') and not qemu else []

    # Identity of the Apple device whose userland runs here: compatible[0] is
    # hw.model/hw.target (D27AP for iPhone14,7), model is hw.machine/hw.product (iPhone14,7).
    # 'xiaomi,sweet' and 'xiaomi,sapphire' stay for SweetPlatformExpert matching.
    return node('device-tree', [
        ph(),
        ('compatible', cstr(dev['target_sub_type']) + cstr(dev['model']) + b''.join(cstr(c) for c in b['compat']) + cstr('AppleARM')),
        ('model', cstr(dev['model'])),
        ('target-type', cstr(dev['target_type'])),
        ('target-sub-type', cstr(dev['target_sub_type'])),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons] + baseband + product + ([] if qemu else ([ufs] if 'ufs' in b else [emmc])) + sweet_uart)


if __name__ == '__main__':
    board = sys.argv[sys.argv.index('--board') + 1] if '--board' in sys.argv else 'sweet'
    device = sys.argv[sys.argv.index('--device') + 1] if '--device' in sys.argv else 'iPhone14,7'
    scale = int(sys.argv[sys.argv.index('--display-scale') + 1]) if '--display-scale' in sys.argv else None
    points = tuple(int(v) for v in sys.argv[sys.argv.index('--points') + 1].split('x')) if '--points' in sys.argv else None
    lowpool = parse_lowpool(sys.argv[sys.argv.index('--lowpool') + 1]) if '--lowpool' in sys.argv else None
    amp = '--amp' in sys.argv

    out_path = None
    skip = False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a in ('--board', '--device', '--display-scale', '--points', '--lowpool'):
            skip = True
            continue
        if not a.startswith('-'):
            out_path = a
            break
    if not out_path:
        out_path = sys.argv[1] if len(sys.argv) > 1 else 'out.adt'

    root = build(qemu='--qemu' in sys.argv, highmem='--highmem' in sys.argv, board=board, display_scale=scale, points=points, lowpool=lowpool, amp=amp, device=device)
    blob = adt.serialize(root)
    open(out_path, 'wb').write(blob)
    if '--dump' in sys.argv:
        adt.dump(adt.parse(blob)[0])
    print(f'{out_path}: {len(blob)} bytes', file=sys.stderr)
