#ifndef HDMI_LOS_TRACE_H
#define HDMI_LOS_TRACE_H

#include <stdint.h>

#define HDMI_LOS_TRACE_MAGIC 0x54524345u
#define HDMI_LOS_TRACE_VERSION 2u

enum hdmi_los_trace_flags {
  /* The agent has independently verified Xorg's scanout.  The interposer may
   * stop relaying routine ioctls, while retaining its compatibility shims and
   * full tracing for later MODE_SETCRTC requests. */
  HDMI_LOS_TRACE_ACK_STEADY = 1u << 0
};

enum hdmi_los_trace_phase {
  HDMI_LOS_TRACE_LOADED = 1,
  HDMI_LOS_TRACE_BEFORE = 2,
  HDMI_LOS_TRACE_DETAIL = 3,
  HDMI_LOS_TRACE_AFTER = 4,
  HDMI_LOS_TRACE_ACK = 5
};

struct hdmi_los_trace_record {
  uint32_t magic;
  uint16_t version;
  uint16_t phase;
  uint32_t sequence;
  int32_t pid;
  int32_t tid;
  int32_t fd;
  int32_t error;
  uint32_t flags;
  uint64_t request;
  int64_t result;
  uint64_t argument[4];
  char name[32];
  char detail[64];
};

#if defined(__cplusplus)
static_assert(sizeof(hdmi_los_trace_record) == 176,
              "hdmi_los_trace_record ABI changed");
#else
_Static_assert(sizeof(struct hdmi_los_trace_record) == 176,
               "hdmi_los_trace_record ABI changed");
#endif

#endif
