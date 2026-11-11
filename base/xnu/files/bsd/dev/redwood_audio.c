/*
 * redwood_audio.c - Stub for physical hardware targets (sapphire)
 */
#include <sys/types.h>
#include <stdbool.h>
#include <sys/redwood_audio.h>

bool
redwood_audio_recording_fed(void)
{
	return false;
}

bool
redwood_audio_recording_mic(void)
{
	return false;
}

void
redwood_audio_record_fed(const int16_t *frames, uint32_t count)
{
	(void)frames;
	(void)count;
}

void
redwood_audio_set_sink(const char *name, redwood_audio_sink_t sink)
{
	(void)name;
	(void)sink;
}

bool
redwood_audio_volume(uint32_t *below, bool *muted, uint32_t *generation)
{
	(void)below;
	(void)muted;
	(void)generation;
	return false;
}

void
redwood_audio_init(void)
{
	/* Unused on physical hardware (sapphire) */
}
