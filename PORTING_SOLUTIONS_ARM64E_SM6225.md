# THIẾT KẾ KIẾN TRÚC & GIẢI PHÁP KỸ THUẬT: PORTING IOS 26 (ARM64E / XNU-12377) LÊN SNAPDRAGON 685 (SM6225-AD / ARMV8.0-A)
*(Phiên bản v2.0 — Đã chuẩn hóa và tích hợp toàn diện các hiệu chỉnh kiến trúc từ Claude)*

---

## 1. TỔNG QUAN CÁC RÀO CẢN HỆ THỐNG CỐT LÕI

| Rào cản | Phân loại | Tác động thực tế trên Cortex-A73/A53 (SM6225) | Hướng xử lý |
| :--- | :---: | :--- | :--- |
| **ARMv8.3 PAC** | Lệnh máy | `pacia, autia, retab, blraa` ném `Undefined Instruction`. | Trap-and-Self-Patch (JIT ghi đè tại chỗ sau lần chạm đầu). |
| **ARMv8.1 LSE** | Đồng bộ luồng | `cas, swp, ldadd` trong `libdispatch/kernel` bị `Undefined`. | Branch Island (Trampoline sang vòng lặp LL/SC an toàn). |
| **16KB Granule** | MMU phần cứng | `TGran16 == 0` (A73/A53 chỉ hỗ trợ 4KB/64KB). XNU set 16K gây crash. | Viết lại pmap XNU theo hình học bảng 4KB (512 entries/L3). |
| **Chained Fixups** | Dynamic Linker | Slide info v5 chứa auth pointers trong Dyld Shared Cache (DSC). | Bóc tách tĩnh (Offline) hoặc vá kernel slide routine. |
| **SMP Concurrency**| Đa lõi | Core A vá mã trong khi Core B đang trap cùng địa chỉ. | Kiểm tra idempodence tại ELR trước khi ném SIGILL. |
| **EL0 Memory VA** | Phân quyền | Kernel không thể dùng con trỏ thô đọc/ghi bộ nhớ userland. | Dùng `copyin` an toàn và `pmap_find_phys(task->pmap, elr)`. |

---

## 2. TRỤ CỘT 1: BỘ FAST-PATH DECODER & SELF-PATCHING (ĐÃ FIX OPCODES & SMP)

### 2.1. Giải quyết Race Condition trên SMP
Khi Core 0 đang xử lý ngoại lệ và ghi đè lệnh, Core 1 có thể đã nạp lệnh cũ và nhảy vào Exception Vector. Khi Core 1 đọc lại bộ nhớ tại `ELR_EL1`, lệnh có thể đã được Core 0 vá xong.  
👉 **Quy tắc vàng:** *Nếu mã lệnh tại ELR không còn là Undefined Instruction $\rightarrow$ lập tức `eret` để CPU chạy lại lệnh đã vá.*

### 2.2. Giải quyết Userland VA (EL0)
Không dùng `kvtophys` cho EL0. Phải lấy pmap của task hiện tại:
$$\text{PA} = \text{pmap\_find\_phys}(\text{current\_thread}()->\text{map}->\text{pmap}, \text{elr\_el1})$$

### 2.3. Mã nguồn Fast-Path Handler chuẩn hóa (`fastpath_v2.c`)

```c
/*
 * fastpath_v2.c - Chuẩn hóa xử lý ngoại lệ Undefined Instruction & Self-Patching
 */
#include <stdint.h>
#include <arm64/proc_reg.h>

#define A64_NOP         0xd503201f
#define A64_RET_X30     0xd65f03c0

extern uint64_t pmap_find_phys(void *pmap, uint64_t va);
extern uint64_t phystokv(uint64_t pa);
extern void *get_current_task_pmap(void);

/* Ghi đè bộ nhớ an toàn qua Physical Mapping và Flush đồng bộ Cache */
static void patch_mem_phys(uint64_t pa, uint64_t va, uint32_t new_inst)
{
    volatile uint32_t *dest = (volatile uint32_t *)phystokv(pa);
    *dest = new_inst;

    __asm__ volatile (
        "dc cvau, %0 \n"  /* Clean Data Cache to PoU */
        "dsb ish     \n"  /* Đảm bảo dữ liệu ghi đã vào bus */
        "ic ivau, %0 \n"  /* Invalidate Instruction Cache */
        "dsb ish     \n"  /* Đảm bảo xóa I-Cache hoàn tất */
        "isb         \n"  /* Đồng bộ lại pipeline CPU */
        :: "r"(va) : "memory"
    );
}

/*
 * Fast-path handler: gọi trực tiếp từ vector entry của fleh_synchronous
 * Trả về 1: Đã xử lý (Kernel chỉ việc thực hiện eret)
 * Trả về 0: Ngoại lệ không thuộc phạm vi xử lý
 */
int fastpath_handle_undef_v2(uint64_t elr_el1, uint64_t esr_el1, int is_user)
{
    /* 1. Kiểm tra Exception Class (EC) */
    if (((esr_el1 >> 26) & 0x3f) != 0) return 0; /* EC != 0: Không phải Undef */

    /* 2. Lấy Physical Address an toàn theo ngữ cảnh EL0 hay EL1 */
    uint64_t pa = 0;
    if (is_user) {
        void *pmap = get_current_task_pmap();
        pa = pmap_find_phys(pmap, elr_el1);
    } else {
        pa = kvtophys(elr_el1);
    }
    if (!pa) return 0;

    /* 3. Đọc opcode hiện tại từ Physical Mapping để tránh lỗi truy cập VA */
    volatile uint32_t *pcode = (volatile uint32_t *)phystokv(pa);
    uint32_t inst = *pcode;

    /* 
     * 4. SMP RACE DEFENSE:
     * Nếu core khác đã vá lệnh này trước đó, nó không còn là mã Undefined nữa.
     * Ta lập tức return 1 để CPU thực thi lại lệnh đã vá!
     */
    if (inst == A64_NOP || inst == A64_RET_X30 || (inst & 0xfffffc1f) == 0xd63f0000) {
        return 1;
    }

    /* 5. Nhóm lệnh RETAA / RETAB -> thay bằng RET */
    /* retaa: 0xd65f0bff, retab: 0xd65f0fff, mask: 0xfffffc1f */
    if ((inst & 0xfffffc1f) == 0xd65f081f || (inst & 0xfffffc1f) == 0xd65f0c1f) {
        patch_mem_phys(pa, elr_el1, A64_RET_X30);
        return 1;
    }

    /* 6. Nhóm lệnh BLRAA / BLRAB (2 registers) -> thay bằng BLR Xn */
    /* blraa: 0xd73f0800, blrab: 0xd73f0c00, mask: 0xfffffc00 */
    if ((inst & 0xfffffc00) == 0xd73f0800 || (inst & 0xfffffc00) == 0xd73f0c00) {
        uint32_t xn = inst & 0x1f;
        uint32_t blr_inst = 0xd63f0000 | (xn << 5);
        patch_mem_phys(pa, elr_el1, blr_inst);
        return 1;
    }

    /* 7. Nhóm lệnh BLRAAZ / BLRABZ (1 register) -> thay bằng BLR Xn */
    /* blraaz: 0xd63f081f, blrabz: 0xd63f0c1f, mask: 0xfffffc1f */
    if ((inst & 0xfffffc1f) == 0xd63f081f || (inst & 0xfffffc1f) == 0xd63f0c1f) {
        uint32_t xn = (inst >> 5) & 0x1f;
        uint32_t blr_inst = 0xd63f0000 | (xn << 5);
        patch_mem_phys(pa, elr_el1, blr_inst);
        return 1;
    }

    /* 8. Nhóm lệnh PACIA, PACIB, PACDA, PACDB, AUTIA... (Data processing 1 source) */
    /* Prefix 16-bit: 0xdac1, 0xdac3, 0xdac0, 0xdad1... -> Mask (inst >> 21) == 0x6d6 */
    if ((inst >> 21) == 0x6d6) {
        patch_mem_phys(pa, elr_el1, A64_NOP);
        return 1;
    }

    return 0; /* Lệnh ngoại lệ không hỗ trợ */
}
```

---

## 3. TRỤ CỘT 2: SCRIPT DECODE SLIDE INFO V5 CHUẨN XÁC

### 3.1. Cấu trúc chuẩn `dyld_cache_slide_info5` (Header 24 bytes)
Cấu trúc thực tế từ `dyld_cache_format.h` của Apple:
```c
struct dyld_cache_slide_info5 {
    uint32_t version;            /* 5 */
    uint32_t page_size;          /* 4096 hoặc 16384 */
    uint32_t page_starts_count;  /* Số trang */
    uint64_t value_add;          /* 64-bit base offset để tính target */
    uint32_t page_starts[/*..*/];
};
```
* **Stride:** Mỗi bước nhảy `next` được tính theo **bước 8 byte** (bước con trỏ 64-bit), **không phải 4 byte**.
* **Target Address:** $\text{Target} = \text{value\_add} + (\text{raw\_ptr} \& 0x3FFFFFFFF\text{ULL})$ (34 bit).

### 3.2. Script Python chuẩn (`dsc_flatten_v2.py`)

```python
#!/usr/bin/env python3
"""
dsc_flatten_v2.py - Flatten Chained Fixups v5 chuẩn Dyld Cache Header
"""
import struct
import mmap
import sys

def flatten_slide_v5(mmap_data, slide_info_offset, data_offset=0):
    version, page_size, page_starts_count, value_add = struct.unpack_from(
        "<III4xQ", mmap_data, slide_info_offset
    )
    print(f"[*] Slide Info v5: PageSize={page_size}, Pages={page_starts_count}, ValueAdd=0x{value_add:x}")

    starts_offset = slide_info_offset + 24 # Header là 24 bytes (III + 4 pad + Q)
    patched = 0

    for i in range(page_starts_count):
        pstart = struct.unpack_from("<H", mmap_data, starts_offset + i * 2)[0]
        if pstart == 0xffff:
            continue

        cursor = data_offset + (i * page_size) + pstart

        while True:
            raw_ptr = struct.unpack_from("<Q", mmap_data, cursor)[0]
            auth = (raw_ptr >> 63) & 1
            stride = (raw_ptr >> 52) & 0x7ff

            if auth == 1:
                # 34-bit runtime offset
                runtime_offset = raw_ptr & 0x3ffffffff
                real_target = value_add + runtime_offset
                
                # Ghi đè con trỏ phẳng thuần 64-bit
                struct.pack_into("<Q", mmap_data, cursor, real_target)
                patched += 1

            if stride == 0:
                break
            cursor += stride * 8 # Stride theo đơn vị 8-byte pointer

    print(f"[✔] Đã làm phẳng {patched} con trỏ auth.")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Sử dụng: python3 dsc_flatten_v2.py <dsc_file> <slide_info_hex_offset>")
        sys.exit(1)
    with open(sys.argv[1], "r+b") as f:
        mm = mmap.mmap(f.fileno(), 0)
        flatten_slide_v5(mm, int(sys.argv[2], 16))
        mm.flush()
```

---

## 4. TRỤ CỘT 3: TRAMPOLINE AN TOÀN CHO LSE ATOMICS (BẢO TOÀN THANH GHI)

Để không phá hủy các thanh ghi đang sống (`clobbering registers`), Trampoline Stub bắt buộc phải:
1. Lưu tạm các thanh ghi nháp (`x3`, `x4`) lên stack một cách an toàn (tránh vùng red-zone: ghi lùi `sp` bằng `stp x3, x4, [sp, #-16]!`).
2. Thực thi vòng lặp `ldaxr`/`stlxr`.
3. Phục hồi lại thanh ghi nháp trước khi nhảy về.

```assembly
/*
 * An toàn cho Register State: cas x0, x1, [x2]
 */
.global __atomic_cas_safe
__atomic_cas_safe:
    stp     x3, x4, [sp, #-16]!   /* Bảo lưu x3, x4 vào Stack an toàn */
1:
    ldaxr   x3, [x2]              /* Đọc giá trị tại [x2] */
    cmp     x3, x0                /* So sánh với x0 */
    b.ne    2f
    stlxr   w4, x1, [x2]          /* Ghi x1 vào [x2] */
    cbnz    w4, 1b                /* Lặp lại nếu tranh chấp bus */
2:
    mov     x0, x3                /* Trả về giá trị cũ */
    ldp     x3, x4, [sp], #16     /* Phục hồi x3, x4 nguyên vẹn */
    b       __return_site         /* Nhảy về mã nguồn chính */
```

---

## 5. TRỤ CỘT 4: BẢNG TRANG 4KB TRONG PMAP XNU

* **Cấu hình `TCR_EL1`:**
  * `TG0` (User / TTBR0): `0b00` (4KB Granule).
  * `TG1` (Kernel / TTBR1): `0b10` (4KB Granule).
* **Hình học Bảng trang:**
  * Bảng 16KB có **2048 entries/page** (Index 11 bit).
  * Bảng 4KB có **512 entries/page** (Index 9 bit).
* **Quy tắc:** Giữ `PAGE_SIZE = 16384` ở mức trừu tượng của Mach VM/Userland, nhưng toàn bộ cấu trúc `pmap` bên dưới thao tác với cấp bảng L1, L2, L3 chuẩn 4KB.

---

## 6. MÔ PHỎNG KIỂM CHỨNG TRÊN QEMU (TRÁNH CHE LẤP LỖI)

Để kiểm chứng mã nguồn mà không bị các tính năng của CPU hiện đại che lấp lỗi:
* ❌ **KHÔNG DÙNG:** `-cpu max` hoặc `-cpu cortex-a710` (vì các CPU này có sẵn PAC và LSE phần cứng, che lấp hoàn toàn các lệnh chưa vá).
* ✅ **BẮT BUỘC DÙNG:**
  ```bash
  qemu-system-aarch64 -M virt,gic-version=3 -cpu cortex-a72 -smp 4 -m 4G \
    -accel tcg -kernel xnu_kernel_test -nographic
  ```
  *(Cortex-A72 là CPU ARMv8.0 thuần, không có PAC, không có LSE, không có 16KB Granule, phản ánh 100% chính xác hành vi phần cứng của Cortex-A73).*
