// SPDX-License-Identifier: GPL-2.0-only
/* Additive lease timing, with an opt-in restricted deferred presenter. */
#include <linux/anon_inodes.h>
#include <linux/compat.h>
#include <linux/file.h>
#include <linux/fs.h>
#include <linux/ktime.h>
#include <linux/list.h>
#include <linux/math64.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/suspend.h>
#include <linux/uaccess.h>
#include <linux/utsname.h>
#include <linux/workqueue.h>
#include <drm/drm_auth.h>
#include <drm/drm_connector.h>
#include <drm/drm_crtc.h>
#include <drm/drm_device.h>
#include <drm/drm_drv.h>
#include <drm/drm_file.h>
#include <drm/drm_ioctl.h>
#include <drm/drm_mode_object.h>
#include <drm/drm_modeset_lock.h>
#include <drm/drm_plane.h>
#include <drm/drm_vblank.h>
#include "uapi.h"
#include "presenter.h"
#include "build-identity.h"

#define START_TIMEOUT_NS (500ULL * NSEC_PER_MSEC)
#define LIVE_CHECK_MS 500
#define START_CHECK_MS 10
#define MAX_SESSIONS 4

static DEFINE_MUTEX(sessions_lock);
static LIST_HEAD(sessions);
static bool suspending;
static atomic64_t next_generation = ATOMIC64_INIT(0);

/* Retain the same interface contract that the query-only probe tested. */
static const struct {
#define IMPORT_FIELD(symbol, bit) typeof(&symbol) symbol;
    HDMI_COMPANION_IMPORTS(IMPORT_FIELD)
#undef IMPORT_FIELD
} imports = {
#define IMPORT_ADDRESS(symbol, bit) .symbol = &symbol,
    HDMI_COMPANION_IMPORTS(IMPORT_ADDRESS)
#undef IMPORT_ADDRESS
};

struct timing_session {
    struct list_head node;
    struct mutex lock;
    struct delayed_work check;
    struct file *lease;
    struct drm_device *dev;
    struct drm_mode_object *connector_obj, *crtc_obj, *plane_obj;
    struct drm_crtc *crtc;
    struct drm_display_mode mode;
    struct hdmi_companion_status status;
    u64 started_ns, previous_msc, previous_timestamp_ns, frame_ns;
    bool owns_reference;
};

static u64 import_mask(void)
{
    u64 mask = 0;
#define IMPORT_PRESENT(symbol, bit) \
    if (READ_ONCE(imports.symbol)) mask |= 1ULL << bit;
    HDMI_COMPANION_IMPORTS(IMPORT_PRESENT)
#undef IMPORT_PRESENT
    return mask;
}

static void stop_locked(struct timing_session *s, u32 reason)
{
    if (s->status.state == HDMI_COMPANION_STOPPED)
        return;
    s->status.state = HDMI_COMPANION_STOPPED;
    s->status.reason = reason;
    if (s->owns_reference) {
        drm_crtc_vblank_put(s->crtc);
        s->owns_reference = false;
        s->status.reference_puts++;
    }
    /* STOP drops graphics ownership immediately, even if a status FD survives. */
    if (s->plane_obj) drm_mode_object_put(s->plane_obj);
    if (s->crtc_obj) drm_mode_object_put(s->crtc_obj);
    if (s->connector_obj) drm_mode_object_put(s->connector_obj);
    if (s->lease) fput(s->lease);
    if (s->dev) drm_dev_put(s->dev);
    s->plane_obj = s->crtc_obj = s->connector_obj = NULL;
    s->crtc = NULL;
    s->lease = NULL;
    s->dev = NULL;
}

static bool lease_live_locked(struct timing_session *s)
{
    struct drm_file *priv = s->lease->private_data;
    struct drm_mode_object *obj;
    const u32 ids[] = {s->status.connector_id, s->status.crtc_id, s->status.plane_id};
    const u32 types[] = {DRM_MODE_OBJECT_CONNECTOR, DRM_MODE_OBJECT_CRTC, DRM_MODE_OBJECT_PLANE};
    unsigned int i;

    if (!drm_is_current_master(priv)) return false;
    for (i = 0; i < ARRAY_SIZE(ids); ++i) {
        obj = drm_mode_object_find(s->dev, priv, ids[i], types[i]);
        if (!obj) return false;
        drm_mode_object_put(obj);
    }
    return true;
}

static bool same_mode(const struct drm_display_mode *a, const struct drm_display_mode *b)
{
    return a->clock == b->clock && a->hdisplay == b->hdisplay &&
           a->hsync_start == b->hsync_start && a->hsync_end == b->hsync_end &&
           a->htotal == b->htotal && a->vdisplay == b->vdisplay &&
           a->vsync_start == b->vsync_start && a->vsync_end == b->vsync_end &&
           a->vtotal == b->vtotal && a->flags == b->flags;
}

static int crtc_check_locked(struct timing_session *s, bool initial)
{
    int ret = drm_modeset_lock(&s->crtc->mutex, NULL);
    const struct drm_crtc_state *state;
    if (ret) return ret;
    state = s->crtc->state;
    if (!state || !state->active || !state->enable ||
        state->adjusted_mode.clock <= 0 || !state->adjusted_mode.htotal ||
        !state->adjusted_mode.vtotal) {
        ret = -ENODEV;
    } else if (initial) {
        s->mode = state->adjusted_mode;
        s->frame_ns = div_u64((u64)s->mode.htotal * s->mode.vtotal * NSEC_PER_MSEC,
                              s->mode.clock);
        if (!s->frame_ns || s->frame_ns > NSEC_PER_SEC) ret = -EINVAL;
    } else if (!same_mode(&s->mode, &state->adjusted_mode)) {
        ret = -ESTALE;
    }
    drm_modeset_unlock(&s->crtc->mutex);
    return ret;
}

static void check_locked(struct timing_session *s)
{
    ktime_t stamp;
    u64 now, count, ns;
    int ret;
    if (s->status.state != HDMI_COMPANION_STARTING &&
        s->status.state != HDMI_COMPANION_TIMING_VALID) return;
    if (!lease_live_locked(s)) {
        stop_locked(s, HDMI_COMPANION_REASON_LEASE_REVOKED);
        return;
    }
    ret = crtc_check_locked(s, false);
    if (ret) {
        stop_locked(s, ret == -ESTALE ? HDMI_COMPANION_REASON_MODE_CHANGED :
                                      HDMI_COMPANION_REASON_CRTC_INACTIVE);
        return;
    }
    count = drm_crtc_vblank_count_and_time(s->crtc, &stamp);
    now = ktime_get_ns();
    ns = ktime_to_ns(stamp);
    s->status.sampled_ns = now;
    s->status.msc = count;
    s->status.timestamp_ns = ns;
    if (s->status.state == HDMI_COMPANION_STARTING) {
        if (count > s->previous_msc && ns > s->previous_timestamp_ns &&
            ns <= now && now - ns <= 4 * s->frame_ns) {
            s->status.state = HDMI_COMPANION_TIMING_VALID;
        } else if (now - s->started_ns >= START_TIMEOUT_NS) {
            stop_locked(s, HDMI_COMPANION_REASON_START_TIMEOUT);
        }
    } else if (count <= s->previous_msc || ns <= s->previous_timestamp_ns ||
               ns > now || now - ns > 4 * s->frame_ns) {
        stop_locked(s, HDMI_COMPANION_REASON_TIMING_LOST);
    }
    s->previous_msc = count;
    s->previous_timestamp_ns = ns;
}

static void check_work(struct work_struct *work)
{
    struct timing_session *s = container_of(to_delayed_work(work), struct timing_session, check);
    mutex_lock(&s->lock);
    check_locked(s);
    if (s->status.state == HDMI_COMPANION_STARTING ||
        s->status.state == HDMI_COMPANION_TIMING_VALID)
        schedule_delayed_work(&s->check, msecs_to_jiffies(
            s->status.state == HDMI_COMPANION_STARTING ? START_CHECK_MS : LIVE_CHECK_MS));
    mutex_unlock(&s->lock);
}

static int session_release(struct inode *inode, struct file *file)
{
    struct timing_session *s = file->private_data;
    mutex_lock(&sessions_lock);
    list_del(&s->node);
    mutex_lock(&s->lock);
    stop_locked(s, HDMI_COMPANION_REASON_FD_CLOSED);
    mutex_unlock(&s->lock);
    mutex_unlock(&sessions_lock);
    cancel_delayed_work_sync(&s->check);
    kfree(s);
    return 0;
}

static long session_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
    struct timing_session *s = file->private_data;
    struct hdmi_companion_control control;
    struct hdmi_companion_status status;
    void __user *pointer = (void __user *)arg;
    ktime_t stamp;
    int ret = 0;
    if (cmd == HDMI_COMPANION_GET_STATUS) {
        if (copy_from_user(&status, pointer, sizeof(status))) return -EFAULT;
        if (status.size != sizeof(status) || status.abi_version != HDMI_COMPANION_ABI_VERSION ||
            memchr_inv((char *)&status + 8, 0, sizeof(status) - 8)) return -EINVAL;
        mutex_lock(&s->lock);
        status = s->status;
        mutex_unlock(&s->lock);
        return copy_to_user(pointer, &status, sizeof(status)) ? -EFAULT : 0;
    }
    if (cmd != HDMI_COMPANION_ENABLE_TIMING && cmd != HDMI_COMPANION_STOP_SESSION)
        return -ENOTTY;
    if (copy_from_user(&control, pointer, sizeof(control))) return -EFAULT;
    if (control.size != sizeof(control) || control.abi_version != HDMI_COMPANION_ABI_VERSION ||
        control.reserved) return -EINVAL;
    mutex_lock(&sessions_lock);
    mutex_lock(&s->lock);
    if (cmd == HDMI_COMPANION_STOP_SESSION) {
        stop_locked(s, HDMI_COMPANION_REASON_USER);
    } else if (s->status.state == HDMI_COMPANION_STOPPED) {
        ret = -ESTALE;
    } else if (s->status.state == HDMI_COMPANION_CREATED) {
        if (suspending) ret = -EBUSY;
        else if (!lease_live_locked(s)) ret = -EACCES;
        else ret = crtc_check_locked(s, true);
        if (!ret) ret = drm_crtc_vblank_get(s->crtc);
        if (!ret) {
            s->owns_reference = true;
            s->status.reference_gets++;
            s->started_ns = ktime_get_ns();
            s->previous_msc = drm_crtc_vblank_count_and_time(s->crtc, &stamp);
            s->previous_timestamp_ns = ktime_to_ns(stamp);
            s->status.state = HDMI_COMPANION_STARTING;
            schedule_delayed_work(&s->check, msecs_to_jiffies(START_CHECK_MS));
        }
    }
    mutex_unlock(&s->lock);
    mutex_unlock(&sessions_lock);
    return ret;
}

static const struct file_operations session_fops = {
    .owner = THIS_MODULE,
    .release = session_release,
    .unlocked_ioctl = session_ioctl,
#ifdef CONFIG_COMPAT
    .compat_ioctl = compat_ptr_ioctl,
#endif
    .llseek = no_llseek,
};

static int validate_lease(struct timing_session *s, const struct hdmi_companion_create *request)
{
    struct drm_file *priv;
    struct drm_master *master;
    struct drm_connector *connector;
    struct drm_plane *plane;
    struct inode *inode;
    s->lease = fget(request->lease_fd);
    if (!s->lease) return -EBADF;
    inode = file_inode(s->lease);
    /* DRM's registered character-device major and primary minor range are
     * validated before interpreting private_data. Function-address equality
     * across modules is unsuitable with noncanonical CFI import thunks. */
    if (!S_ISCHR(inode->i_mode) || imajor(inode) != DRM_MAJOR ||
        iminor(inode) >= 64 || !s->lease->private_data) return -EINVAL;
    priv = s->lease->private_data;
    if (!drm_is_primary_client(priv) || !priv->minor->dev ||
        priv->minor->index != iminor(inode)) return -EINVAL;
    s->dev = priv->minor->dev;
    drm_dev_get(s->dev);
    if (s->lease->f_op != s->dev->driver->fops ||
        (strcmp(s->dev->driver->name, "msm") && strcmp(s->dev->driver->name, "msm_drm")))
        return -ENODEV;
    master = drm_file_get_master(priv);
    if (!master) return -EACCES;
    if (!master->lessor) {
        drm_master_put(&master);
        return -EACCES;
    }
    drm_master_put(&master);
    if (!drm_is_current_master(priv)) return -EACCES;
    s->connector_obj = drm_mode_object_find(s->dev, priv, request->connector_id, DRM_MODE_OBJECT_CONNECTOR);
    s->crtc_obj = drm_mode_object_find(s->dev, priv, request->crtc_id, DRM_MODE_OBJECT_CRTC);
    s->plane_obj = drm_mode_object_find(s->dev, priv, request->plane_id, DRM_MODE_OBJECT_PLANE);
    if (!s->connector_obj || !s->crtc_obj || !s->plane_obj) return -EACCES;
    connector = container_of(s->connector_obj, struct drm_connector, base);
    s->crtc = container_of(s->crtc_obj, struct drm_crtc, base);
    plane = container_of(s->plane_obj, struct drm_plane, base);
    if (connector->connector_type != DRM_MODE_CONNECTOR_DisplayPort &&
        connector->connector_type != DRM_MODE_CONNECTOR_HDMIA &&
        connector->connector_type != DRM_MODE_CONNECTOR_HDMIB) return -EINVAL;
    if (plane->type != DRM_PLANE_TYPE_PRIMARY || s->crtc->primary != plane) return -EINVAL;
    return 0;
}

/* Reuse the guard's validated authority, but retain independent references:
 * broker STOP is allowed to drop its own objects before a submitted flip ends. */
int hdmi_present_bind(struct hdmi_present_binding *b, struct hdmi_companion_create *req)
{
    struct timing_session *s;
    struct file *lease=fget(req->lease_fd);
    int ret=-EACCES;
    if (!lease) return -EBADF;
    mutex_lock(&sessions_lock);
    list_for_each_entry(s,&sessions,node) {
        mutex_lock(&s->lock);
        if (s->lease == lease && s->status.state == HDMI_COMPANION_TIMING_VALID &&
            s->status.connector_id == req->connector_id && s->status.crtc_id == req->crtc_id &&
            s->status.plane_id == req->plane_id && (!req->generation || req->generation == s->status.generation) && lease_live_locked(s)) {
            b->lease=lease; b->dev=s->dev; drm_dev_get(b->dev);
            b->crtc=s->crtc; b->connector=s->connector_obj; b->crtc_obj=s->crtc_obj; b->plane=s->plane_obj;
            drm_mode_object_get(b->connector); drm_mode_object_get(b->crtc_obj); drm_mode_object_get(b->plane);
            b->generation=req->generation=s->status.generation; ret=0;
        }
        mutex_unlock(&s->lock);
        if (!ret) break;
    }
    mutex_unlock(&sessions_lock);
    if (ret) fput(lease);
    return ret;
}
bool hdmi_present_valid(const struct hdmi_present_binding *b)
{
    struct timing_session *s;
    bool valid=false;
    mutex_lock(&sessions_lock);
    list_for_each_entry(s,&sessions,node) {
        mutex_lock(&s->lock);
        valid=s->lease == b->lease && s->status.generation == b->generation &&
              s->status.state == HDMI_COMPANION_TIMING_VALID && lease_live_locked(s);
        mutex_unlock(&s->lock);
        if (valid) break;
    }
    mutex_unlock(&sessions_lock);
    return valid;
}
void hdmi_present_unbind(struct hdmi_present_binding *b)
{
    drm_mode_object_put(b->plane); drm_mode_object_put(b->crtc_obj); drm_mode_object_put(b->connector);
    fput(b->lease); drm_dev_put(b->dev);
}

static long create_session(void __user *pointer)
{
    struct hdmi_companion_create request;
    struct timing_session *s, *existing;
    struct file *session_file;
    int fd, ret, count = 0;
    if (copy_from_user(&request, pointer, sizeof(request))) return -EFAULT;
    if (request.size != sizeof(request) || request.abi_version != HDMI_COMPANION_ABI_VERSION ||
        request.session_fd != -1 || request.flags || request.generation ||
        request.reserved[0] || request.reserved[1] || request.reserved[2]) return -EINVAL;
    s = kzalloc(sizeof(*s), GFP_KERNEL);
    if (!s) return -ENOMEM;
    mutex_init(&s->lock);
    INIT_DELAYED_WORK(&s->check, check_work);
    INIT_LIST_HEAD(&s->node);
    s->status.size = sizeof(s->status);
    s->status.abi_version = HDMI_COMPANION_ABI_VERSION;
    s->status.connector_id = request.connector_id;
    s->status.crtc_id = request.crtc_id;
    s->status.plane_id = request.plane_id;
    s->status.generation = atomic64_inc_return(&next_generation);
    mutex_lock(&sessions_lock);
    if (suspending) { ret = -EBUSY; goto fail; }
    list_for_each_entry(existing, &sessions, node) {
        bool busy;
        count++;
        mutex_lock(&existing->lock);
        busy = existing->status.state != HDMI_COMPANION_STOPPED &&
               existing->status.crtc_id == request.crtc_id;
        mutex_unlock(&existing->lock);
        if (busy) {
            ret = -EBUSY;
            goto fail;
        }
    }
    if (count >= MAX_SESSIONS) { ret = -ENOSPC; goto fail; }
    ret = validate_lease(s, &request);
    if (ret) goto fail;
    fd = get_unused_fd_flags(O_CLOEXEC);
    if (fd < 0) { ret = fd; goto fail; }
    session_file = anon_inode_getfile("hdmi-companion-session", &session_fops, s, O_RDWR);
    if (IS_ERR(session_file)) { put_unused_fd(fd); ret = PTR_ERR(session_file); goto fail; }
    list_add_tail(&s->node, &sessions);
    request.session_fd = fd;
    request.generation = s->status.generation;
    mutex_unlock(&sessions_lock);
    if (copy_to_user(pointer, &request, sizeof(request))) {
        put_unused_fd(fd);
        fput(session_file); /* release removes the list entry and all ownership */
        return -EFAULT;
    }
    fd_install(fd, session_file);
    return 0;
fail:
    stop_locked(s, HDMI_COMPANION_REASON_USER);
    mutex_unlock(&sessions_lock);
    kfree(s);
    return ret;
}

static long companion_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
    struct hdmi_companion_caps request, caps = {
        .size = sizeof(caps), .abi_version = HDMI_COMPANION_ABI_VERSION,
        .features = HDMI_COMPANION_FEATURE_TIMING_GUARD | HDMI_COMPANION_FEATURE_PRESENTER,
    };
    void __user *pointer = (void __user *)arg;
    if (cmd == HDMI_COMPANION_CREATE_SESSION) return create_session(pointer);
    if (cmd == HDMI_COMPANION_CREATE_PRESENTER) return hdmi_present_create(pointer);
    if (cmd != HDMI_COMPANION_QUERY_CAPS) return -ENOTTY;
    if (copy_from_user(&request, pointer, sizeof(request))) return -EFAULT;
    if (request.size != sizeof(request) || request.abi_version != HDMI_COMPANION_ABI_VERSION ||
        request.features || request.imports || request.reserved[0] || request.reserved[1])
        return -EINVAL;
    caps.imports = import_mask();
    strscpy(caps.kernel_release, utsname()->release, sizeof(caps.kernel_release));
    strscpy(caps.build_id, HDMI_COMPANION_BUILD_ID, sizeof(caps.build_id));
    return copy_to_user(pointer, &caps, sizeof(caps)) ? -EFAULT : 0;
}

static const struct file_operations companion_fops = {
    .owner = THIS_MODULE, .unlocked_ioctl = companion_ioctl,
#ifdef CONFIG_COMPAT
    .compat_ioctl = compat_ptr_ioctl,
#endif
    .llseek = no_llseek,
};
static struct miscdevice companion_device = {
    .minor = MISC_DYNAMIC_MINOR, .name = "hdmi_companion", .fops = &companion_fops, .mode = 0600,
};

static int power_event(struct notifier_block *nb, unsigned long event, void *unused)
{
    struct timing_session *s;
    mutex_lock(&sessions_lock);
    if (event == PM_SUSPEND_PREPARE || event == PM_HIBERNATION_PREPARE || event == PM_RESTORE_PREPARE) {
        suspending = true;
        list_for_each_entry(s, &sessions, node) {
            mutex_lock(&s->lock);
            stop_locked(s, HDMI_COMPANION_REASON_SUSPEND);
            mutex_unlock(&s->lock);
        }
    } else if (event == PM_POST_SUSPEND || event == PM_POST_HIBERNATION || event == PM_POST_RESTORE) {
        suspending = false; /* Old sessions stay stopped; a new generation is required. */
    }
    mutex_unlock(&sessions_lock);
    return NOTIFY_OK;
}
static struct notifier_block power_notifier = { .notifier_call = power_event };

static int __init companion_init(void)
{
    int ret;
    BUILD_BUG_ON(sizeof(struct hdmi_companion_create) != 64);
    BUILD_BUG_ON(sizeof(struct hdmi_companion_status) != 96);
    BUILD_BUG_ON(sizeof(struct hdmi_companion_control) != 16);
    if (import_mask() != HDMI_COMPANION_REQUIRED_IMPORTS) return -ENODEV;
    BUILD_BUG_ON(sizeof(struct hdmi_present_request) != 48);
    BUILD_BUG_ON(sizeof(struct hdmi_present_status) != 64);
    ret = hdmi_present_init();
    if (ret) return ret;
    ret = register_pm_notifier(&power_notifier);
    if (ret) { hdmi_present_exit(); return ret; }
    ret = misc_register(&companion_device);
    if (ret) { unregister_pm_notifier(&power_notifier); hdmi_present_exit(); }
    return ret;
}
static void __exit companion_exit(void)
{
    misc_deregister(&companion_device);
    unregister_pm_notifier(&power_notifier);
    hdmi_present_exit();
    WARN_ON(!list_empty(&sessions)); /* Session file_operations pin this module. */
}
module_init(companion_init);
module_exit(companion_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Session-scoped HDMI lease vblank accounting companion");
MODULE_VERSION(HDMI_COMPANION_BUILD_ID);
