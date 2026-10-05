#ifndef HDMI_TIMING_SESSION_H
#define HDMI_TIMING_SESSION_H

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include <string>
#include "../../kernel/hdmi_companion/uapi.h"

// The broker alone owns this FD. It is never sent to the X server or clients.
class HdmiTimingSession {
 public:
  ~HdmiTimingSession() { Stop(); }
  HdmiTimingSession() = default;
  HdmiTimingSession(const HdmiTimingSession &) = delete;
  HdmiTimingSession &operator=(const HdmiTimingSession &) = delete;

  static bool Available(std::string *error) {
    int device = Open(error);
    if (device < 0) return false;
    close(device);
    return true;
  }

  bool Start(int lease, uint32_t connector, uint32_t crtc, uint32_t plane,
             std::string *error) {
    Stop();
    int device = Open(error);
    if (device < 0) return false;
    hdmi_companion_create request = {};
    request.size = sizeof(request);
    request.abi_version = HDMI_COMPANION_ABI_VERSION;
    request.lease_fd = lease;
    request.session_fd = -1;
    request.connector_id = connector;
    request.crtc_id = crtc;
    request.plane_id = plane;
    int result = ioctl(device, HDMI_COMPANION_CREATE_SESSION, &request);
    int saved_errno = errno;
    close(device);
    if (result != 0) {
      *error = std::string("timing session creation failed: ") + strerror(saved_errno);
      return false;
    }
    fd_ = request.session_fd;
    generation_ = request.generation;
    if (fd_ < 0 || !generation_) {
      *error = "invalid timing session identity";
      Stop();
      return false;
    }
    hdmi_companion_control control = {};
    control.size = sizeof(control);
    control.abi_version = HDMI_COMPANION_ABI_VERSION;
    if (ioctl(fd_, HDMI_COMPANION_ENABLE_TIMING, &control) != 0) {
      *error = std::string("timing reference acquisition failed: ") + strerror(errno);
      Stop();
      return false;
    }
    const int64_t deadline = NowMs() + 750;
    do {
      hdmi_companion_status status = {};
      if (!ReadStatus(&status, error)) { Stop(); return false; }
      if (status.state == HDMI_COMPANION_TIMING_VALID) return true;
      if (status.state != HDMI_COMPANION_STARTING) {
        *error = "timing startup stopped, reason=" + std::to_string(status.reason);
        Stop();
        return false;
      }
      timespec delay = {0, 10000000};
      while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {}
    } while (NowMs() < deadline);
    *error = "timing accounting did not become valid within 750 ms";
    Stop();
    return false;
  }

  bool Valid(std::string *error) const {
    hdmi_companion_status status = {};
    if (!ReadStatus(&status, error)) return false;
    if (status.state == HDMI_COMPANION_TIMING_VALID) return true;
    *error = "timing session invalidated, reason=" + std::to_string(status.reason);
    return false;
  }

  void Stop() {
    if (fd_ < 0) return;
    hdmi_companion_control control = {};
    control.size = sizeof(control);
    control.abi_version = HDMI_COMPANION_ABI_VERSION;
    (void)ioctl(fd_, HDMI_COMPANION_STOP_SESSION, &control);
    close(fd_); // Final-FD release is also an independent kernel cleanup path.
    fd_ = -1;
    generation_ = 0;
  }
  uint64_t generation() const { return generation_; }

 private:
  static int64_t NowMs() {
    timespec now = {};
    clock_gettime(CLOCK_MONOTONIC, &now);
    return static_cast<int64_t>(now.tv_sec) * 1000 + now.tv_nsec / 1000000;
  }
  static int Open(std::string *error) {
    int device = open("/dev/hdmi_companion", O_RDWR | O_CLOEXEC);
    if (device < 0) {
      *error = std::string("timing companion unavailable: ") + strerror(errno);
      return -1;
    }
    hdmi_companion_caps caps = {};
    caps.size = sizeof(caps);
    caps.abi_version = HDMI_COMPANION_ABI_VERSION;
    if (ioctl(device, HDMI_COMPANION_QUERY_CAPS, &caps) != 0 ||
        caps.size != sizeof(caps) || caps.abi_version != HDMI_COMPANION_ABI_VERSION ||
        caps.features != HDMI_COMPANION_FEATURE_TIMING_GUARD ||
        caps.imports != HDMI_COMPANION_REQUIRED_IMPORTS ||
        caps.reserved[0] || caps.reserved[1] ||
        !memchr(caps.build_id, 0, sizeof(caps.build_id)) || !caps.build_id[0]) {
      *error = "timing companion capability/ABI check failed";
      close(device);
      return -1;
    }
    return device;
  }
  bool ReadStatus(hdmi_companion_status *status, std::string *error) const {
    status->size = sizeof(*status);
    status->abi_version = HDMI_COMPANION_ABI_VERSION;
    if (fd_ < 0 || ioctl(fd_, HDMI_COMPANION_GET_STATUS, status) != 0) {
      *error = "timing status query failed";
      return false;
    }
    if (status->size != sizeof(*status) || status->abi_version != HDMI_COMPANION_ABI_VERSION ||
        status->generation != generation_ || status->reserved0 ||
        status->reserved[0] || status->reserved[1]) {
      *error = "timing status identity/ABI mismatch";
      return false;
    }
    return true;
  }
  int fd_ = -1;
  uint64_t generation_ = 0;
};
#endif
