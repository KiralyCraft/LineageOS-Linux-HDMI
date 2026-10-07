#ifndef HDMI_AGENT_READER_H
#define HDMI_AGENT_READER_H

#include <errno.h>
#include <string.h>
#include <sys/socket.h>
#include "hdmi_los_protocol.h"

// Stream framing survives a timed-out command: its partial reply must be
// completed and classified before a later STOP acknowledgement can be read.
class HdmiAgentReader {
 public:
  enum class Result { kPending, kMessage, kError };
  void Reset() { used_ = 0; }
  Result ReadAvailable(int fd, hdmi_los_message *message) {
    while (used_ < sizeof(bytes_)) {
      ssize_t count = recv(fd, bytes_ + used_, sizeof(bytes_) - used_, MSG_DONTWAIT);
      if (count > 0) used_ += static_cast<size_t>(count);
      else if (count < 0 && errno == EINTR) continue;
      else if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
        return Result::kPending;
      else return Result::kError;
    }
    memcpy(message, bytes_, sizeof(*message));
    used_ = 0;
    return Result::kMessage;
  }
 private:
  unsigned char bytes_[sizeof(hdmi_los_message)] = {};
  size_t used_ = 0;
};
#endif
