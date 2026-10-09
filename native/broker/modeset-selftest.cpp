// Exercise production barrier ordering without a DRM device or display.
#include <algorithm>
#include <cmath>
#include <string>
#include <vector>
#include <assert.h>
#include "../common/hdmi_timing_session.h"
#include "../common/hdmi_connected_restart.h"
#define private public
#define main broker_program_main
#include "main.cpp"
#undef main
#undef private

class Fixture : public Broker {
 public:
  int stops = 0, starts = 0;
  bool fail_start = false;
  Fixture() {
    agent_modeset_timing_ = agent_timing_required_ = active_ = true;
    timing_lease_fd_ = 99; timing_crtc_ = 235;
  }
  void SuspendModeTiming() override { ++stops; }
  bool RestartModeTiming(std::string *error) override {
    ++starts;
    if (fail_start) *error = "injected validation failure";
    return !fail_start;
  }
};
static hdmi_los_message barrier(unsigned seq, unsigned state, bool enabled = true, int result = 0) {
  auto e = make_message(HDMI_LOS_OP_AGENT_MODESET);
  e.request_id = seq; e.state = state; e.crtc_id = 235;
  e.active_refresh_millihz = enabled; e.status = result;
  return e;
}
int main() {
  std::string error;
  {
    Fixture b;
    assert(b.ModeBarrier(barrier(9,0), &error));
    assert(b.ModeBarrier(barrier(10,0), &error));
    assert(b.ModeBarrier(barrier(1,1,false), &error));
    auto deadline = b.modeset_deadline_ms_;
    assert(b.stops == 3 && !b.starts && deadline);
    assert(b.ModeBarrier(barrier(1,2,false), &error));
    assert(b.modeset_deadline_ms_ == deadline && !b.starts);
    assert(b.ModeBarrier(barrier(2,1), &error));
    assert(b.modeset_deadline_ms_ == deadline); // no renewal of bounded timeout
    assert(b.ModeBarrier(barrier(2,2), &error));
    assert(b.starts == 1 && !b.modeset_deadline_ms_ && !b.modeset_sequence_);
    // Fullscreen exit and same-size refresh changes also renew the generation.
    assert(b.ModeBarrier(barrier(3,1), &error));
    assert(b.ModeBarrier(barrier(3,2), &error));
    assert(b.starts == 2);
  }
  {
    Fixture b;
    assert(!b.ModeBarrier(barrier(1,2), &error));
    auto wrong = barrier(1,1); wrong.crtc_id = 1;
    assert(!b.ModeBarrier(wrong, &error));
    assert(b.stops == 0);
    assert(b.ModeBarrier(barrier(1,1), &error));
    assert(!b.ModeBarrier(barrier(2,1), &error));
    assert(!b.ModeBarrier(barrier(2,2), &error));
    b.modeset_deadline_ms_ = monotonic_ms() - 1;
    assert(!b.ModeBarrier(barrier(1,2), &error));
    assert(!b.starts);
  }
  {
    Fixture b;
    assert(b.ModeBarrier(barrier(1,1), &error));
    assert(b.ModeBarrier(barrier(1,2,true,-EINVAL), &error));
    assert(b.starts == 1); // kernel must validate actual surviving mode
    b.fail_start = true;
    assert(b.ModeBarrier(barrier(2,1), &error));
    assert(!b.ModeBarrier(barrier(2,2), &error));
    assert(b.modeset_deadline_ms_); // failure never blesses presentation
  }
  {
    Fixture b;
    b.stopping_agent_ = true;
    assert(b.ModeBarrier(barrier(1,1), &error));
    assert(b.ModeBarrier(barrier(1,2), &error));
    assert(!b.stops && !b.starts && !b.modeset_deadline_ms_);
  }
  {
    Fixture b;
    b.active_ = false;
    assert(!b.ModeBarrier(barrier(1,1), &error));
    b.starting_ = true;
    assert(b.ModeBarrier(barrier(1,1), &error));
    assert(b.ModeBarrier(barrier(1,2), &error));
  }
  puts("PASS modeset disable/enable, repeated transitions, rollback, timeout, mismatch, startup and teardown");
}
