#ifndef HDMI_CONNECTED_RESTART_H
#define HDMI_CONNECTED_RESTART_H

#include "hdmi_los_protocol.h"

// A connected restart adopts only the timing of the session just stopped.
// It cannot select a mode or arm a different hotplug generation. The broker
// invalidates this token on every observed disconnect, including one followed
// immediately by a reconnect in the same composer receive loop.
class HdmiConnectedRestart {
 public:
  static constexpr int64_t kLifetimeMs = 300000;
  bool Remember(const hdmi_los_message &status, int64_t now) {
    Cancel();
    if (!(status.flags & HDMI_LOS_FLAG_CONNECTED) ||
        !(status.flags & HDMI_LOS_FLAG_ACTIVE_MODE) ||
        !status.active_width || !status.active_height || !status.active_refresh_millihz)
      return false;
    width_ = status.active_width;
    height_ = status.active_height;
    refresh_ = status.active_refresh_millihz;
    expires_ms_ = now + kLifetimeMs;
    ready_ = true;
    return true;
  }
  bool ready() const { return ready_; }
  bool Expired(int64_t now) const { return ready_ && now >= expires_ms_; }
  bool Matches(const hdmi_los_message &status) const {
    return ready_ && (status.flags & HDMI_LOS_FLAG_CONNECTED) &&
           (status.flags & HDMI_LOS_FLAG_ACTIVE_MODE) &&
           width_ == status.active_width && height_ == status.active_height &&
           refresh_ == status.active_refresh_millihz;
  }
  void Cancel() { ready_ = false; expires_ms_ = 0; }
 private:
  bool ready_ = false;
  uint32_t width_ = 0, height_ = 0, refresh_ = 0;
  int64_t expires_ms_ = 0;
};

#endif
