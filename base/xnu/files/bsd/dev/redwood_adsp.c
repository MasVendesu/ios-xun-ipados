/*
 * redwood_adsp.c - Stub for physical hardware targets (sapphire)
 */
#include <sys/types.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/redwood_audio.h>

bool
redwood_adsp_apr(uint8_t service, uint16_t port, uint32_t token, uint32_t opcode, const void *payload, uint32_t length,
    uint32_t wait_ms, struct redwood_apr_answer *answer)
{
	(void)service;
	(void)port;
	(void)token;
	(void)opcode;
	(void)payload;
	(void)length;
	(void)wait_ms;
	(void)answer;
	return false;
}

void
redwood_adsp_apr_events(redwood_apr_events_t handler)
{
	(void)handler;
}

void
redwood_adsp_apr_events_second(redwood_apr_events_t handler)
{
	(void)handler;
}

const uint8_t *
redwood_adsp_fw_file(const char *name, uint64_t *size)
{
	(void)name;
	if (size) {
		*size = 0;
	}
	return NULL;
}

void
redwood_adsp_fw_free(const uint8_t *data, uint64_t size)
{
	(void)data;
	(void)size;
}

void
redwood_adsp_init(void)
{
	/* Unused on physical hardware (sapphire) */
}

void
redwood_adsp_stop(void)
{
	/* Unused on physical hardware (sapphire) */
}

void
redwood_adsp_quiesce(void)
{
	/* Unused on physical hardware (sapphire) */
}

/* HakiOS Bengal RPMh / ADSP / MPSS / AOP stubs */
bool
hakios_adsp_gpr(uint32_t port, uint32_t opcode, const void *payload, uint32_t length, bool with_header, uint32_t ms, uint32_t *answer_opcode, uint32_t *words, uint32_t answer_words)
{
	(void)port; (void)opcode; (void)payload; (void)length; (void)with_header; (void)ms; (void)answer_opcode; (void)words; (void)answer_words;
	return false;
}

bool
hakios_adsp_gpr_raw(const void *packet, uint32_t length, uint32_t ms, uint32_t *answer_opcode, uint32_t *words, uint32_t answer_words)
{
	(void)packet; (void)length; (void)ms; (void)answer_opcode; (void)words; (void)answer_words;
	return false;
}

void (*hakios_adsp_gpr_event)(uint32_t opcode, uint32_t token, const uint32_t *words, uint32_t length) = NULL;

bool
hakios_aop_send(const char *text)
{
	(void)text;
	return false;
}

volatile int hakios_mpss_state = 0;

bool
hakios_rpmh_level(const char *name, uint32_t level)
{
	(void)name; (void)level;
	return true;
}

bool
hakios_rpmh_regulator(const char *name, uint32_t mv, bool enable, uint32_t mode)
{
	(void)name; (void)mv; (void)enable; (void)mode;
	return true;
}

bool
hakios_rpmh_write(const char *name, uint32_t value)
{
	(void)name; (void)value;
	return true;
}
