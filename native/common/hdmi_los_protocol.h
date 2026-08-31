#ifndef HDMI_LOS_PROTOCOL_H
#define HDMI_LOS_PROTOCOL_H

#include <stdint.h>

#define HDMI_LOS_MAGIC 0x48444d49u
#define HDMI_LOS_VERSION 1u
#define HDMI_LOS_MESSAGE_SIZE 160u
#define HDMI_LOS_COMPOSER_SOCKET "hdmi-los-composer-v1"
#define HDMI_LOS_BROKER_SOCKET "hdmi-los-broker-v1"

enum hdmi_los_opcode {
  HDMI_LOS_OP_STATUS = 1,
  HDMI_LOS_OP_ACQUIRE = 2,
  HDMI_LOS_OP_RELEASE = 3,
  HDMI_LOS_OP_PING = 4,
  HDMI_LOS_OP_TOGGLE = 5,
  HDMI_LOS_OP_AGENT_REGISTER = 16,
  HDMI_LOS_OP_AGENT_PREPARE = 17,
  HDMI_LOS_OP_AGENT_START = 18,
  HDMI_LOS_OP_AGENT_STOP = 19,
  HDMI_LOS_OP_AGENT_READY = 20,
  HDMI_LOS_OP_AGENT_FAILED = 21,
  HDMI_LOS_OP_RESPONSE = 0x8000
};

enum hdmi_los_state {
  HDMI_LOS_STATE_ANDROID = 0,
  HDMI_LOS_STATE_DRAINING = 1,
  HDMI_LOS_STATE_LEASED = 2,
  HDMI_LOS_STATE_RESTORING = 3,
  HDMI_LOS_STATE_UNAVAILABLE = 4,
  HDMI_LOS_STATE_ERROR = 5,
  HDMI_LOS_STATE_AGENT_READY = 6,
  HDMI_LOS_STATE_STARTING_X = 7
};

enum hdmi_los_status {
  HDMI_LOS_OK = 0,
  HDMI_LOS_ERR_PROTOCOL = -1,
  HDMI_LOS_ERR_BUSY = -2,
  HDMI_LOS_ERR_UNAVAILABLE = -3,
  HDMI_LOS_ERR_PERMISSION = -4,
  HDMI_LOS_ERR_STATE = -5,
  HDMI_LOS_ERR_IO = -6,
  HDMI_LOS_ERR_TIMEOUT = -7,
  HDMI_LOS_ERR_INCOMPATIBLE = -8,
  HDMI_LOS_ERR_AGENT = -9
};

struct hdmi_los_message {
  uint32_t magic;
  uint16_t version;
  uint16_t opcode;
  uint32_t request_id;
  int32_t status;
  uint32_t state;
  uint32_t remaining_seconds;
  uint32_t connector_id;
  uint32_t crtc_id;
  uint32_t plane_id;
  uint32_t lessee_id;
  uint32_t flags;
  char detail[116];
};

#if defined(__cplusplus)
static_assert(sizeof(hdmi_los_message) == HDMI_LOS_MESSAGE_SIZE,
              "hdmi_los_message ABI changed");
#else
_Static_assert(sizeof(struct hdmi_los_message) == HDMI_LOS_MESSAGE_SIZE,
               "hdmi_los_message ABI changed");
#endif

#endif
