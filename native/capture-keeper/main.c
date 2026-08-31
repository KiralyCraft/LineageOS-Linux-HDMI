#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#define BUFFER_COUNT 4

struct mapped_buffer {
    void *address;
    size_t length;
};

static volatile sig_atomic_t stop_requested;

static void on_signal(int signo)
{
    (void)signo;
    stop_requested = 1;
}

static long long monotonic_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000L;
}

static int xioctl(int fd, unsigned long request, void *argument)
{
    int result;
    do {
        result = ioctl(fd, request, argument);
    } while (result < 0 && errno == EINTR);
    return result;
}

static size_t jpeg_length(const uint8_t *data, size_t size)
{
    size_t i;
    if (size < 4 || data[0] != 0xff || data[1] != 0xd8)
        return 0;
    for (i = size; i >= 2; --i) {
        if (data[i - 2] == 0xff && data[i - 1] == 0xd9)
            return i;
    }
    return 0;
}

static int save_latest(const char *path, const void *data, size_t size)
{
    char temporary[4096];
    int fd;
    const uint8_t *cursor = data;
    size_t remaining = size;

    if (snprintf(temporary, sizeof(temporary), "%s.tmp.%ld", path, (long)getpid()) >=
        (int)sizeof(temporary))
        return -1;
    fd = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0)
        return -1;
    while (remaining > 0) {
        ssize_t written = write(fd, cursor, remaining);
        if (written > 0) {
            cursor += written;
            remaining -= (size_t)written;
            continue;
        }
        if (written < 0 && errno == EINTR)
            continue;
        close(fd);
        unlink(temporary);
        return -1;
    }
    if (fsync(fd) < 0 || close(fd) < 0 || rename(temporary, path) < 0) {
        unlink(temporary);
        return -1;
    }
    return 0;
}

static void usage(FILE *stream)
{
    fprintf(stream, "Usage: hdmi-capture-keeper --device /dev/videoN --latest PATH\n");
}

int main(int argc, char **argv)
{
    const char *device = NULL;
    const char *latest = NULL;
    struct v4l2_capability capability;
    struct v4l2_format format;
    struct v4l2_streamparm parameters;
    struct v4l2_requestbuffers request;
    struct mapped_buffer buffers[BUFFER_COUNT] = {{0}};
    enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    struct sigaction action = {.sa_handler = on_signal};
    long long last_snapshot = 0;
    unsigned int invalid_frames = 0;
    int fd = -1;
    int allocated = 0;
    int i;
    int result = 1;

    for (i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--device") == 0 && i + 1 < argc)
            device = argv[++i];
        else if (strcmp(argv[i], "--latest") == 0 && i + 1 < argc)
            latest = argv[++i];
        else {
            usage(stderr);
            return 2;
        }
    }
    if (!device || !latest) {
        usage(stderr);
        return 2;
    }
    sigemptyset(&action.sa_mask);
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGHUP, &action, NULL);

    fd = open(device, O_RDWR | O_NONBLOCK | O_CLOEXEC | O_NOCTTY);
    if (fd < 0) {
        fprintf(stderr, "open %s: %s\n", device, strerror(errno));
        goto cleanup;
    }
    memset(&capability, 0, sizeof(capability));
    if (xioctl(fd, VIDIOC_QUERYCAP, &capability) < 0) {
        fprintf(stderr, "VIDIOC_QUERYCAP: %s\n", strerror(errno));
        goto cleanup;
    }
    uint32_t caps = capability.device_caps ? capability.device_caps : capability.capabilities;
    if ((caps & V4L2_CAP_VIDEO_CAPTURE) == 0 || (caps & V4L2_CAP_STREAMING) == 0) {
        fprintf(stderr, "%s is not a streaming video-capture node\n", device);
        goto cleanup;
    }
    memset(&format, 0, sizeof(format));
    format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    format.fmt.pix.width = 1920;
    format.fmt.pix.height = 1080;
    format.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG;
    format.fmt.pix.field = V4L2_FIELD_ANY;
    if (xioctl(fd, VIDIOC_S_FMT, &format) < 0) {
        fprintf(stderr, "VIDIOC_S_FMT: %s\n", strerror(errno));
        goto cleanup;
    }
    if (format.fmt.pix.pixelformat != V4L2_PIX_FMT_MJPEG) {
        fprintf(stderr, "capture node refused MJPEG format\n");
        goto cleanup;
    }
    memset(&parameters, 0, sizeof(parameters));
    parameters.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    parameters.parm.capture.timeperframe.numerator = 1;
    parameters.parm.capture.timeperframe.denominator = 30;
    if (xioctl(fd, VIDIOC_S_PARM, &parameters) < 0)
        fprintf(stderr, "warning: VIDIOC_S_PARM: %s\n", strerror(errno));

    memset(&request, 0, sizeof(request));
    request.count = BUFFER_COUNT;
    request.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    request.memory = V4L2_MEMORY_MMAP;
    if (xioctl(fd, VIDIOC_REQBUFS, &request) < 0 || request.count < 2) {
        fprintf(stderr, "VIDIOC_REQBUFS: %s\n", strerror(errno));
        goto cleanup;
    }
    allocated = request.count < BUFFER_COUNT ? (int)request.count : BUFFER_COUNT;
    for (i = 0; i < allocated; ++i) {
        struct v4l2_buffer buffer;
        memset(&buffer, 0, sizeof(buffer));
        buffer.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buffer.memory = V4L2_MEMORY_MMAP;
        buffer.index = (uint32_t)i;
        if (xioctl(fd, VIDIOC_QUERYBUF, &buffer) < 0) {
            fprintf(stderr, "VIDIOC_QUERYBUF: %s\n", strerror(errno));
            goto cleanup;
        }
        buffers[i].length = buffer.length;
        buffers[i].address = mmap(NULL, buffer.length, PROT_READ | PROT_WRITE, MAP_SHARED,
                                  fd, buffer.m.offset);
        if (buffers[i].address == MAP_FAILED) {
            buffers[i].address = NULL;
            fprintf(stderr, "mmap: %s\n", strerror(errno));
            goto cleanup;
        }
        if (xioctl(fd, VIDIOC_QBUF, &buffer) < 0) {
            fprintf(stderr, "VIDIOC_QBUF: %s\n", strerror(errno));
            goto cleanup;
        }
    }
    if (xioctl(fd, VIDIOC_STREAMON, &type) < 0) {
        fprintf(stderr, "VIDIOC_STREAMON: %s\n", strerror(errno));
        goto cleanup;
    }
    fprintf(stderr, "streaming %ux%u MJPEG from %s\n", format.fmt.pix.width,
            format.fmt.pix.height, device);

    while (!stop_requested) {
        struct pollfd pollfd = {.fd = fd, .events = POLLIN | POLLERR | POLLHUP};
        int polled = poll(&pollfd, 1, 1000);
        if (polled < 0) {
            if (errno == EINTR)
                continue;
            fprintf(stderr, "poll: %s\n", strerror(errno));
            goto streamoff;
        }
        if (polled == 0)
            continue;
        if (pollfd.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            fprintf(stderr, "capture endpoint disappeared\n");
            goto streamoff;
        }
        if (pollfd.revents & POLLIN) {
            struct v4l2_buffer buffer;
            size_t valid;
            long long now;
            memset(&buffer, 0, sizeof(buffer));
            buffer.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
            buffer.memory = V4L2_MEMORY_MMAP;
            if (xioctl(fd, VIDIOC_DQBUF, &buffer) < 0) {
                if (errno == EAGAIN)
                    continue;
                fprintf(stderr, "VIDIOC_DQBUF: %s\n", strerror(errno));
                goto streamoff;
            }
            if ((int)buffer.index >= allocated || buffer.bytesused > buffers[buffer.index].length) {
                fprintf(stderr, "capture returned an invalid buffer descriptor\n");
                goto streamoff;
            }
            valid = jpeg_length(buffers[buffer.index].address, buffer.bytesused);
            now = monotonic_ms();
            if (valid > 0 && now - last_snapshot >= 1000) {
                if (save_latest(latest, buffers[buffer.index].address, valid) < 0)
                    fprintf(stderr, "warning: cannot update %s: %s\n", latest, strerror(errno));
                else
                    last_snapshot = now;
            } else if (valid == 0 && invalid_frames++ < 5) {
                fprintf(stderr, "warning: ignored malformed startup MJPEG buffer (%u bytes)\n",
                        buffer.bytesused);
            }
            if (xioctl(fd, VIDIOC_QBUF, &buffer) < 0) {
                fprintf(stderr, "VIDIOC_QBUF: %s\n", strerror(errno));
                goto streamoff;
            }
        }
    }
    result = 0;

streamoff:
    xioctl(fd, VIDIOC_STREAMOFF, &type);
cleanup:
    for (i = 0; i < allocated; ++i) {
        if (buffers[i].address)
            munmap(buffers[i].address, buffers[i].length);
    }
    if (fd >= 0)
        close(fd);
    return result;
}
