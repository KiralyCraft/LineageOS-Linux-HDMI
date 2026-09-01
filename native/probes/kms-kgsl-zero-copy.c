#define _GNU_SOURCE

/*
 * Probe whether an SDE KMS dumb buffer can make the complete zero-copy trip:
 *
 *   KMS dumb allocation -> PRIME dma-buf -> KGSL import -> KMS framebuffer
 *
 * This is deliberately a capability probe, not a display takeover.  It never
 * modesets or changes a plane.  Run it as root while Android owns the display.
 */

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <drm.h>
#include <drm_fourcc.h>
#include <drm_mode.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

#define KGSL_IOC_TYPE 0x09
#define KGSL_USER_MEM_TYPE_DMABUF 0x00000003U

struct kgsl_gpuobj_import {
    uint64_t priv;
    uint64_t priv_len;
    uint64_t flags;
    uint32_t type;
    uint32_t id;
};

struct kgsl_gpuobj_import_dma_buf {
    int fd;
};

struct kgsl_gpuobj_free {
    uint64_t flags;
    uint64_t priv;
    uint32_t id;
    uint32_t type;
    uint32_t len;
};

#define IOCTL_KGSL_GPUOBJ_FREE \
    _IOW(KGSL_IOC_TYPE, 0x46, struct kgsl_gpuobj_free)
#define IOCTL_KGSL_GPUOBJ_IMPORT \
    _IOWR(KGSL_IOC_TYPE, 0x48, struct kgsl_gpuobj_import)

static void report_errno(const char *operation)
{
    fprintf(stderr, "FAIL %-24s errno=%d (%s)\n", operation, errno,
            strerror(errno));
}

int main(int argc, char **argv)
{
    const char *card_path = argc > 1 ? argv[1] : "/dev/dri/card0";
    const uint32_t width = 64;
    const uint32_t height = 64;
    struct drm_mode_create_dumb create = {
        .width = width,
        .height = height,
        .bpp = 32,
    };
    struct drm_mode_destroy_dumb destroy = {0};
    struct drm_mode_map_dumb map = {0};
    struct kgsl_gpuobj_import_dma_buf import_dmabuf = { .fd = -1 };
    struct kgsl_gpuobj_import import = {
        .priv = (uintptr_t)&import_dmabuf,
        .priv_len = sizeof(import_dmabuf),
        .type = KGSL_USER_MEM_TYPE_DMABUF,
    };
    struct kgsl_gpuobj_free free_object = {0};
    uint32_t handles[4] = {0};
    uint32_t pitches[4] = {0};
    uint32_t offsets[4] = {0};
    uint64_t modifiers[4] = {DRM_FORMAT_MOD_LINEAR, 0, 0, 0};
    uint32_t framebuffer = 0;
    void *mapping = MAP_FAILED;
    int card = -1;
    int kgsl = -1;
    int prime = -1;
    int result = 1;

    card = open(card_path, O_RDWR | O_CLOEXEC);
    if (card < 0) {
        report_errno("open card0");
        goto out;
    }

    if (ioctl(card, DRM_IOCTL_MODE_CREATE_DUMB, &create) != 0) {
        report_errno("DRM create dumb");
        goto out;
    }
    destroy.handle = create.handle;
    printf("PASS create dumb              device=%s handle=%u pitch=%u size=%" PRIu64 "\n",
           card_path, create.handle, create.pitch, (uint64_t)create.size);

    if (drmPrimeHandleToFD(card, create.handle,
                           DRM_CLOEXEC | DRM_RDWR, &prime) != 0) {
        report_errno("DRM PRIME export");
        goto out;
    }
    import_dmabuf.fd = prime;
    printf("PASS PRIME export             fd=%d\n", prime);

    map.handle = create.handle;
    if (ioctl(card, DRM_IOCTL_MODE_MAP_DUMB, &map) != 0) {
        report_errno("DRM map dumb");
        goto out;
    }
    mapping = mmap(NULL, create.size, PROT_READ | PROT_WRITE, MAP_SHARED,
                   card, map.offset);
    if (mapping == MAP_FAILED) {
        report_errno("mmap dumb");
        goto out;
    }
    memset(mapping, 0x5a, create.size);
    printf("PASS CPU map                  offset=%" PRIu64 "\n",
           (uint64_t)map.offset);

    kgsl = open("/dev/kgsl-3d0", O_RDWR | O_CLOEXEC);
    if (kgsl < 0) {
        report_errno("open kgsl");
        goto out;
    }
    if (ioctl(kgsl, IOCTL_KGSL_GPUOBJ_IMPORT, &import) != 0) {
        report_errno("KGSL dma-buf import");
        goto out;
    }
    free_object.id = import.id;
    printf("PASS KGSL import              id=%u\n", import.id);

    handles[0] = create.handle;
    pitches[0] = create.pitch;
    if (drmModeAddFB2WithModifiers(card, width, height, DRM_FORMAT_XRGB8888,
                                   handles, pitches, offsets, modifiers,
                                   &framebuffer, DRM_MODE_FB_MODIFIERS) != 0) {
        if (drmModeAddFB2(card, width, height, DRM_FORMAT_XRGB8888,
                          handles, pitches, offsets, &framebuffer, 0) != 0) {
            report_errno("DRM add framebuffer");
            goto out;
        }
        printf("PASS KMS framebuffer          id=%u (implicit linear)\n",
               framebuffer);
    } else {
        printf("PASS KMS framebuffer          id=%u (explicit linear)\n",
               framebuffer);
    }

    puts("RESULT zero-copy allocation path is supported");
    result = 0;

out:
    if (framebuffer != 0)
        drmModeRmFB(card, framebuffer);
    if (free_object.id != 0 && kgsl >= 0 &&
        ioctl(kgsl, IOCTL_KGSL_GPUOBJ_FREE, &free_object) != 0)
        report_errno("KGSL free");
    if (mapping != MAP_FAILED)
        munmap(mapping, create.size);
    if (prime >= 0)
        close(prime);
    if (destroy.handle != 0 && card >= 0 &&
        ioctl(card, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy) != 0)
        report_errno("DRM destroy dumb");
    if (kgsl >= 0)
        close(kgsl);
    if (card >= 0)
        close(card);
    return result;
}
