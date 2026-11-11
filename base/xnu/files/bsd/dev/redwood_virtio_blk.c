/*
 * redwood_virtio_blk.c - Stub for physical hardware targets (sapphire)
 */
#include <sys/types.h>
#include <stdbool.h>
#include <sys/redwood_virtio_blk.h>

bool
redwood_virtio_blk_dev(dev_t dev)
{
	(void)dev;
	return false;
}

void
redwood_virtio_blk_init(void)
{
	/* Unused on physical hardware (sapphire) */
}
