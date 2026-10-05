/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
#ifndef HDMI_COMPANION_UAPI_H
#define HDMI_COMPANION_UAPI_H

#include <linux/ioctl.h>
#include <linux/types.h>

#define HDMI_COMPANION_ABI_VERSION 1
#define HDMI_COMPANION_FEATURE_PROBE_ONLY (1ULL << 0)
#define HDMI_COMPANION_FEATURE_TIMING_GUARD (1ULL << 1)
#define HDMI_COMPANION_FEATURE_PRESENTER (1ULL << 2)

/* These bits report imported interfaces, never kernel addresses. */
#define HDMI_COMPANION_IMPORTS(X) \
    X(drm_crtc_vblank_get, 0) \
    X(drm_crtc_vblank_put, 1) \
    X(drm_crtc_vblank_count_and_time, 2) \
    X(drm_crtc_vblank_waitqueue, 3) \
    X(drm_mode_object_find, 4) \
    X(drm_mode_object_put, 5) \
    X(drm_file_get_master, 6) \
    X(drm_master_get, 7) \
    X(drm_master_put, 8) \
    X(drm_is_current_master, 9) \
    X(drm_ioctl, 10) \
    X(drm_dev_get, 11) \
    X(drm_dev_put, 12) \
    X(fget, 13) \
    X(fput, 14) \
    X(anon_inode_getfile, 15) \
    X(get_unused_fd_flags, 16) \
    X(put_unused_fd, 17) \
    X(fd_install, 18) \
    X(register_pm_notifier, 19) \
    X(unregister_pm_notifier, 20) \
    X(drm_modeset_lock, 21) \
    X(drm_modeset_unlock, 22) \
    X(drm_modeset_acquire_init, 23) \
    X(drm_modeset_acquire_fini, 24) \
    X(drm_modeset_backoff, 25) \
    X(drm_modeset_drop_locks, 26)

#define HDMI_COMPANION_IMPORT_COUNT 27
#define HDMI_COMPANION_REQUIRED_IMPORTS ((1ULL << HDMI_COMPANION_IMPORT_COUNT) - 1)

struct hdmi_companion_caps {
    __u32 size;
    __u32 abi_version;
    __aligned_u64 features;
    __aligned_u64 imports;
    __u32 reserved[2];
    char kernel_release[64];
    char build_id[80];
};

#define HDMI_COMPANION_QUERY_CAPS _IOWR('H', 0, struct hdmi_companion_caps)

enum hdmi_companion_state {
    HDMI_COMPANION_CREATED = 0,
    HDMI_COMPANION_STARTING = 1,
    HDMI_COMPANION_TIMING_VALID = 2,
    HDMI_COMPANION_STOPPED = 3,
};

enum hdmi_companion_stop_reason {
    HDMI_COMPANION_REASON_NONE = 0,
    HDMI_COMPANION_REASON_USER = 1,
    HDMI_COMPANION_REASON_FD_CLOSED = 2,
    HDMI_COMPANION_REASON_LEASE_REVOKED = 3,
    HDMI_COMPANION_REASON_CRTC_INACTIVE = 4,
    HDMI_COMPANION_REASON_MODE_CHANGED = 5,
    HDMI_COMPANION_REASON_TIMING_LOST = 6,
    HDMI_COMPANION_REASON_SUSPEND = 7,
    HDMI_COMPANION_REASON_START_TIMEOUT = 8,
};

struct hdmi_companion_create {
    __u32 size;
    __u32 abi_version;
    __s32 lease_fd;
    __s32 session_fd; /* Input -1; output O_CLOEXEC descriptor. */
    __u32 connector_id;
    __u32 crtc_id;
    __u32 plane_id;
    __u32 flags; /* Must be zero. */
    __aligned_u64 generation; /* Input zero; output session identity. */
    __aligned_u64 reserved[3];
};

struct hdmi_companion_control {
    __u32 size;
    __u32 abi_version;
    __aligned_u64 reserved;
};

struct hdmi_companion_status {
    __u32 size;
    __u32 abi_version;
    __u32 state;
    __u32 reason;
    __u32 connector_id;
    __u32 crtc_id;
    __u32 plane_id;
    __u32 reserved0;
    __aligned_u64 generation;
    __aligned_u64 msc;
    __aligned_u64 timestamp_ns;
    __aligned_u64 sampled_ns;
    __aligned_u64 reference_gets;
    __aligned_u64 reference_puts;
    __aligned_u64 reserved[2];
};

#define HDMI_COMPANION_CREATE_SESSION _IOWR('H', 1, struct hdmi_companion_create)
/* These operations act on the returned session FD, not the control device. */
#define HDMI_COMPANION_ENABLE_TIMING _IOW('H', 2, struct hdmi_companion_control)
#define HDMI_COMPANION_GET_STATUS _IOWR('H', 3, struct hdmi_companion_status)
#define HDMI_COMPANION_STOP_SESSION _IOW('H', 4, struct hdmi_companion_control)

/* Presenter FDs cannot control the broker's timing session. Creation requires
 * the exact lease file and object set of a currently valid timing generation. */
#define HDMI_COMPANION_CREATE_PRESENTER _IOWR('H', 5, struct hdmi_companion_create)
enum hdmi_present_state {
    HDMI_PRESENT_EMPTY=0, HDMI_PRESENT_WAITING=1, HDMI_PRESENT_SUBMITTED=2,
    HDMI_PRESENT_COMPLETE=3, HDMI_PRESENT_CANCELLED=4, HDMI_PRESENT_FAILED=5
};
struct hdmi_present_request {
    __u32 size, abi_version;
    __u32 framebuffer_id;
    __s32 acquire_fd;
    __aligned_u64 generation, serial;
    __aligned_u64 reserved[2];
};
struct hdmi_present_status {
    __u32 size, abi_version;
    __u32 state;
    __s32 error;
    __aligned_u64 generation, serial;
    __aligned_u64 accepted_ns, submitted_ns, completed_ns;
    __aligned_u64 reserved;
};
#define HDMI_COMPANION_PRESENT _IOW('H', 6, struct hdmi_present_request)
#define HDMI_COMPANION_PRESENT_STATUS _IOWR('H', 7, struct hdmi_present_status)
#define HDMI_COMPANION_CANCEL_PRESENT _IOWR('H', 8, struct hdmi_present_status)
#endif
