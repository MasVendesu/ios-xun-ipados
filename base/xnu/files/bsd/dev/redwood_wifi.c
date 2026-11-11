/*
 * redwood_wifi.c - Stub for physical hardware targets (sapphire)
 */
#include <sys/types.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/cdefs.h>
#include <sys/redwood_input.h>

int
redwood_wifi_say(const char *format, ...)
{
	(void)format;
	return 0;
}

int
redwood_wifi_tell(const char *format, ...)
{
	(void)format;
	return 0;
}

uint32_t
redwood_wifi_option(const char *name)
{
	(void)name;
	return 0;
}

const uint8_t *
redwood_wifi_file(const char *name, uint64_t *size)
{
	(void)name;
	if (size) {
		*size = 0;
	}
	return NULL;
}

bool
redwood_wifi_radio_map(uint64_t physical, uint64_t size, unsigned long long *address)
{
	(void)physical;
	(void)size;
	(void)address;
	return false;
}

bool
redwood_wifi_radio_msi(unsigned long long *address, uint32_t *data, uint32_t *first_spi)
{
	(void)address;
	(void)data;
	(void)first_spi;
	return false;
}

bool
redwood_wifi_dsp_is_up(void)
{
	return false;
}

const uint8_t *
redwood_wifi_driver_file(const char *name, uint64_t *size)
{
	(void)name;
	if (size) {
		*size = 0;
	}
	return NULL;
}

bool
redwood_wifi_radio_address(uint8_t *address)
{
	(void)address;
	return false;
}

bool
redwood_wifi_network(uint8_t *name, unsigned *name_length, uint8_t *passphrase, unsigned *passphrase_length)
{
	(void)name;
	(void)name_length;
	(void)passphrase;
	(void)passphrase_length;
	return false;
}

void
redwood_wifi_stop(void)
{
	/* Unused on physical hardware (sapphire) */
}

void
redwood_wifi_quiesce(void)
{
	/* Unused on physical hardware (sapphire) */
}

void
redwood_wifi_init(void)
{
	/* Unused on physical hardware (sapphire) */
}
