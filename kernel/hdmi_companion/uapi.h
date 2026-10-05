/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
#ifndef HDMI_COMPANION_UAPI_H
#define HDMI_COMPANION_UAPI_H

#include <linux/ioctl.h>
#include <linux/types.h>

#define HDMI_COMPANION_ABI_VERSION 1
#define HDMI_COMPANION_FEATURE_PROBE_ONLY (1ULL << 0)
#define HDMI_COMPANION_FEATURE_TIMING_GUARD (1ULL << 1)

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

#endif
