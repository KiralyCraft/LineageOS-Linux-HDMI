#define _GNU_SOURCE

#include <drm/drm.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

#include "hdmi_los_trace.h"
#include "property_cache.h"

static int property_cache_case(void) {
  struct hdmi_los_property_cache cache = {0};
  if (hdmi_los_property_cache_is_noop(&cache, 79, 53, 0)) return 40;
  if (!hdmi_los_property_cache_store(&cache, 79, 53, 0) ||
      !hdmi_los_property_cache_store(&cache, 79, 55, 1024)) return 41;
  if (!hdmi_los_property_cache_is_noop(&cache, 79, 53, 0) ||
      !hdmi_los_property_cache_is_noop(&cache, 79, 55, 1024)) return 42;
  if (hdmi_los_property_cache_is_noop(&cache, 80, 55, 1024) ||
      hdmi_los_property_cache_is_noop(&cache, 79, 55, 2048)) return 43;
  if (!hdmi_los_property_cache_store(&cache, 79, 55, 2048) ||
      !hdmi_los_property_cache_is_noop(&cache, 79, 55, 2048) ||
      hdmi_los_property_cache_is_noop(&cache, 79, 55, 1024)) return 44;
  if (!hdmi_los_xorg_property_type_is_unsupported(DRM_MODE_PROP_BITMASK,
                                                   DRM_MODE_PROP_BITMASK) ||
      hdmi_los_xorg_property_type_is_unsupported(DRM_MODE_PROP_ENUM,
                                                 DRM_MODE_PROP_BITMASK)) return 45;
  return 0;
}

static int send_ack_flags(int fd, uint32_t sequence, uint32_t flags) {
  struct hdmi_los_trace_record ack = {0};
  ack.magic = HDMI_LOS_TRACE_MAGIC;
  ack.version = HDMI_LOS_TRACE_VERSION;
  ack.phase = HDMI_LOS_TRACE_ACK;
  ack.sequence = sequence;
  ack.flags = flags;
  return send(fd, &ack, sizeof(ack), MSG_NOSIGNAL) == (ssize_t)sizeof(ack);
}

static int send_ack(int fd, uint32_t sequence) {
  return send_ack_flags(fd, sequence, 0);
}

static int receive_record(int fd, struct hdmi_los_trace_record *record) {
  ssize_t result = recv(fd, record, sizeof(*record), MSG_WAITALL);
  return result == (ssize_t)sizeof(*record) && record->magic == HDMI_LOS_TRACE_MAGIC &&
         record->version == HDMI_LOS_TRACE_VERSION;
}

static int child_main(void) {
  int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
  if (fd < 0) return 10;
  struct drm_get_cap capability = {DRM_CAP_DUMB_BUFFER, 0};
  int count = getenv("HDMI_LOS_SELFTEST_TWO_IOCTLS") ? 2 : 1;
  int result = 0;
  int saved_errno = 0;
  for (int index = 0; index < count; ++index) {
    errno = 0;
    result = ioctl(fd, DRM_IOCTL_GET_CAP, &capability);
    saved_errno = errno;
    if (result != -1 || saved_errno != ENOTTY) break;
  }
  close(fd);
  return result == -1 && saved_errno == ENOTTY ? 0 : 11;
}

static pid_t spawn_child(const char *self, const char *library, int socket_fd,
                         int two_ioctls) {
  pid_t child = fork();
  if (child != 0) return child;
  int flags = fcntl(socket_fd, F_GETFD);
  if (flags < 0 || fcntl(socket_fd, F_SETFD, flags & ~FD_CLOEXEC) != 0) _exit(12);
  char descriptor[32];
  snprintf(descriptor, sizeof(descriptor), "%d", socket_fd);
  setenv("HDMI_LOS_TRACE_FD", descriptor, 1);
  if (two_ioctls) setenv("HDMI_LOS_SELFTEST_TWO_IOCTLS", "1", 1);
  setenv("LD_PRELOAD", library, 1);
  execl(self, self, "child", (char *)NULL);
  _exit(13);
}

static int success_case(const char *self, const char *library) {
  int sockets[2];
  if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) != 0) return 20;
  pid_t child = spawn_child(self, library, sockets[1], 0);
  close(sockets[1]);
  if (child < 0) return 21;

  const uint16_t expected[] = {
      HDMI_LOS_TRACE_LOADED, HDMI_LOS_TRACE_BEFORE, HDMI_LOS_TRACE_AFTER};
  for (size_t index = 0; index < sizeof(expected) / sizeof(expected[0]); ++index) {
    struct hdmi_los_trace_record record = {0};
    if (!receive_record(sockets[0], &record) || record.phase != expected[index] ||
        (index > 0 && record.request != DRM_IOCTL_GET_CAP) ||
        !send_ack(sockets[0], record.sequence)) {
      close(sockets[0]);
      kill(child, SIGKILL);
      waitpid(child, NULL, 0);
      return 22;
    }
  }
  close(sockets[0]);
  int status = 0;
  if (waitpid(child, &status, 0) != child || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
    return 23;
  }
  return 0;
}

static int steady_case(const char *self, const char *library) {
  int sockets[2];
  if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) != 0) return 24;
  pid_t child = spawn_child(self, library, sockets[1], 1);
  close(sockets[1]);
  if (child < 0) return 25;

  const uint16_t expected[] = {
      HDMI_LOS_TRACE_LOADED, HDMI_LOS_TRACE_BEFORE, HDMI_LOS_TRACE_AFTER};
  for (size_t index = 0; index < sizeof(expected) / sizeof(expected[0]); ++index) {
    struct hdmi_los_trace_record record = {0};
    uint32_t flags = index == 1 ? HDMI_LOS_TRACE_ACK_STEADY : 0;
    if (!receive_record(sockets[0], &record) || record.phase != expected[index] ||
        (index > 0 && record.request != DRM_IOCTL_GET_CAP) ||
        !send_ack_flags(sockets[0], record.sequence, flags)) {
      close(sockets[0]);
      kill(child, SIGKILL);
      waitpid(child, NULL, 0);
      return 26;
    }
  }

  struct pollfd descriptor = {sockets[0], POLLIN, 0};
  int polled;
  do {
    polled = poll(&descriptor, 1, 2000);
  } while (polled < 0 && errno == EINTR);
  if (polled < 0 || (polled > 0 && (descriptor.revents & POLLIN))) {
    struct hdmi_los_trace_record unexpected = {0};
    ssize_t received = recv(sockets[0], &unexpected, sizeof(unexpected), MSG_DONTWAIT);
    if (received > 0) {
      close(sockets[0]);
      kill(child, SIGKILL);
      waitpid(child, NULL, 0);
      return 27;
    }
  }
  close(sockets[0]);
  int status = 0;
  if (waitpid(child, &status, 0) != child || !WIFEXITED(status) || WEXITSTATUS(status) != 0)
    return 28;
  return 0;
}

static int fail_closed_case(const char *self, const char *library) {
  int sockets[2];
  if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) != 0) return 30;
  pid_t child = spawn_child(self, library, sockets[1], 0);
  close(sockets[1]);
  if (child < 0) return 31;
  struct hdmi_los_trace_record loaded = {0};
  if (!receive_record(sockets[0], &loaded) || loaded.phase != HDMI_LOS_TRACE_LOADED) {
    close(sockets[0]);
    kill(child, SIGKILL);
    waitpid(child, NULL, 0);
    return 32;
  }
  int status = 0;
  if (waitpid(child, &status, 0) != child || !WIFEXITED(status) || WEXITSTATUS(status) != 125) {
    close(sockets[0]);
    return 33;
  }
  close(sockets[0]);
  return 0;
}

int main(int argc, char **argv) {
  if (argc == 2 && strcmp(argv[1], "child") == 0) return child_main();
  if (argc != 2) {
    fprintf(stderr, "usage: %s LIBRARY\n", argv[0]);
    return 2;
  }
  int result = property_cache_case();
  if (result != 0) return result;
  result = success_case(argv[0], argv[1]);
  if (result != 0) return result;
  result = steady_case(argv[0], argv[1]);
  if (result != 0) return result;
  result = fail_closed_case(argv[0], argv[1]);
  if (result != 0) return result;
  puts("DRM tracer acknowledgement tests: PASS");
  return 0;
}
