// Host-only test of the real registration handler. Run as root on the build
// server for SO_PEERCRED; it creates only private abstract sockets and never
// invokes Broker::Run(), display acquisition, or timing-device operations.
#if defined(__ANDROID__)
#error This fixture is for the build host, not Android
#endif
#include <algorithm>
#include <atomic>
#include <cmath>
#include <string>
#include <vector>
#include <assert.h>
#include "../common/hdmi_connected_restart.h"
#include "../common/hdmi_timing_session.h"
#define private public
#define main broker_program_main
#include "main.cpp"
#undef main
#undef private

enum class DisplayState { kIdle, kArmed, kPaused };

static void registration(DisplayState state, uint32_t flags, bool accepted) {
  static unsigned serial = 0;
  char name[80];
  snprintf(name, sizeof(name), "hdmi-registration-test-%ld-%u",
           static_cast<long>(getpid()), ++serial);
  Broker broker;
  broker.listen_fd_ = listen_abstract(name);
  assert(broker.listen_fd_ >= 0);
  broker.armed_ = state == DisplayState::kArmed;
  if (state == DisplayState::kPaused) {
    auto mode = make_message(HDMI_LOS_OP_STATUS);
    mode.flags = HDMI_LOS_FLAG_CONNECTED | HDMI_LOS_FLAG_ACTIVE_MODE;
    mode.active_width = 3840;
    mode.active_height = 2160;
    mode.active_refresh_millihz = 30000;
    assert(broker.restart_.Remember(mode, monotonic_ms()));
  }

  int client = connect_abstract(name, SOCK_STREAM);
  assert(client >= 0);
  auto request = make_message(HDMI_LOS_OP_AGENT_REGISTER);
  request.flags = flags;
  assert(write_full(client, &request, sizeof(request)));
  broker.AcceptClient();
  hdmi_los_message reply = {};
  assert(read_full(client, &reply, sizeof(reply)) && valid_message(reply));
  assert(reply.request_id == request.request_id);
  if (accepted) {
    assert(reply.status == HDMI_LOS_OK);
    assert(reply.state == HDMI_LOS_STATE_AGENT_READY);
    assert((reply.flags & HDMI_LOS_FLAG_CONTINUOUS) == flags);
    assert(!(reply.flags & HDMI_LOS_FLAG_TIMING_REQUIRED));
    assert(broker.agent_fd_ >= 0);
    assert(broker.agent_continuous_ == bool(flags & HDMI_LOS_FLAG_CONTINUOUS));
    close(broker.agent_fd_);
  } else {
    assert(reply.status == HDMI_LOS_ERR_PROTOCOL && broker.agent_fd_ == -1);
    assert(!broker.agent_continuous_);
  }
  assert(broker.restart_.ready() == (state == DisplayState::kPaused));
  assert(broker.armed_ == (state == DisplayState::kArmed));
  close(client);
  close(broker.listen_fd_);
}

int main() {
  assert(getuid() == 0);
  alarm(10);
  for (auto state : {DisplayState::kIdle, DisplayState::kArmed, DisplayState::kPaused}) {
    registration(state, 0, true);
    registration(state, HDMI_LOS_FLAG_CONTINUOUS, true);
    registration(state, HDMI_LOS_FLAG_CONTINUOUS | 1u, false);
  }
  puts("PASS: real AcceptClient registration acknowledges continuous and bounded agents while idle, armed and paused; invalid flags rejected; display token preserved");
}
