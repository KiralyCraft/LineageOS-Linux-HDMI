// SPDX-License-Identifier: GPL-2.0-only
/* Loadability probe: registers a query-only device; never opens DRM or KGSL. */
#include <linux/anon_inodes.h>
#include <linux/compat.h>
#include <linux/file.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/suspend.h>
#include <linux/uaccess.h>
#include <linux/utsname.h>
#include <drm/drm_auth.h>
#include <drm/drm_drv.h>
#include <drm/drm_ioctl.h>
#include <drm/drm_mode_object.h>
#include <drm/drm_modeset_lock.h>
#include <drm/drm_vblank.h>

#include "uapi.h"
#include "build-identity.h"

/* Typed addresses preserve CFI types. Volatile reads retain these imports
 * through full LTO without invoking any display-related function. */
static const struct {
#define IMPORT_FIELD(symbol, bit) typeof(&symbol) symbol;
    HDMI_COMPANION_IMPORTS(IMPORT_FIELD)
#undef IMPORT_FIELD
} probe_imports = {
#define IMPORT_ADDRESS(symbol, bit) .symbol = &symbol,
    HDMI_COMPANION_IMPORTS(IMPORT_ADDRESS)
#undef IMPORT_ADDRESS
};

static u64 probe_import_mask(void)
{
    u64 mask = 0;
#define IMPORT_PRESENT(symbol, bit) \
    if (READ_ONCE(probe_imports.symbol)) mask |= 1ULL << bit;
    HDMI_COMPANION_IMPORTS(IMPORT_PRESENT)
#undef IMPORT_PRESENT
    return mask;
}

static long probe_ioctl(struct file *file, unsigned int command,
                        unsigned long argument)
{
    struct hdmi_companion_caps request;
    struct hdmi_companion_caps caps = {
        .size = sizeof(caps),
        .abi_version = HDMI_COMPANION_ABI_VERSION,
        .features = HDMI_COMPANION_FEATURE_PROBE_ONLY,
    };
    void __user *pointer = (void __user *)argument;

    if (command != HDMI_COMPANION_QUERY_CAPS)
        return -ENOTTY;
    if (copy_from_user(&request, pointer, sizeof(request)))
        return -EFAULT;
    if (request.size != sizeof(request) ||
        request.abi_version != HDMI_COMPANION_ABI_VERSION ||
        request.features || request.imports ||
        request.reserved[0] || request.reserved[1])
        return -EINVAL;

    caps.imports = probe_import_mask();
    strscpy(caps.kernel_release, utsname()->release,
            sizeof(caps.kernel_release));
    strscpy(caps.build_id, HDMI_COMPANION_BUILD_ID, sizeof(caps.build_id));
    if (copy_to_user(pointer, &caps, sizeof(caps)))
        return -EFAULT;
    return 0;
}

static const struct file_operations probe_operations = {
    .owner = THIS_MODULE,
    .unlocked_ioctl = probe_ioctl,
#ifdef CONFIG_COMPAT
    .compat_ioctl = compat_ptr_ioctl,
#endif
    .llseek = no_llseek,
};

static struct miscdevice probe_device = {
    .minor = MISC_DYNAMIC_MINOR,
    .name = "hdmi_companion_probe",
    .fops = &probe_operations,
    .mode = 0600,
};

static int __init probe_init(void)
{
    if (probe_import_mask() != HDMI_COMPANION_REQUIRED_IMPORTS)
        return -ENODEV;
    return misc_register(&probe_device);
}

static void __exit probe_exit(void)
{
    misc_deregister(&probe_device);
}

module_init(probe_init);
module_exit(probe_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Query-only HDMI companion kernel compatibility probe");
MODULE_VERSION(HDMI_COMPANION_BUILD_ID);
