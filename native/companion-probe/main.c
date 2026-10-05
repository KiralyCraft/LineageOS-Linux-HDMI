// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include "../../kernel/hdmi_companion/uapi.h"

_Static_assert(sizeof(struct hdmi_companion_caps) == 176, "caps ABI changed");

static void reset_caps(struct hdmi_companion_caps *caps)
{
    memset(caps, 0, sizeof(*caps));
    caps->size = sizeof(*caps);
    caps->abi_version = HDMI_COMPANION_ABI_VERSION;
}

static int expect_error(int fd, unsigned long command, void *argument,
                        int expected, const char *name)
{
    errno = 0;
    if (ioctl(fd, command, argument) != -1 || errno != expected) {
        fprintf(stderr, "FAIL: %s, expected errno %d, got %d\n",
                name, expected, errno);
        return 1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    struct hdmi_companion_caps caps;
    const char *device = "/dev/hdmi_companion_probe";
    const char *expected_release = NULL, *expected_build = NULL;
    int fd, errors = 0;

    for (int index = 1; index < argc; ++index) {
        if (!strcmp(argv[index], "--help")) {
            puts("usage: hdmi-companion-probe [--device PATH] "
                 "[--expect-release RELEASE] [--expect-build ID]");
            return 0;
        }
        if (index + 1 >= argc) return 2;
        if (!strcmp(argv[index], "--device")) device = argv[++index];
        else if (!strcmp(argv[index], "--expect-release")) expected_release = argv[++index];
        else if (!strcmp(argv[index], "--expect-build")) expected_build = argv[++index];
        else return 2;
    }

    fd = open(device, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        fprintf(stderr, "open %s: %s\n", device, strerror(errno));
        return 1;
    }
    reset_caps(&caps);
    if (ioctl(fd, HDMI_COMPANION_QUERY_CAPS, &caps) != 0) {
        perror("QUERY_CAPS");
        close(fd);
        return 1;
    }
    if (caps.size != sizeof(caps) || caps.abi_version != HDMI_COMPANION_ABI_VERSION ||
        caps.features != HDMI_COMPANION_FEATURE_PROBE_ONLY ||
        caps.imports != HDMI_COMPANION_REQUIRED_IMPORTS ||
        caps.reserved[0] || caps.reserved[1] ||
        !memchr(caps.kernel_release, 0, sizeof(caps.kernel_release)) ||
        !memchr(caps.build_id, 0, sizeof(caps.build_id))) {
        fputs("FAIL: incompatible probe capabilities\n", stderr);
        close(fd);
        return 1;
    }
    printf("kernel_release=%s\nbuild_id=%s\nabi_version=%u\nimports=%llu\n",
           caps.kernel_release, caps.build_id, caps.abi_version,
           (unsigned long long)caps.imports);
    if ((expected_release && strcmp(expected_release, caps.kernel_release)) ||
        (expected_build && strcmp(expected_build, caps.build_id))) {
        fputs("FAIL: probe identity mismatch\n", stderr);
        close(fd);
        return 1;
    }

    reset_caps(&caps);
    caps.size--;
    errors += expect_error(fd, HDMI_COMPANION_QUERY_CAPS, &caps, EINVAL, "wrong size");
    reset_caps(&caps);
    caps.abi_version++;
    errors += expect_error(fd, HDMI_COMPANION_QUERY_CAPS, &caps, EINVAL, "wrong ABI");
    reset_caps(&caps);
    caps.reserved[1] = 1;
    errors += expect_error(fd, HDMI_COMPANION_QUERY_CAPS, &caps, EINVAL, "reserved field");
    reset_caps(&caps);
    caps.features = 1;
    errors += expect_error(fd, HDMI_COMPANION_QUERY_CAPS, &caps, EINVAL, "input features");
    reset_caps(&caps);
    errors += expect_error(fd, _IO('H', 127), &caps, ENOTTY, "unknown ioctl");
    errors += expect_error(fd, HDMI_COMPANION_QUERY_CAPS, NULL, EFAULT, "null pointer");
    /* Repeated queries exercise FD operations without creating timing sessions. */
    for (int query = 0; query < 100; ++query) {
        reset_caps(&caps);
        if (ioctl(fd, HDMI_COMPANION_QUERY_CAPS, &caps) != 0) {
            perror("repeated QUERY_CAPS");
            ++errors;
            break;
        }
    }
    close(fd);
    if (errors) return 1;
    puts("PASS: query-only compatibility probe; no timing session created");
    return 0;
}
