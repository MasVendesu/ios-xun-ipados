/*
 * redwood_adreno.c - Stub for bring-up on sapphire
 */
#include <sys/types.h>
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

uint32_t gpu_log = 0;
uint32_t gpu_va = 0;

void
redwood_adreno_init(void)
{
	/* Unused during early boot / bring-up */
}

void
redwood_adreno_stop(void)
{
}

void
redwood_adreno_minute(void)
{
}

void
redwood_adreno_quiesce(void)
{
}

bool
redwood_gpu_ready(void)
{
	return false;
}

uint64_t
redwood_gpu_address(uint64_t size)
{
	(void)size;
	return 0;
}

void
redwood_gpu_address_free(uint64_t address, uint64_t size)
{
	(void)address;
	(void)size;
}

bool
redwood_gpu_done(uint32_t fence)
{
	(void)fence;
	return true;
}

uint32_t
redwood_gpu_last(void)
{
	return 0;
}

bool
redwood_gpu_map(uint64_t address, uint64_t phys, bool read_only)
{
	(void)address;
	(void)phys;
	(void)read_only;
	return false;
}

void
redwood_gpu_mapped(void)
{
}

bool
redwood_gpu_parameter(uint32_t which, uint64_t *value)
{
	(void)which;
	(void)value;
	return false;
}

bool
redwood_gpu_recover(void)
{
	return false;
}

void
redwood_gpu_report(const char *why)
{
	(void)why;
}

void
redwood_gpu_space(uint64_t *used, uint32_t *live, uint64_t *largest, unsigned *holes, unsigned *most, uint64_t *lost, uint32_t *refused)
{
	if (used) *used = 0;
	if (live) *live = 0;
	if (largest) *largest = 0;
	if (holes) *holes = 0;
	if (most) *most = 0;
	if (lost) *lost = 0;
	if (refused) *refused = 0;
}

void
redwood_gpu_stop(void)
{
}

uint32_t
redwood_gpu_submit(const uint64_t *addresses, const uint32_t *words, uint32_t count)
{
	(void)addresses;
	(void)words;
	(void)count;
	return 0;
}

void
redwood_gpu_to_memory(void *start, uint64_t size)
{
	(void)start;
	(void)size;
}

void
redwood_gpu_unmap(uint64_t address, uint64_t size)
{
	(void)address;
	(void)size;
}

void
redwood_gpu_worked(uint64_t *microseconds, uint32_t *submissions, uint32_t by[7])
{
	if (microseconds) *microseconds = 0;
	if (submissions) *submissions = 0;
	if (by) {
		for (int i = 0; i < 7; i++) {
			by[i] = 0;
		}
	}
}
