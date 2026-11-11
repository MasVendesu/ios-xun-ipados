/*
 * redwood_sound.c - Stub for physical hardware targets (sapphire)
 */
#include <sys/types.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/redwood_audio.h>

void
redwood_sound_dsp_up(void)
{
}

void
redwood_sound_dsp_down(void)
{
}

void
redwood_sound_quiesce(void)
{
}

bool
redwood_audio_dsp_memory(uintptr_t *memory, uint64_t *dsp_address, uint32_t *bytes)
{
	(void)memory;
	(void)dsp_address;
	(void)bytes;
	return false;
}

void
redwood_audio_dsp_memory_put(void)
{
}
