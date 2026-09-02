#define _GNU_SOURCE

#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <poll.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/uio.h>
#include <unistd.h>

#include "hdmi_los_trace.h"
#include "property_cache.h"

struct hdmi_los_drm_mode_property {
  uint32_t prop_id;
  uint32_t flags;
  char name[DRM_PROP_NAME_LEN];
  int count_values;
  uint64_t *values;
  int count_enums;
  struct drm_mode_property_enum *enums;
  int count_blobs;
  uint32_t *blob_ids;
};

enum {
  kTraceTimeoutMs = 2000,
  kMaxAtomicObjects = 32,
  kMaxAtomicProperties = 128,
  kMaxCrtcConnectors = 16,
  kMaxObjectProperties = 128,
};

static int g_trace_fd = -1;
static _Atomic uint32_t g_sequence = 1;
static struct hdmi_los_property_cache g_connector_properties;
static atomic_flag g_connector_properties_lock = ATOMIC_FLAG_INIT;
static int g_suppress_connector_property_noops;
static int g_ignore_xorg_bitmask_properties;
static int g_ignore_xorg_pointer_properties;
static int g_same_mode_pageflip_fallback;
static uint32_t g_expected_connector;
static uint32_t g_expected_crtc;
static __thread int g_in_trace;

static int same_mode_timing(const struct drm_mode_modeinfo *left,
                            const struct drm_mode_modeinfo *right) {
  return left->clock == right->clock && left->hdisplay == right->hdisplay &&
         left->hsync_start == right->hsync_start && left->hsync_end == right->hsync_end &&
         left->htotal == right->htotal && left->hskew == right->hskew &&
         left->vdisplay == right->vdisplay && left->vsync_start == right->vsync_start &&
         left->vsync_end == right->vsync_end && left->vtotal == right->vtotal &&
         left->vscan == right->vscan && left->flags == right->flags;
}

static uint32_t parse_u32_environment(const char *name) {
  const char *text = getenv(name);
  if (!text || !*text) return 0;
  char *end = NULL;
  errno = 0;
  unsigned long value = strtoul(text, &end, 10);
  return errno == 0 && end && *end == '\0' && value <= UINT32_MAX ?
      (uint32_t)value : 0;
}

typedef struct hdmi_los_drm_mode_property *(*drm_mode_get_property_fn)(int, uint32_t);
typedef void (*drm_mode_free_property_fn)(struct hdmi_los_drm_mode_property *);
static drm_mode_get_property_fn g_real_drm_mode_get_property;
static drm_mode_free_property_fn g_real_drm_mode_free_property;

static int wait_readable(int fd) {
  struct pollfd descriptor = {fd, POLLIN, 0};
  for (;;) {
    int result = poll(&descriptor, 1, kTraceTimeoutMs);
    if (result < 0 && errno == EINTR) continue;
    return result > 0 && (descriptor.revents & POLLIN) != 0;
  }
}

static int exchange_record(const struct hdmi_los_trace_record *record) {
  struct hdmi_los_trace_record ack = {0};
  ssize_t sent;
  do {
    sent = send(g_trace_fd, record, sizeof(*record), MSG_NOSIGNAL);
  } while (sent < 0 && errno == EINTR);
  if (sent != (ssize_t)sizeof(*record) || !wait_readable(g_trace_fd)) return 0;
  ssize_t received;
  do {
    received = recv(g_trace_fd, &ack, sizeof(ack), MSG_WAITALL);
  } while (received < 0 && errno == EINTR);
  return received == (ssize_t)sizeof(ack) && ack.magic == HDMI_LOS_TRACE_MAGIC &&
         ack.version == HDMI_LOS_TRACE_VERSION && ack.phase == HDMI_LOS_TRACE_ACK &&
         ack.sequence == record->sequence;
}

static void initialize_record(struct hdmi_los_trace_record *record, uint16_t phase,
                              uint32_t sequence, int fd, unsigned long request,
                              const char *name) {
  memset(record, 0, sizeof(*record));
  record->magic = HDMI_LOS_TRACE_MAGIC;
  record->version = HDMI_LOS_TRACE_VERSION;
  record->phase = phase;
  record->sequence = sequence;
  record->pid = (int32_t)getpid();
  record->tid = (int32_t)syscall(SYS_gettid);
  record->fd = fd;
  record->request = request;
  if (name) snprintf(record->name, sizeof(record->name), "%s", name);
}

static int safe_copy(void *destination, uint64_t source, size_t size) {
  if (!source || !destination || !size) return 0;
  struct iovec local = {destination, size};
  struct iovec remote = {(void *)(uintptr_t)source, size};
  ssize_t result = syscall(SYS_process_vm_readv, getpid(), &local, 1, &remote, 1, 0);
  return result == (ssize_t)size;
}

static const char *request_name(unsigned long request) {
  switch (request) {
    case DRM_IOCTL_VERSION: return "VERSION";
    case DRM_IOCTL_GET_UNIQUE: return "GET_UNIQUE";
    case DRM_IOCTL_GET_MAGIC: return "GET_MAGIC";
    case DRM_IOCTL_GEM_CLOSE: return "GEM_CLOSE";
    case DRM_IOCTL_PRIME_HANDLE_TO_FD: return "PRIME_HANDLE_TO_FD";
    case DRM_IOCTL_PRIME_FD_TO_HANDLE: return "PRIME_FD_TO_HANDLE";
    case DRM_IOCTL_SET_MASTER: return "SET_MASTER";
    case DRM_IOCTL_DROP_MASTER: return "DROP_MASTER";
    case DRM_IOCTL_GET_CAP: return "GET_CAP";
    case DRM_IOCTL_SET_CLIENT_CAP: return "SET_CLIENT_CAP";
    case DRM_IOCTL_MODE_GETRESOURCES: return "MODE_GETRESOURCES";
    case DRM_IOCTL_MODE_GETCRTC: return "MODE_GETCRTC";
    case DRM_IOCTL_MODE_SETCRTC: return "MODE_SETCRTC";
    case DRM_IOCTL_MODE_CURSOR: return "MODE_CURSOR";
    case DRM_IOCTL_MODE_GETCONNECTOR: return "MODE_GETCONNECTOR";
    case DRM_IOCTL_MODE_GETENCODER: return "MODE_GETENCODER";
    case DRM_IOCTL_MODE_GETPROPERTY: return "MODE_GETPROPERTY";
    case DRM_IOCTL_MODE_SETPROPERTY: return "MODE_SETPROPERTY";
    case DRM_IOCTL_MODE_GETPROPBLOB: return "MODE_GETPROPBLOB";
    case DRM_IOCTL_MODE_GETGAMMA: return "MODE_GETGAMMA";
    case DRM_IOCTL_MODE_SETGAMMA: return "MODE_SETGAMMA";
    case DRM_IOCTL_MODE_ADDFB: return "MODE_ADDFB";
    case DRM_IOCTL_MODE_GETFB: return "MODE_GETFB";
    case DRM_IOCTL_MODE_RMFB: return "MODE_RMFB";
    case DRM_IOCTL_MODE_DIRTYFB: return "MODE_DIRTYFB";
    case DRM_IOCTL_MODE_PAGE_FLIP: return "MODE_PAGE_FLIP";
    case DRM_IOCTL_MODE_CREATE_DUMB: return "MODE_CREATE_DUMB";
    case DRM_IOCTL_MODE_MAP_DUMB: return "MODE_MAP_DUMB";
    case DRM_IOCTL_MODE_DESTROY_DUMB: return "MODE_DESTROY_DUMB";
    case DRM_IOCTL_MODE_GETPLANERESOURCES: return "MODE_GETPLANERESOURCES";
    case DRM_IOCTL_MODE_GETPLANE: return "MODE_GETPLANE";
    case DRM_IOCTL_MODE_ADDFB2: return "MODE_ADDFB2";
    case DRM_IOCTL_MODE_OBJ_GETPROPERTIES: return "MODE_OBJ_GETPROPERTIES";
    case DRM_IOCTL_MODE_CURSOR2: return "MODE_CURSOR2";
    case DRM_IOCTL_MODE_ATOMIC: return "MODE_ATOMIC";
    case DRM_IOCTL_MODE_CREATEPROPBLOB: return "MODE_CREATEPROPBLOB";
    case DRM_IOCTL_MODE_DESTROYPROPBLOB: return "MODE_DESTROYPROPBLOB";
    default: return "DRM_IOCTL";
  }
}

static void decode_record(struct hdmi_los_trace_record *record, const void *argument) {
  uint64_t address = (uint64_t)(uintptr_t)argument;
  if (!address) {
    snprintf(record->detail, sizeof(record->detail), "arg=null");
    return;
  }

  if (record->request == DRM_IOCTL_GET_CAP) {
    struct drm_get_cap value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.capability;
      record->argument[1] = value.value;
      snprintf(record->detail, sizeof(record->detail), "cap=%llu value=%llu",
               (unsigned long long)value.capability, (unsigned long long)value.value);
    }
  } else if (record->request == DRM_IOCTL_SET_CLIENT_CAP) {
    struct drm_set_client_cap value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.capability;
      record->argument[1] = value.value;
      snprintf(record->detail, sizeof(record->detail), "cap=%llu value=%llu",
               (unsigned long long)value.capability, (unsigned long long)value.value);
    }
  } else if (record->request == DRM_IOCTL_MODE_GETRESOURCES) {
    struct drm_mode_card_res value;
    if (safe_copy(&value, address, sizeof(value))) {
      snprintf(record->detail, sizeof(record->detail), "fb=%u crtc=%u conn=%u enc=%u",
               value.count_fbs, value.count_crtcs, value.count_connectors,
               value.count_encoders);
    }
  } else if (record->request == DRM_IOCTL_MODE_GETCONNECTOR) {
    struct drm_mode_get_connector value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.connector_id;
      snprintf(record->detail, sizeof(record->detail), "id=%u modes=%u props=%u enc=%u status=%u",
               value.connector_id, value.count_modes, value.count_props,
               value.count_encoders, value.connection);
    }
  } else if (record->request == DRM_IOCTL_MODE_GETENCODER) {
    struct drm_mode_get_encoder value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.encoder_id;
      record->argument[1] = value.crtc_id;
      record->argument[2] = value.possible_crtcs;
      record->argument[3] = value.possible_clones;
      snprintf(record->detail, sizeof(record->detail),
               "encoder=%u crtc=%u masks=0x%x/0x%x", value.encoder_id,
               value.crtc_id, value.possible_crtcs, value.possible_clones);
    }
  } else if (record->request == DRM_IOCTL_MODE_GETCRTC ||
             record->request == DRM_IOCTL_MODE_SETCRTC) {
    struct drm_mode_crtc value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.crtc_id;
      record->argument[1] = value.fb_id;
      record->argument[2] = value.count_connectors;
      record->argument[3] = value.mode_valid;
      snprintf(record->detail, sizeof(record->detail), "crtc=%u fb=%u xy=%u,%u conns=%u mode=%u",
               value.crtc_id, value.fb_id, value.x, value.y, value.count_connectors,
               value.mode_valid);
    }
  } else if (record->request == DRM_IOCTL_MODE_GETPLANERESOURCES) {
    struct drm_mode_get_plane_res value;
    if (safe_copy(&value, address, sizeof(value))) {
      snprintf(record->detail, sizeof(record->detail), "planes=%u", value.count_planes);
    }
  } else if (record->request == DRM_IOCTL_MODE_GETPLANE) {
    struct drm_mode_get_plane value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.plane_id;
      record->argument[1] = value.crtc_id;
      record->argument[2] = value.fb_id;
      snprintf(record->detail, sizeof(record->detail), "plane=%u crtc=%u fb=%u mask=0x%x fmts=%u",
               value.plane_id, value.crtc_id, value.fb_id, value.possible_crtcs,
               value.count_format_types);
    }
  } else if (record->request == DRM_IOCTL_MODE_OBJ_GETPROPERTIES) {
    struct drm_mode_obj_get_properties value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.obj_id;
      record->argument[1] = value.obj_type;
      record->argument[2] = value.count_props;
      snprintf(record->detail, sizeof(record->detail), "obj=%u type=0x%x props=%u",
               value.obj_id, value.obj_type, value.count_props);
    }
  } else if (record->request == DRM_IOCTL_MODE_GETPROPERTY) {
    struct drm_mode_get_property value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.prop_id;
      snprintf(record->detail, sizeof(record->detail), "prop=%u flags=0x%x values=%u enums=%u name=%.20s",
               value.prop_id, value.flags, value.count_values, value.count_enum_blobs,
               value.name);
    }
  } else if (record->request == DRM_IOCTL_MODE_SETPROPERTY) {
    struct drm_mode_connector_set_property value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.connector_id;
      record->argument[1] = value.prop_id;
      record->argument[2] = value.value;
      snprintf(record->detail, sizeof(record->detail),
               "connector=%u prop=%u value=%llu", value.connector_id,
               value.prop_id, (unsigned long long)value.value);
    }
  } else if (record->request == DRM_IOCTL_MODE_GETPROPBLOB) {
    struct drm_mode_get_blob value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.blob_id;
      record->argument[1] = value.length;
      record->argument[2] = value.data;
      snprintf(record->detail, sizeof(record->detail),
               "blob=%u length=%u data=0x%llx", value.blob_id, value.length,
               (unsigned long long)value.data);
    }
  } else if (record->request == DRM_IOCTL_MODE_CREATE_DUMB) {
    struct drm_mode_create_dumb value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.handle;
      record->argument[1] = value.size;
      snprintf(record->detail, sizeof(record->detail), "w=%u h=%u bpp=%u handle=%u pitch=%u size=%llu",
               value.width, value.height, value.bpp, value.handle, value.pitch,
               (unsigned long long)value.size);
    }
  } else if (record->request == DRM_IOCTL_MODE_MAP_DUMB) {
    struct drm_mode_map_dumb value;
    if (safe_copy(&value, address, sizeof(value))) {
      snprintf(record->detail, sizeof(record->detail), "handle=%u offset=%llu", value.handle,
               (unsigned long long)value.offset);
    }
  } else if (record->request == DRM_IOCTL_MODE_DESTROY_DUMB) {
    struct drm_mode_destroy_dumb value;
    if (safe_copy(&value, address, sizeof(value))) {
      snprintf(record->detail, sizeof(record->detail), "handle=%u", value.handle);
    }
  } else if (record->request == DRM_IOCTL_MODE_ADDFB) {
    struct drm_mode_fb_cmd value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.fb_id;
      record->argument[1] = value.handle;
      snprintf(record->detail, sizeof(record->detail), "fb=%u w=%u h=%u pitch=%u bpp=%u depth=%u handle=%u",
               value.fb_id, value.width, value.height, value.pitch, value.bpp, value.depth,
               value.handle);
    }
  } else if (record->request == DRM_IOCTL_MODE_ADDFB2) {
    struct drm_mode_fb_cmd2 value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.fb_id;
      record->argument[1] = value.handles[0];
      snprintf(record->detail, sizeof(record->detail), "fb=%u w=%u h=%u fmt=0x%x flags=0x%x h0=%u p0=%u",
               value.fb_id, value.width, value.height, value.pixel_format, value.flags,
               value.handles[0], value.pitches[0]);
    }
  } else if (record->request == DRM_IOCTL_MODE_RMFB) {
    uint32_t value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value;
      snprintf(record->detail, sizeof(record->detail), "fb=%u", value);
    }
  } else if (record->request == DRM_IOCTL_MODE_CURSOR ||
             record->request == DRM_IOCTL_MODE_CURSOR2) {
    struct drm_mode_cursor2 value = {0};
    size_t size = record->request == DRM_IOCTL_MODE_CURSOR ?
        sizeof(struct drm_mode_cursor) : sizeof(value);
    if (safe_copy(&value, address, size)) {
      record->argument[0] = value.crtc_id;
      record->argument[1] = value.handle;
      snprintf(record->detail, sizeof(record->detail), "crtc=%u flags=0x%x handle=%u wh=%ux%u xy=%d,%d",
               value.crtc_id, value.flags, value.handle, value.width, value.height,
               value.x, value.y);
    }
  } else if (record->request == DRM_IOCTL_MODE_PAGE_FLIP) {
    struct drm_mode_crtc_page_flip value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.crtc_id;
      record->argument[1] = value.fb_id;
      record->argument[2] = value.flags;
      snprintf(record->detail, sizeof(record->detail), "crtc=%u fb=%u flags=0x%x",
               value.crtc_id, value.fb_id, value.flags);
    }
  } else if (record->request == DRM_IOCTL_MODE_ATOMIC) {
    struct drm_mode_atomic value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.flags;
      record->argument[1] = value.count_objs;
      snprintf(record->detail, sizeof(record->detail), "flags=0x%x objs=%u userdata=%llu",
               value.flags, value.count_objs, (unsigned long long)value.user_data);
    }
  } else if (record->request == DRM_IOCTL_MODE_CREATEPROPBLOB) {
    struct drm_mode_create_blob value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.blob_id;
      record->argument[1] = value.length;
      snprintf(record->detail, sizeof(record->detail), "blob=%u length=%u",
               value.blob_id, value.length);
    }
  } else if (record->request == DRM_IOCTL_MODE_DESTROYPROPBLOB) {
    struct drm_mode_destroy_blob value;
    if (safe_copy(&value, address, sizeof(value))) {
      record->argument[0] = value.blob_id;
      snprintf(record->detail, sizeof(record->detail), "blob=%u", value.blob_id);
    }
  } else {
    snprintf(record->detail, sizeof(record->detail), "request=0x%lx arg=0x%llx",
             record->request, (unsigned long long)address);
  }
}

static void lock_connector_properties(void) {
  while (atomic_flag_test_and_set_explicit(&g_connector_properties_lock,
                                           memory_order_acquire)) {}
}

static void unlock_connector_properties(void) {
  atomic_flag_clear_explicit(&g_connector_properties_lock, memory_order_release);
}

static int should_suppress_connector_property_noop(
    const void *argument, struct drm_mode_connector_set_property *value) {
  if (!g_suppress_connector_property_noops ||
      !safe_copy(value, (uint64_t)(uintptr_t)argument, sizeof(*value))) return 0;
  lock_connector_properties();
  int is_noop = hdmi_los_property_cache_is_noop(
      &g_connector_properties, value->connector_id, value->prop_id, value->value);
  unlock_connector_properties();
  return is_noop;
}

static void emit_detail(uint32_t sequence, int fd, unsigned long request, const char *name,
                        uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3,
                        const char *detail) {
  struct hdmi_los_trace_record record;
  initialize_record(&record, HDMI_LOS_TRACE_DETAIL, sequence, fd, request, name);
  record.argument[0] = a0;
  record.argument[1] = a1;
  record.argument[2] = a2;
  record.argument[3] = a3;
  if (detail) snprintf(record.detail, sizeof(record.detail), "%s", detail);
  if (!exchange_record(&record)) _exit(125);
}

static void emit_setcrtc_details(uint32_t sequence, int fd, unsigned long request,
                                 const void *argument) {
  struct drm_mode_crtc value;
  if (!safe_copy(&value, (uint64_t)(uintptr_t)argument, sizeof(value)) ||
      value.count_connectors == 0 || value.count_connectors > kMaxCrtcConnectors) return;
  uint32_t connectors[kMaxCrtcConnectors];
  size_t bytes = (size_t)value.count_connectors * sizeof(connectors[0]);
  if (!safe_copy(connectors, value.set_connectors_ptr, bytes)) return;
  for (uint32_t index = 0; index < value.count_connectors; ++index) {
    char detail[64];
    snprintf(detail, sizeof(detail), "index=%u connector=%u", index, connectors[index]);
    emit_detail(sequence, fd, request, "SETCRTC_CONNECTOR", value.crtc_id, index,
                connectors[index], 0, detail);
  }
}

static void emit_atomic_details(uint32_t sequence, int fd, unsigned long request,
                                const void *argument) {
  struct drm_mode_atomic value;
  if (!safe_copy(&value, (uint64_t)(uintptr_t)argument, sizeof(value)) ||
      value.count_objs == 0) return;
  if (value.count_objs > kMaxAtomicObjects) {
    emit_detail(sequence, fd, request, "ATOMIC_TRUNCATED", value.count_objs, 0, 0, 0,
                "object count exceeds trace bound");
    return;
  }

  uint32_t objects[kMaxAtomicObjects];
  uint32_t counts[kMaxAtomicObjects];
  size_t object_bytes = (size_t)value.count_objs * sizeof(objects[0]);
  if (!safe_copy(objects, value.objs_ptr, object_bytes) ||
      !safe_copy(counts, value.count_props_ptr, object_bytes)) return;

  uint32_t total = 0;
  for (uint32_t index = 0; index < value.count_objs; ++index) {
    if (counts[index] > kMaxAtomicProperties - total) {
      emit_detail(sequence, fd, request, "ATOMIC_TRUNCATED", value.count_objs,
                  total, counts[index], index, "property count exceeds trace bound");
      return;
    }
    total += counts[index];
  }

  uint32_t properties[kMaxAtomicProperties];
  uint64_t property_values[kMaxAtomicProperties];
  if (total > 0 &&
      (!safe_copy(properties, value.props_ptr, (size_t)total * sizeof(properties[0])) ||
       !safe_copy(property_values, value.prop_values_ptr,
                  (size_t)total * sizeof(property_values[0])))) return;

  uint32_t property_index = 0;
  for (uint32_t object_index = 0; object_index < value.count_objs; ++object_index) {
    for (uint32_t local_index = 0; local_index < counts[object_index];
         ++local_index, ++property_index) {
      char detail[64];
      snprintf(detail, sizeof(detail), "obj=%u prop=%u value=%llu", objects[object_index],
               properties[property_index], (unsigned long long)property_values[property_index]);
      emit_detail(sequence, fd, request, "ATOMIC_PROPERTY", objects[object_index],
                  properties[property_index], property_values[property_index], local_index,
                  detail);
    }
  }
}

static void emit_object_property_details(uint32_t sequence, int fd,
                                         unsigned long request,
                                         const void *argument) {
  struct drm_mode_obj_get_properties value;
  if (!safe_copy(&value, (uint64_t)(uintptr_t)argument, sizeof(value)) ||
      value.count_props == 0) return;
  if (value.count_props > kMaxObjectProperties) {
    emit_detail(sequence, fd, request, "OBJECT_PROPERTIES_TRUNCATED", value.obj_id,
                value.obj_type, value.count_props, 0,
                "property count exceeds trace bound");
    return;
  }

  uint32_t properties[kMaxObjectProperties];
  uint64_t property_values[kMaxObjectProperties];
  if (!safe_copy(properties, value.props_ptr,
                 (size_t)value.count_props * sizeof(properties[0])) ||
      !safe_copy(property_values, value.prop_values_ptr,
                 (size_t)value.count_props * sizeof(property_values[0]))) return;

  for (uint32_t index = 0; index < value.count_props; ++index) {
    if (value.obj_type == DRM_MODE_OBJECT_CONNECTOR) {
      lock_connector_properties();
      hdmi_los_property_cache_store(&g_connector_properties, value.obj_id,
                                    properties[index], property_values[index]);
      unlock_connector_properties();
    }
    char detail[64];
    snprintf(detail, sizeof(detail), "obj=%u prop=%u value=%llu index=%u",
             value.obj_id, properties[index],
             (unsigned long long)property_values[index], index);
    emit_detail(sequence, fd, request, "OBJECT_PROPERTY", value.obj_id,
                properties[index], property_values[index], index, detail);
  }
}

static int try_same_mode_pageflip(int fd, const void *argument, uint32_t sequence) {
  if (!g_same_mode_pageflip_fallback || !g_expected_crtc || !g_expected_connector) return 0;
  struct drm_mode_crtc requested;
  if (!safe_copy(&requested, (uint64_t)(uintptr_t)argument, sizeof(requested)) ||
      requested.crtc_id != g_expected_crtc || requested.fb_id == 0 ||
      requested.count_connectors != 1 || !requested.mode_valid) return 0;
  uint32_t connector = 0;
  if (!safe_copy(&connector, requested.set_connectors_ptr, sizeof(connector)) ||
      connector != g_expected_connector) return 0;

  struct drm_mode_crtc current = {0};
  current.crtc_id = requested.crtc_id;
  if (syscall(SYS_ioctl, fd, DRM_IOCTL_MODE_GETCRTC, &current) != 0 ||
      !current.mode_valid || current.fb_id == 0 || current.x != requested.x ||
      current.y != requested.y || !same_mode_timing(&current.mode, &requested.mode)) return 0;

  struct drm_mode_crtc_page_flip flip = {0};
  flip.crtc_id = requested.crtc_id;
  flip.fb_id = requested.fb_id;
  if (syscall(SYS_ioctl, fd, DRM_IOCTL_MODE_PAGE_FLIP, &flip) != 0) return 0;

  for (int attempt = 0; attempt < 20; ++attempt) {
    struct drm_mode_crtc verified = {0};
    verified.crtc_id = requested.crtc_id;
    if (syscall(SYS_ioctl, fd, DRM_IOCTL_MODE_GETCRTC, &verified) == 0 &&
        verified.fb_id == requested.fb_id && verified.mode_valid &&
        same_mode_timing(&verified.mode, &requested.mode)) {
      char detail[64];
      snprintf(detail, sizeof(detail), "crtc=%u old_fb=%u new_fb=%u", requested.crtc_id,
               current.fb_id, requested.fb_id);
      emit_detail(sequence, fd, DRM_IOCTL_MODE_SETCRTC, "SETCRTC_PAGEFLIP_FALLBACK",
                  requested.crtc_id, current.fb_id, requested.fb_id,
                  g_expected_connector, detail);
      return 1;
    }
    usleep(25000);
  }
  errno = EIO;
  return -1;
}

__attribute__((constructor)) static void hdmi_los_trace_initialize(void) {
  const char *descriptor = getenv("HDMI_LOS_TRACE_FD");
  if (!descriptor || !*descriptor) return;
  char *end = NULL;
  errno = 0;
  long parsed = strtol(descriptor, &end, 10);
  if (errno != 0 || !end || *end != '\0' || parsed < 0 || parsed > INT32_MAX) _exit(125);
  g_trace_fd = (int)parsed;
  const char *suppress = getenv("HDMI_LOS_SUPPRESS_CONNECTOR_PROPERTY_NOOPS");
  g_suppress_connector_property_noops = suppress && strcmp(suppress, "1") == 0;
  const char *ignore = getenv("HDMI_LOS_IGNORE_XORG_BITMASK_PROPERTIES");
  g_ignore_xorg_bitmask_properties = ignore && strcmp(ignore, "1") == 0;
  const char *ignore_pointers = getenv("HDMI_LOS_IGNORE_XORG_POINTER_PROPERTIES");
  g_ignore_xorg_pointer_properties =
      ignore_pointers && strcmp(ignore_pointers, "1") == 0;
  const char *fallback = getenv("HDMI_LOS_SAME_MODE_PAGEFLIP_FALLBACK");
  g_same_mode_pageflip_fallback = fallback && strcmp(fallback, "1") == 0;
  g_expected_connector = parse_u32_environment("HDMI_LOS_EXPECTED_CONNECTOR");
  g_expected_crtc = parse_u32_environment("HDMI_LOS_EXPECTED_CRTC");
  g_real_drm_mode_get_property =
      (drm_mode_get_property_fn)dlsym(RTLD_NEXT, "drmModeGetProperty");
  g_real_drm_mode_free_property =
      (drm_mode_free_property_fn)dlsym(RTLD_NEXT, "drmModeFreeProperty");

  struct hdmi_los_trace_record loaded;
  initialize_record(&loaded, HDMI_LOS_TRACE_LOADED, 0, g_trace_fd, 0, "DRMTRACE_LOADED");
  snprintf(loaded.detail, sizeof(loaded.detail), "protocol=%u", HDMI_LOS_TRACE_VERSION);
  if (!exchange_record(&loaded)) _exit(125);
}

struct hdmi_los_drm_mode_property *drmModeGetProperty(int fd, uint32_t property_id) {
  if (!g_real_drm_mode_get_property) {
    g_real_drm_mode_get_property =
        (drm_mode_get_property_fn)dlsym(RTLD_NEXT, "drmModeGetProperty");
  }
  if (!g_real_drm_mode_get_property) {
    errno = ENOSYS;
    return NULL;
  }

  struct hdmi_los_drm_mode_property *property =
      g_real_drm_mode_get_property(fd, property_id);
  if (!property) return NULL;

  const char *ignored_reason = NULL;
  if (g_ignore_xorg_bitmask_properties &&
      hdmi_los_xorg_property_type_is_unsupported(property->flags,
                                                 DRM_MODE_PROP_BITMASK)) {
    ignored_reason = "IGNORED_XORG_BITMASK";
  } else if (g_ignore_xorg_pointer_properties &&
             strcmp(property->name, "RETIRE_FENCE") == 0) {
    ignored_reason = "IGNORED_XORG_POINTER";
  }
  if (!ignored_reason) {
    return property;
  }

  if (g_trace_fd >= 0 && !g_in_trace) {
    g_in_trace = 1;
    uint32_t sequence = atomic_fetch_add(&g_sequence, 1);
    char detail[64];
    snprintf(detail, sizeof(detail), "prop=%u flags=0x%x name=%.28s",
             property->prop_id, property->flags, property->name);
    emit_detail(sequence, fd, DRM_IOCTL_MODE_GETPROPERTY, ignored_reason,
                property->prop_id, property->flags, 0, 0, detail);
    g_in_trace = 0;
  }

  if (!g_real_drm_mode_free_property) {
    g_real_drm_mode_free_property =
        (drm_mode_free_property_fn)dlsym(RTLD_NEXT, "drmModeFreeProperty");
  }
  if (g_real_drm_mode_free_property) g_real_drm_mode_free_property(property);
  return NULL;
}

int ioctl(int fd, unsigned long request, ...) {
  va_list arguments;
  va_start(arguments, request);
  void *argument = va_arg(arguments, void *);
  va_end(arguments);

  if (g_trace_fd < 0 || g_in_trace || _IOC_TYPE(request) != DRM_IOCTL_BASE) {
    return (int)syscall(SYS_ioctl, fd, request, argument);
  }

  g_in_trace = 1;
  uint32_t sequence = atomic_fetch_add(&g_sequence, 1);
  struct hdmi_los_trace_record before;
  initialize_record(&before, HDMI_LOS_TRACE_BEFORE, sequence, fd, request,
                    request_name(request));
  decode_record(&before, argument);
  if (!exchange_record(&before)) _exit(125);
  if (request == DRM_IOCTL_MODE_SETCRTC) {
    emit_setcrtc_details(sequence, fd, request, argument);
  } else if (request == DRM_IOCTL_MODE_ATOMIC) {
    emit_atomic_details(sequence, fd, request, argument);
  }

  struct drm_mode_connector_set_property suppressed = {0};
  if (request == DRM_IOCTL_MODE_SETPROPERTY &&
      should_suppress_connector_property_noop(argument, &suppressed)) {
    char detail[64];
    snprintf(detail, sizeof(detail), "connector=%u prop=%u value=%llu",
             suppressed.connector_id, suppressed.prop_id,
             (unsigned long long)suppressed.value);
    emit_detail(sequence, fd, request, "SUPPRESSED_CONNECTOR_NOOP",
                suppressed.connector_id, suppressed.prop_id, suppressed.value, 0,
                detail);

    struct hdmi_los_trace_record after;
    initialize_record(&after, HDMI_LOS_TRACE_AFTER, sequence, fd, request,
                      request_name(request));
    after.result = 0;
    decode_record(&after, argument);
    if (!exchange_record(&after)) _exit(125);
    g_in_trace = 0;
    errno = 0;
    return 0;
  }

  errno = 0;
  long result = syscall(SYS_ioctl, fd, request, argument);
  int saved_errno = errno;
  if (result < 0 && saved_errno == EINVAL && request == DRM_IOCTL_MODE_SETCRTC) {
    int fallback = try_same_mode_pageflip(fd, argument, sequence);
    if (fallback > 0) {
      result = 0;
      saved_errno = 0;
    } else if (fallback < 0) {
      saved_errno = errno;
    }
  }

  struct hdmi_los_trace_record after;
  initialize_record(&after, HDMI_LOS_TRACE_AFTER, sequence, fd, request,
                    request_name(request));
  after.result = result;
  after.error = result < 0 ? saved_errno : 0;
  decode_record(&after, argument);
  if (!exchange_record(&after)) _exit(125);
  if (result >= 0 && request == DRM_IOCTL_MODE_OBJ_GETPROPERTIES) {
    emit_object_property_details(sequence, fd, request, argument);
  }
  g_in_trace = 0;
  errno = saved_errno;
  return (int)result;
}
