/* SPDX-License-Identifier: MIT
 * Read-only diagnostic: independent DRM file; no master/capability/modeset calls
 * and no access to Xorg's event queue. Snapshot the committed external buffer.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <linux/dma-buf.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>

struct hdmi_snapshot {
 uint32_t size,version,framebuffer,format,width,height,pitch,stable;
 uint64_t begin_ns,end_ns,sequence_before,sequence_after,vblank_before_ns,vblank_after_ns;
};
static uint64_t stamp(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static int result(void) {return -(errno ? errno : EIO);}
int hdmi_scanout_read(int fd,uint32_t crtc_id,uint32_t x,uint32_t y,uint32_t w,uint32_t h,
                      void *out,uint64_t capacity,struct hdmi_snapshot *meta) {
 if(!meta || meta->size!=sizeof(*meta) || meta->version!=1 || !out || !w || !h || w>3840 || h>2160 || capacity<(uint64_t)w*h*4) return -EINVAL;
 *meta=(struct hdmi_snapshot){.size=sizeof(*meta),.version=1,.begin_ns=stamp()};
 int ret=-EIO,dma=-1;void *map=MAP_FAILED;size_t map_len=0;drmModeFB2 *fb=NULL;
 drmModeCrtc *before=drmModeGetCrtc(fd,crtc_id),*after=NULL;
 if(!before) {ret=result();goto done;}
 if(!before->mode_valid || before->mode.hdisplay!=3840 || before->mode.vdisplay!=2160 || before->width!=3840 || before->height!=2160 || !before->buffer_id) {ret=-ENOTSUP;goto done;}
 if(drmCrtcGetSequence(fd,crtc_id,&meta->sequence_before,&meta->vblank_before_ns)) {ret=result();goto done;}
 fb=drmModeGetFB2(fd,before->buffer_id);if(!fb) {ret=result();goto done;}
 meta->framebuffer=fb->fb_id;meta->format=fb->pixel_format;meta->width=fb->width;meta->height=fb->height;meta->pitch=fb->pitches[0];
 if(fb->modifier!=DRM_FORMAT_MOD_LINEAR || (fb->pixel_format!=DRM_FORMAT_XRGB8888 && fb->pixel_format!=DRM_FORMAT_ARGB8888) || !fb->handles[0] || fb->handles[1] || fb->handles[2] || fb->handles[3] || fb->width!=3840 || fb->height!=2160 || fb->pitches[0]<(uint64_t)fb->width*4 || x>fb->width || w>fb->width-x || y>fb->height || h>fb->height-y) {ret=-ENOTSUP;goto done;}
 if(drmPrimeHandleToFD(fd,fb->handles[0],DRM_CLOEXEC,&dma)) {ret=result();goto done;}
 struct stat st;if(fstat(dma,&st) || st.st_size<=0) {ret=result();goto done;}
 uint64_t start=(uint64_t)fb->offsets[0]+(uint64_t)y*fb->pitches[0]+x*4;
 uint64_t end=start+(uint64_t)(h-1)*fb->pitches[0]+w*4;
 long page=sysconf(_SC_PAGESIZE);if(page<=0 || end>(uint64_t)st.st_size) {ret=-EINVAL;goto done;}
 uint64_t page_start=start&~((uint64_t)page-1);map_len=(size_t)(end-page_start);
 map=mmap(NULL,map_len,PROT_READ,MAP_SHARED,dma,(off_t)page_start);if(map==MAP_FAILED) {ret=result();goto done;}
 struct dma_buf_sync sync={.flags=DMA_BUF_SYNC_START|DMA_BUF_SYNC_READ};
 if(ioctl(dma,DMA_BUF_IOCTL_SYNC,&sync)) {ret=result();goto done;}
 for(uint32_t row=0;row<h;row++) memcpy((char*)out+(size_t)row*w*4,(const char*)map+(start-page_start)+(size_t)row*fb->pitches[0],(size_t)w*4);
 sync.flags=DMA_BUF_SYNC_END|DMA_BUF_SYNC_READ;
 if(ioctl(dma,DMA_BUF_IOCTL_SYNC,&sync)) {ret=result();goto done;}
 if(drmCrtcGetSequence(fd,crtc_id,&meta->sequence_after,&meta->vblank_after_ns)) {ret=result();goto done;}
 after=drmModeGetCrtc(fd,crtc_id);if(!after) {ret=result();goto done;}
 meta->stable=before->buffer_id==after->buffer_id && before->mode_valid==after->mode_valid && after->mode.hdisplay==3840 && after->mode.vdisplay==2160 && meta->sequence_before==meta->sequence_after;
 ret=0;
done:
 if(map!=MAP_FAILED) munmap(map,map_len);
 if(dma>=0) close(dma);
 if(fb) {
  /* GetFB2 adds handles only in this independent file. Close each distinct
   * handle exactly once; never close a framebuffer owned by the presenter. */
  for(unsigned i=0;i<4;i++) if(fb->handles[i]) {
   int duplicate=0;for(unsigned j=0;j<i;j++) duplicate|=fb->handles[j]==fb->handles[i];
   if(!duplicate) {struct drm_gem_close close_arg={.handle=fb->handles[i]};drmIoctl(fd,DRM_IOCTL_GEM_CLOSE,&close_arg);}
  }
  drmModeFreeFB2(fb);
 }
 if(before) drmModeFreeCrtc(before);
 if(after) drmModeFreeCrtc(after);
 meta->end_ns=stamp();return ret;
}
