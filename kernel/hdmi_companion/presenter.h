/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HDMI_PRESENTER_H
#define HDMI_PRESENTER_H
struct file;
struct drm_device;
struct drm_crtc;
struct drm_mode_object;
struct hdmi_companion_create;
struct hdmi_present_binding {
    struct file *lease;
    struct drm_device *dev;
    struct drm_crtc *crtc;
    struct drm_mode_object *connector, *crtc_obj, *plane;
    unsigned long long generation;
};
int hdmi_present_bind(struct hdmi_present_binding *, struct hdmi_companion_create *);
bool hdmi_present_valid(const struct hdmi_present_binding *);
void hdmi_present_unbind(struct hdmi_present_binding *);
long hdmi_present_create(void __user *);
#endif
