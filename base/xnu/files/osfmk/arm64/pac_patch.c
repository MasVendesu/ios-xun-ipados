/*
 * pac_patch.c - Runtime Trap-and-Self-Patch Engine for ARMv8.0 (No FEAT_PAuth)
 * Part 1 Implementation for HakiOS / XNU-12377 on Snapdragon 685 (Cortex-A73/A53)
 */

/* Note: Do NOT use __attribute__((target(...))) here because Clang's target
 * attribute resets -mgeneral-regs-only and allows NEON/FP instructions inside
 * the synchronous exception handler. Build-time patch_kernel_pac.py converts
 * any compiler-emitted PAC instructions in __TEXT_EXEC to native ARMv8.0.
 */

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#if defined(__has_include) && __has_include(<arm/thread.h>)
#include <arm/thread.h>
#include <mach/machine/thread_status.h>
#include <kern/thread.h>
#include <kern/task.h>
#include <vm/vm_map.h>
#include <libkern/copyio.h>
#else
typedef struct arm_saved_state arm_saved_state_t;
typedef void *thread_t;
typedef struct task { char pad[32]; struct vm_map *map; } *task_t;
typedef struct vm_map { char pad[40]; void *pmap; } *vm_map_t;
extern thread_t current_thread(void);
extern task_t get_threadtask(thread_t th);
extern uint64_t get_saved_state_reg(arm_saved_state_t *state, unsigned int reg);
extern void set_saved_state_reg(arm_saved_state_t *state, unsigned int reg, uint64_t val);
extern uint64_t get_saved_state_sp(arm_saved_state_t *state);
extern void set_saved_state_sp(arm_saved_state_t *state, uint64_t sp);
extern uint64_t get_saved_state_pc(arm_saved_state_t *state);
extern void set_saved_state_pc(arm_saved_state_t *state, uint64_t pc);
typedef uint64_t user_addr_t;
typedef uint64_t vm_size_t;
typedef uint64_t vm_offset_t;
extern int copyin(const user_addr_t user_addr, void *kernel_addr, vm_size_t nbytes);
#endif

#include "pac_decode.h"

extern unsigned long gVirtBase, gPhysBase;
extern uint64_t phystokv(uint64_t pa);
#define PHYSTOKV(pa) ((gVirtBase != 0) ? phystokv((uint64_t)(pa)) : (uint64_t)(pa))

extern uint64_t kvtophys(vm_offset_t va);
extern void loader_log_line(const char *line);

/* Thống kê hiệu năng runtime */
static uint64_t pac_patch_count = 0;
static uint64_t pac_emul_count = 0;
static uint64_t pac_smp_races = 0;

/*
 * Ghi đè mã máy tại Physical Address và đồng bộ Cache toàn bộ Core.
 * Trả về false nếu trang đích là Read-Only (ví dụ __TEXT_EXEC sau arm_vm_init).
 */
static bool
patch_memory_phys(uint64_t pa, uint64_t va, uint32_t new_insn)
{
	volatile uint32_t *p = (gVirtBase != 0) ? (volatile uint32_t *)PHYSTOKV(pa) : (volatile uint32_t *)va;
	uintptr_t p_va = (uintptr_t)p;
	uint64_t par, daif;

	__asm__ volatile(
		"mrs %0, daif\n"
		"msr daifset, #7\n"
		"at s1e1w, %2\n"
		"isb\n"
		"mrs %1, par_el1\n"
		"msr daif, %0\n"
		: "=&r"(daif), "=&r"(par)
		: "r"(p_va)
		: "memory"
	);
	if (par & 1ULL) {
		return false;
	}

	*p = new_insn;

	__asm__ volatile (
		"dc cvau, %0 \n"
		"dc cvau, %1 \n"
		"dsb ish     \n"
		"ic ivau, %1 \n"
		"dsb ish     \n"
		"isb         \n"
		:: "r"(p_va), "r"(va) : "memory"
	);
	return true;
}

/*
 * Xử lý lệnh LDRAA / LDRAB không encode được thành 1 lệnh duy nhất (Phải Emulate)
 */
static bool
emulate_ldra(arm_saved_state_t *state, const pac_result_t *res, bool is_user)
{
	uint64_t base_addr = (res->rn == 31) ? 
		(uint64_t)get_saved_state_sp(state) : 
		(uint64_t)get_saved_state_reg(state, res->rn);

	uint64_t target_addr = base_addr + (int64_t)res->off;
	uint64_t loaded_val = 0;

	if (is_user) {
		if (copyin((user_addr_t)target_addr, &loaded_val, sizeof(loaded_val)) != 0) {
			/* Lỗi truy cập bộ nhớ userland: trả về false để kernel xử lý */
			return false;
		}
	} else {
		loaded_val = *(volatile uint64_t *)target_addr;
	}

	/* Ghi giá trị đọc được vào thanh ghi đích Xt */
	if (res->rt != 31) {
		set_saved_state_reg(state, res->rt, loaded_val);
	}

	/* Nếu là chế độ pre-index writeback (Xn!): cập nhật lại thanh ghi cơ sở Xn */
	if (res->writeback) {
		if (res->rn == 31) {
			set_saved_state_sp(state, target_addr);
		} else {
			set_saved_state_reg(state, res->rn, target_addr);
		}
	}

	/* Tiến con trỏ lệnh PC qua lệnh LDRAA (4 bytes) */
	uint64_t pc = (uint64_t)get_saved_state_pc(state);
	set_saved_state_pc(state, pc + 4);
	return true;
}

/*
 * Main Entry Point: Được gọi từ sleh_synchronous khi gặp ESR_EC_UNCATEGORIZED (0x00)
 * Trả về true: Đã xử lý / self-patched -> XNU chỉ cần return và eret để CPU chạy lại
 * Trả về false: Không phải lệnh PAC -> XNU tiếp tục đẩy sang SIGILL (EXC_BAD_INSTRUCTION)
 */
bool
pac_patch_handle_trap(arm_saved_state_t *state, bool is_user)
{
	uint64_t pc = (uint64_t)get_saved_state_pc(state);
	uint32_t insn = 0;
	uint64_t pa = 0;
	void *pmap = NULL;

	/* 1. Đọc mã lệnh insn 32-bit từ vị trí PC */
	if (is_user) {
		if (copyin((user_addr_t)pc, &insn, sizeof(insn)) != 0) {
			return false;
		}
	} else {
		insn = *(volatile uint32_t *)pc;
	}

	/*
	 * 2. SMP RACE DEFENSE:
	 * Nếu core khác vừa vá xong lệnh này tại RAM, opcode đọc được tại thời điểm này
	 * đã không còn là lệnh PAC / RCpc nữa. Ta coi như đã xử lý và quay về để CPU nạp lại!
	 */
	if (insn == A64_NOP || insn == A64_RET || insn == A64_ERET || 
	    (insn & 0xfffffc1fu) == A64_BR_BASE || (insn & 0xfffffc1fu) == A64_BLR_BASE ||
	    (insn & 0xffffffe0u) == A64_MOV_X_XZR ||
	    (insn & 0xffc00000u) == A64_LDR_UIMM ||
	    (insn & 0xffe00000u) == 0xf8400000u ||
	    (insn & 0x3ffffc00u) == 0x08dffc00u || /* LDAR (ARMv8.0) */
	    (insn & 0x3fe00c00u) == 0x38400000u || /* LDUR (ARMv8.0) */
	    (insn & 0x3fe00c00u) == 0x38000000u) { /* STUR (ARMv8.0) */
		pac_smp_races++;
		return true;
	}

	/* 3. Giải mã qua bộ PAC Decoder thuần ARM ARM (trước khi dịch địa chỉ PA!) */
	pac_result_t res;
	pac_action_t act = pac_decode(insn, &res);
	if (act == PAC_NOT_PAC) {
		return false;
	}

	if (act == PAC_PATCH) {
		if (is_user) {
			uint64_t par, daif;
			__asm__ volatile(
				"mrs %0, daif\n"
				"msr daifset, #7\n"
				"at s1e0r, %2\n"
				"isb\n"
				"mrs %1, par_el1\n"
				"msr daif, %0\n"
				: "=&r"(daif), "=&r"(par)
				: "r"(pc)
				: "memory"
			);
			if (!(par & 1ULL)) {
				pa = (par & 0x0000FFFFFFFFF000ULL) | (pc & 0xFFFULL);
			}
		} else {
			pa = (gVirtBase != 0) ? kvtophys(pc) : pc;
			if (!pa) {
				uint64_t par, daif;
				__asm__ volatile(
					"mrs %0, daif\n"
					"msr daifset, #7\n"
					"at s1e1r, %2\n"
					"isb\n"
					"mrs %1, par_el1\n"
					"msr daif, %0\n"
					: "=&r"(daif), "=&r"(par)
					: "r"(pc)
					: "memory"
				);
				if (!(par & 1ULL)) {
					pa = (par & 0x0000FFFFFFFFF000ULL) | (pc & 0xFFFULL);
				}
			}
		}
	}

	switch (act) {
	case PAC_PATCH:
		/* Ghi đè mã máy tại RAM qua Physical Mapping (nếu trang cho phép ghi) */
		if (pa && patch_memory_phys(pa, pc, res.new_insn)) {
			pac_patch_count++;
			/*
			 * KHÔNG tăng PC: CPU khi eret sẽ thực thi lại chính địa chỉ PC này,
			 * nhưng lúc này lệnh đã trở thành NOP, RET, BLR, LDR... gốc ARMv8.0!
			 */
			return true;
		}
		/*
		 * Trang đích là Read-Only (ví dụ __TEXT_EXEC sau arm_vm_init):
		 * Emulate trực tiếp lệnh thay thế res.new_insn để không bị Permission Fault!
		 */
		if (res.new_insn == A64_NOP) {
			set_saved_state_pc(state, pc + 4);
			pac_emul_count++;
			return true;
		}
		if (res.new_insn == A64_RET) {
			set_saved_state_pc(state, (uint64_t)get_saved_state_reg(state, 30));
			pac_emul_count++;
			return true;
		}
		if ((res.new_insn & 0xfffffc1fu) == A64_BR_BASE) {
			unsigned int rn = (res.new_insn >> 5) & 31;
			set_saved_state_pc(state, (uint64_t)get_saved_state_reg(state, rn));
			pac_emul_count++;
			return true;
		}
		if ((res.new_insn & 0xfffffc1fu) == A64_BLR_BASE) {
			unsigned int rn = (res.new_insn >> 5) & 31;
			uint64_t target = (uint64_t)get_saved_state_reg(state, rn);
			set_saved_state_reg(state, 30, pc + 4);
			set_saved_state_pc(state, target);
			pac_emul_count++;
			return true;
		}
		if ((res.new_insn & 0xffffffe0u) == A64_MOV_X_XZR) {
			unsigned int rd = res.new_insn & 31;
			if (rd != 31) set_saved_state_reg(state, rd, 0);
			set_saved_state_pc(state, pc + 4);
			pac_emul_count++;
			return true;
		}
		if ((res.new_insn & 0xffffffe0u) == 0xd2a00800u) {
			unsigned int rd = res.new_insn & 31;
			if (rd != 31) set_saved_state_reg(state, rd, 0x400000ULL);
			set_saved_state_pc(state, pc + 4);
			pac_emul_count++;
			return true;
		}
		return false;

	case PAC_EMULATE_LDRA:
		if (!emulate_ldra(state, &res, is_user)) {
			return false;
		}
		pac_emul_count++;
		return true;

	case PAC_NOT_PAC:
	default:
		return false;
	}
}
