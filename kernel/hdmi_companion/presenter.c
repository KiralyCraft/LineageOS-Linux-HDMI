// SPDX-License-Identifier: GPL-2.0-only
/* Candidate F: one lease-scoped FIFO request, no modesets or ioctl proxying.
 * Fence callbacks only schedule work. The existing DRM file remains the sole
 * source of real flip events; this module never consumes that event stream. */
#include <linux/anon_inodes.h>
#include <linux/compat.h>
#include <linux/dma-fence.h>
#include <linux/file.h>
#include <linux/kref.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/poll.h>
#include <linux/slab.h>
#include <linux/sync_file.h>
#include <linux/uaccess.h>
#include <linux/workqueue.h>
#include <drm/drm_atomic_helper.h>
#include <drm/drm_crtc.h>
#include <drm/drm_device.h>
#include <drm/drm_file.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_framebuffer.h>
#include <drm/drm_modeset_lock.h>
#include <drm/drm_plane.h>
#include <drm/drm_vblank.h>
#include "uapi.h"
#include "presenter.h"

static struct workqueue_struct *presenter_queue;
static atomic_t presenter_count=ATOMIC_INIT(0);
struct presenter;
struct present_job {
    struct dma_fence done;
    spinlock_t fence_lock;
    struct dma_fence *acquire;
    struct dma_fence_cb acquire_cb;
    atomic_t event_result;
    atomic_t acquire_callback_ref, deadline_ref;
    struct work_struct work;
    struct delayed_work deadline;
    struct presenter *p;
    struct drm_framebuffer *fb;
    struct hdmi_present_status status;
    bool cancel, vblank;
};
struct presenter {
    struct kref refs;
    struct mutex lock;
    wait_queue_head_t changed;
    struct hdmi_present_binding binding;
    struct present_job *job;
    u64 change, seen;
    bool closing;
};

static void presenter_destroy(struct kref *ref)
{
    struct presenter *p=container_of(ref,struct presenter,refs);
    hdmi_present_unbind(&p->binding);
    kfree(p);
    atomic_dec(&presenter_count);
    module_put(THIS_MODULE);
}
static const char *present_fence_name(struct dma_fence *f) { return "hdmi-companion-present"; }
static void present_fence_release(struct dma_fence *f)
{
    struct present_job *r=container_of(f,struct present_job,done);
    struct presenter *p=r->p;
    WARN_ON(r->fb || r->vblank);
    dma_fence_put(r->acquire);
    kfree(r);
    kref_put(&p->refs,presenter_destroy);
}
static const struct dma_fence_ops present_fence_ops={
    .get_driver_name=present_fence_name,.get_timeline_name=present_fence_name,
    .release=present_fence_release,
};
static void kick_job(struct present_job *r)
{
    dma_fence_get(&r->done);
    if (!queue_work(presenter_queue,&r->work)) dma_fence_put(&r->done);
}
static void acquire_ready(struct dma_fence *f, struct dma_fence_cb *cb)
{
    struct present_job *r=container_of(cb,struct present_job,acquire_cb);
    kick_job(r);
    if (atomic_xchg(&r->acquire_callback_ref,0)) dma_fence_put(&r->done);
}
/* The DRM event owns this fence's only initial reference. Its release
 * therefore distinguishes an actual signal from event cancellation, even
 * after the presenter FD closes. No callback/reference cycle can hide a
 * cancelled kernel event and strand a vblank reference indefinitely. */
struct display_event_fence {
    struct dma_fence fence;
    spinlock_t lock;
    struct present_job *job;
};
static void display_event_release(struct dma_fence *f)
{
    struct display_event_fence *event=container_of(f,struct display_event_fence,fence);
    struct present_job *r=event->job;
    int result=dma_fence_get_status(f);
    atomic_set(&r->event_result,result ? result : -ECANCELED);
    kick_job(r);
    kfree(event);
    dma_fence_put(&r->done); /* event-owned job lifetime */
}
static const struct dma_fence_ops display_event_ops={
    .get_driver_name=present_fence_name,.get_timeline_name=present_fence_name,
    .release=display_event_release,
};

static void notify_locked(struct presenter *p)
{
    p->change++;
    wake_up_interruptible_poll(&p->changed,EPOLLIN|EPOLLRDNORM);
}
static void detach_acquire_callback(struct present_job *r)
{
    if (dma_fence_remove_callback(r->acquire,&r->acquire_cb) &&
        atomic_xchg(&r->acquire_callback_ref,0)) dma_fence_put(&r->done);
}
static void retire_locked(struct present_job *r, u32 state, int error)
{
    detach_acquire_callback(r);
    if (cancel_delayed_work(&r->deadline) && atomic_xchg(&r->deadline_ref,0))
        dma_fence_put(&r->done);
    if (r->fb) { drm_framebuffer_put(r->fb); r->fb=NULL; }
    if (r->vblank) { drm_crtc_vblank_put(r->p->binding.crtc); r->vblank=false; }
    r->status.state=state; r->status.error=error;
    r->status.completed_ns=ktime_get_ns();
    if (error) dma_fence_set_error(&r->done,error);
    dma_fence_signal(&r->done);
    notify_locked(r->p);
}

/* The existing driver's atomic checks validate layout and hardware constraints.
 * Our front-end additionally restricts this to the current full-screen primary
 * plane geometry, matching format, unchanged CRTC, and validated lease objects. */
static int submit_locked(struct present_job *r)
{
    struct hdmi_present_binding *b=&r->p->binding;
    struct drm_modeset_acquire_ctx ctx;
    struct drm_pending_vblank_event *event=NULL;
    struct display_event_fence *completion;
    struct drm_plane *plane=b->crtc->primary;
    struct drm_plane_state *ps;
    struct drm_mode_object *obj;
    int ret;
    if (!hdmi_present_valid(b)) return -EACCES;
    obj=drm_mode_object_find(b->dev,b->lease->private_data,b->crtc->base.id,DRM_MODE_OBJECT_CRTC);
    if (!obj) return -EACCES;
    drm_mode_object_put(obj);
    obj=drm_mode_object_find(b->dev,b->lease->private_data,plane->base.id,DRM_MODE_OBJECT_PLANE);
    if (!obj) return -EACCES;
    drm_mode_object_put(obj);
    drm_modeset_acquire_init(&ctx,0);
retry:
    ret=drm_modeset_lock(&b->crtc->mutex,&ctx);
    if (ret) goto out;
    ret=drm_modeset_lock(&plane->mutex,&ctx);
    if (ret) goto out;
    ps=plane->state;
    if (!b->crtc->state || !b->crtc->state->active || !ps || !ps->fb || ps->crtc != b->crtc ||
        ps->src_x || ps->src_y || ps->crtc_x || ps->crtc_y ||
        ps->src_w != (u64)r->fb->width<<16 || ps->src_h != (u64)r->fb->height<<16 ||
        ps->crtc_w != r->fb->width || ps->crtc_h != r->fb->height ||
        r->fb->format->format != ps->fb->format->format ||
        r->fb->width != b->crtc->state->adjusted_mode.hdisplay ||
        r->fb->height != b->crtc->state->adjusted_mode.vdisplay) { ret=-EINVAL; goto out; }
    ret=drm_crtc_vblank_get(b->crtc);
    if (ret) goto out;
    r->vblank=true;
    /* A deadlock retry cancels the previous attempt's reserved event and
     * releases its sole completion-fence reference synchronously. That
     * cancellation must not become the outcome of a later successful flip. */
    atomic_set(&r->event_result,0);
    event=kzalloc(sizeof(*event),GFP_KERNEL);
    if (!event) { ret=-ENOMEM; goto out; }
    event->event.base.type=DRM_EVENT_FLIP_COMPLETE;
    event->event.base.length=sizeof(event->event);
    event->event.vbl.user_data=r->status.serial;
    event->event.vbl.crtc_id=b->crtc->base.id;
    ret=drm_event_reserve_init(b->dev,b->lease->private_data,&event->base,&event->event.base);
    if (ret) { kfree(event); event=NULL; goto out; }
    completion=kzalloc(sizeof(*completion),GFP_KERNEL);
    if (!completion) { drm_event_cancel_free(b->dev,&event->base); event=NULL; ret=-ENOMEM; goto out; }
    completion->job=r; spin_lock_init(&completion->lock); dma_fence_get(&r->done);
    dma_fence_init(&completion->fence,&display_event_ops,&completion->lock,dma_fence_context_alloc(1),r->status.serial);
    event->base.fence=&completion->fence; /* transfer initial reference to DRM */
    ret=drm_atomic_helper_page_flip(b->crtc,r->fb,event,DRM_MODE_PAGE_FLIP_EVENT,&ctx);
    if (ret) { drm_event_cancel_free(b->dev,&event->base); event=NULL; }
out:
    if (ret == -EDEADLK) {
        if (r->vblank) { drm_crtc_vblank_put(b->crtc); r->vblank=false; }
        ret=drm_modeset_backoff(&ctx);
        if (!ret) goto retry;
    }
    drm_modeset_drop_locks(&ctx);
    drm_modeset_acquire_fini(&ctx);
    if (ret && r->vblank) { drm_crtc_vblank_put(b->crtc); r->vblank=false; }
    return ret;
}
static void observe_display_locked(struct present_job *r)
{
    int result=atomic_read(&r->event_result);
    if (r->status.state == HDMI_PRESENT_SUBMITTED && result)
        retire_locked(r,result > 0 ? HDMI_PRESENT_COMPLETE : HDMI_PRESENT_FAILED,result > 0 ? 0 : result);
}
static void present_work(struct work_struct *work)
{
    struct present_job *r=container_of(work,struct present_job,work);
    struct presenter *p=r->p;
    int ready,ret;
    mutex_lock(&p->lock);
    if (r->status.state == HDMI_PRESENT_SUBMITTED) {
        observe_display_locked(r);
    } else if (r->status.state == HDMI_PRESENT_WAITING) {
        ready=dma_fence_get_status(r->acquire);
        if (r->cancel || p->closing || !hdmi_present_valid(&p->binding))
            retire_locked(r,HDMI_PRESENT_CANCELLED,r->status.error ? r->status.error : -ECANCELED);
        else if (ready < 0) retire_locked(r,HDMI_PRESENT_FAILED,ready);
        else if (ready > 0) {
            ret=submit_locked(r);
            if (ret) retire_locked(r,HDMI_PRESENT_FAILED,ret);
            else {
                r->status.state=HDMI_PRESENT_SUBMITTED;
                r->status.submitted_ns=ktime_get_ns();
                detach_acquire_callback(r);
                if (cancel_delayed_work(&r->deadline) && atomic_xchg(&r->deadline_ref,0)) dma_fence_put(&r->done);
                notify_locked(p);
                /* A fast real completion may have occurred inside commit. */
                observe_display_locked(r);
            }
        }
    }
    mutex_unlock(&p->lock);
    dma_fence_put(&r->done); /* scheduled-work reference */
}
static void present_deadline(struct work_struct *work)
{
    struct present_job *r=container_of(to_delayed_work(work),struct present_job,deadline);
    mutex_lock(&r->p->lock);
    if (r->status.state == HDMI_PRESENT_WAITING) { r->cancel=true; r->status.error=-ETIMEDOUT; }
    mutex_unlock(&r->p->lock);
    kick_job(r);
    if (atomic_xchg(&r->deadline_ref,0)) dma_fence_put(&r->done);
}
static bool job_active(struct present_job *r)
{
    return r && (r->status.state == HDMI_PRESENT_WAITING || r->status.state == HDMI_PRESENT_SUBMITTED);
}
static long present_ioctl(struct file *file,unsigned int cmd,unsigned long arg)
{
    struct presenter *p=file->private_data;
    void __user *pointer=(void __user *)arg;
    struct hdmi_present_request req;
    struct hdmi_present_status input,status;
    struct present_job *r;
    int ret=0;
    if (cmd == HDMI_COMPANION_PRESENT_STATUS || cmd == HDMI_COMPANION_CANCEL_PRESENT) {
        if (copy_from_user(&input,pointer,sizeof(input))) return -EFAULT;
        if (input.size != sizeof(input) || input.abi_version != HDMI_COMPANION_ABI_VERSION ||
            input.state || input.error || input.generation || input.serial || input.accepted_ns ||
            input.submitted_ns || input.completed_ns || input.reserved) return -EINVAL;
        mutex_lock(&p->lock);
        r=p->job;
        if (r) observe_display_locked(r);
        if (cmd == HDMI_COMPANION_CANCEL_PRESENT && r && r->status.state == HDMI_PRESENT_WAITING) {
            r->cancel=true;
            /* Cancellation is serialized with submission. Return a terminal
             * state before Xorg decides whether a real DRM event can exist. */
            retire_locked(r,HDMI_PRESENT_CANCELLED,-ECANCELED);
        }
        status=r ? r->status : (struct hdmi_present_status){.size=sizeof(status),.abi_version=HDMI_COMPANION_ABI_VERSION,.generation=p->binding.generation};
        if (copy_to_user(pointer,&status,sizeof(status))) ret=-EFAULT;
        else p->seen=p->change;
        mutex_unlock(&p->lock);
        return ret;
    }
    if (cmd != HDMI_COMPANION_PRESENT) return -ENOTTY;
    if (copy_from_user(&req,pointer,sizeof(req))) return -EFAULT;
    if (req.size != sizeof(req) || req.abi_version != HDMI_COMPANION_ABI_VERSION ||
        req.generation != p->binding.generation || !req.serial || !req.framebuffer_id ||
        req.acquire_fd < 0 || req.reserved[0] || req.reserved[1]) return -EINVAL;
    mutex_lock(&p->lock);
    if (p->closing || !hdmi_present_valid(&p->binding)) { ret=-EACCES; goto out; }
    if (p->job) observe_display_locked(p->job);
    if (job_active(p->job)) { ret=-EBUSY; goto out; }
    r=kzalloc(sizeof(*r),GFP_KERNEL);
    if (!r) { ret=-ENOMEM; goto out; }
    r->acquire=sync_file_get_fence(req.acquire_fd);
    r->fb=drm_framebuffer_lookup(p->binding.dev,p->binding.lease->private_data,req.framebuffer_id);
    if (!r->acquire || !r->fb) {
        dma_fence_put(r->acquire); if (r->fb) drm_framebuffer_put(r->fb); kfree(r); ret=-EINVAL; goto out;
    }
    kref_get(&p->refs); r->p=p; spin_lock_init(&r->fence_lock);
    dma_fence_init(&r->done,&present_fence_ops,&r->fence_lock,dma_fence_context_alloc(1),req.serial);
    INIT_WORK(&r->work,present_work); INIT_DELAYED_WORK(&r->deadline,present_deadline);
    r->status=(struct hdmi_present_status){.size=sizeof(r->status),.abi_version=HDMI_COMPANION_ABI_VERSION,
        .state=HDMI_PRESENT_WAITING,.generation=req.generation,.serial=req.serial,.accepted_ns=ktime_get_ns()};
    if (p->job) dma_fence_put(&p->job->done);
    p->job=r;
    dma_fence_get(&r->done); atomic_set(&r->acquire_callback_ref,1);
    ret=dma_fence_add_callback(r->acquire,&r->acquire_cb,acquire_ready);
    if (ret && atomic_xchg(&r->acquire_callback_ref,0)) dma_fence_put(&r->done);
    dma_fence_get(&r->done); atomic_set(&r->deadline_ref,1);
    queue_delayed_work(presenter_queue,&r->deadline,msecs_to_jiffies(2000));
    notify_locked(p); kick_job(r); ret=0;
out:
    mutex_unlock(&p->lock);
    return ret;
}
static __poll_t present_poll(struct file *file,struct poll_table_struct *wait)
{
    struct presenter *p=file->private_data;
    __poll_t mask=0;
    poll_wait(file,&p->changed,wait);
    mutex_lock(&p->lock);
    if (p->seen != p->change) mask=EPOLLIN|EPOLLRDNORM;
    if (p->closing) mask|=EPOLLHUP;
    mutex_unlock(&p->lock);
    return mask;
}
static int present_release(struct inode *inode,struct file *file)
{
    struct presenter *p=file->private_data;
    mutex_lock(&p->lock); p->closing=true;
    if (p->job) {
        struct present_job *r=p->job;
        if (r->status.state == HDMI_PRESENT_WAITING) retire_locked(r,HDMI_PRESENT_CANCELLED,-ECANCELED);
        p->job=NULL; dma_fence_put(&r->done);
    }
    mutex_unlock(&p->lock);
    kref_put(&p->refs,presenter_destroy);
    return 0;
}
static const struct file_operations present_fops={.owner=THIS_MODULE,.unlocked_ioctl=present_ioctl,
#ifdef CONFIG_COMPAT
    .compat_ioctl=present_ioctl,
#endif
    .poll=present_poll,.release=present_release,.llseek=no_llseek};
long hdmi_present_create(void __user *pointer)
{
    struct hdmi_companion_create req;
    struct presenter *p;
    struct file *file;
    int fd,ret;
    if (copy_from_user(&req,pointer,sizeof(req))) return -EFAULT;
    if (req.size != sizeof(req) || req.abi_version != HDMI_COMPANION_ABI_VERSION ||
        req.lease_fd < 0 || req.session_fd != -1 || req.flags ||
        req.reserved[0] || req.reserved[1] || req.reserved[2]) return -EINVAL;
    p=kzalloc(sizeof(*p),GFP_KERNEL);
    if (!p) return -ENOMEM;
    ret=hdmi_present_bind(&p->binding,&req);
    if (ret) { kfree(p); return ret; }
    if (atomic_inc_return(&presenter_count)>4) {
        atomic_dec(&presenter_count); hdmi_present_unbind(&p->binding); kfree(p); return -ENOSPC;
    }
    if (!try_module_get(THIS_MODULE)) { atomic_dec(&presenter_count); hdmi_present_unbind(&p->binding); kfree(p); return -ENODEV; }
    kref_init(&p->refs); mutex_init(&p->lock); init_waitqueue_head(&p->changed);
    fd=get_unused_fd_flags(O_CLOEXEC);
    if (fd < 0) { ret=fd; goto fail; }
    file=anon_inode_getfile("hdmi-companion-presenter",&present_fops,p,O_RDWR);
    if (IS_ERR(file)) { put_unused_fd(fd); ret=PTR_ERR(file); goto fail; }
    req.session_fd=fd;
    if (copy_to_user(pointer,&req,sizeof(req))) { put_unused_fd(fd); fput(file); return -EFAULT; }
    fd_install(fd,file);
    return 0;
fail:
    kref_put(&p->refs,presenter_destroy);
    return ret;
}

int hdmi_present_init(void)
{
    presenter_queue=alloc_workqueue("hdmi-present",WQ_UNBOUND|WQ_MEM_RECLAIM,1);
    return presenter_queue ? 0 : -ENOMEM;
}
void hdmi_present_exit(void)
{
    WARN_ON(atomic_read(&presenter_count));
    /* Drain executing module code even after its last session pin is released. */
    destroy_workqueue(presenter_queue);
}
