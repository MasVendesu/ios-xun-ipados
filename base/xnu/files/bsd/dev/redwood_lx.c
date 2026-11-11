/*
 * redwood_lx.c - Stub for physical hardware targets (sapphire)
 */
#include <sys/types.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/redwood_lx.h>
#include <sys/redwood_input.h>

void *
lx_os_alloc(unsigned long size)
{
	(void)size;
	return NULL;
}

void
lx_os_free(void *memory, unsigned long size)
{
	(void)memory;
	(void)size;
}

void *
lx_os_radio_memory(unsigned long size, unsigned long long *address)
{
	(void)size;
	(void)address;
	return NULL;
}

void *
lx_os_map_registers(unsigned long long physical, unsigned long size)
{
	(void)physical;
	(void)size;
	return NULL;
}

void
lx_os_print(const char *text)
{
	(void)text;
}

void
lx_os_tell(const char *text)
{
	(void)text;
}

void
lx_os_panic(const char *text)
{
	(void)text;
	while (1) {
		__builtin_trap();
	}
}

unsigned long long
lx_os_nanoseconds(void)
{
	return 0;
}

void *
lx_os_thread_self(void)
{
	return NULL;
}

int
lx_os_thread(const char *name, void (*function)(void *), void *argument)
{
	(void)name;
	(void)function;
	(void)argument;
	return 0;
}

void
lx_os_lock(void)
{
}

void
lx_os_unlock(void)
{
}

int
lx_os_sleep(const void *address, unsigned long long nanoseconds)
{
	(void)address;
	(void)nanoseconds;
	return 0;
}

void
lx_os_wakeup(const void *address)
{
	(void)address;
}

void
lx_os_random(void *buffer, unsigned long size)
{
	(void)buffer;
	(void)size;
}

unsigned
lx_os_option(const char *name)
{
	(void)name;
	return 0;
}

int
lx_os_firmware(const char *name, const void **data, unsigned long *size)
{
	(void)name;
	(void)data;
	(void)size;
	return 0;
}

int
lx_os_rproc_boot(void)
{
	return 0;
}

int
lx_os_radio_address(unsigned char *address)
{
	(void)address;
	return 0;
}

int
lx_os_network(unsigned char *name, unsigned *name_length, unsigned char *passphrase, unsigned *passphrase_length)
{
	(void)name;
	(void)name_length;
	(void)passphrase;
	(void)passphrase_length;
	return 0;
}

void
lx_os_net_attach(const unsigned char *address)
{
	(void)address;
}

void
lx_os_net_link(int up)
{
	(void)up;
}

void
lx_os_net_input(const unsigned char *frame, unsigned long length)
{
	(void)frame;
	(void)length;
}

int
lx_os_irq_msi(unsigned long long *address, unsigned *data)
{
	(void)address;
	(void)data;
	return 0;
}

int
lx_os_irq_attach(unsigned vector, void (*function)(void *), void *argument)
{
	(void)vector;
	(void)function;
	(void)argument;
	return 0;
}

void
lx_os_irq_enable(unsigned vector)
{
	(void)vector;
}

void
lx_os_irq_disable(unsigned vector)
{
	(void)vector;
}

void
lx_os_irq_detach_all(void)
{
}

unsigned long long
lx_os_irq_count(unsigned vector)
{
	(void)vector;
	return 0;
}

void
lx_os_station(const struct redwood_sta_ops *ops)
{
	(void)ops;
}

void
lx_os_station_lost(int error)
{
	(void)error;
}

int
redwood_lx_start(void)
{
	return 0;
}

void
redwood_lx_stop(void)
{
}

int
redwood_lx_prepare(void)
{
	return 0;
}

void
redwood_lx_net_early(void)
{
}
