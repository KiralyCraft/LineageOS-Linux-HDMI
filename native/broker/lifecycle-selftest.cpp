// Production broker state machine with socket peers. Only Android startup
// prerequisites and the already-authorized command entry are replaced.
#include <algorithm>
#include <atomic>
#include <cmath>
#include <string>
#include <vector>
#include <assert.h>
#include <sys/wait.h>
#include "../common/hdmi_connected_restart.h"
#include "../common/hdmi_timing_session.h"
#define private public
#define main broker_program_main
#include "main.cpp"
#undef main
#undef private

static hdmi_los_message mode() {
  auto m = make_message(HDMI_LOS_OP_STATUS);
  m.version = HDMI_LOS_VERSION;
  m.flags = HDMI_LOS_FLAG_CONNECTED | HDMI_LOS_FLAG_ACTIVE_MODE | HDMI_LOS_FLAG_LEASE_READY;
  m.active_width = 3840; m.active_height = 2160; m.active_refresh_millihz = 30000;
  return m;
}
static void child_ok(pid_t child) {
  int status = 0; assert(waitpid(child, &status, 0) == child);
  assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
}
static hdmi_los_message receive(int fd, uint16_t opcode, bool lease = false) {
  hdmi_los_message m = {}; int passed = -1;
  assert(recv_with_fd(fd, &m, &passed)); assert(m.opcode == opcode);
  assert((passed >= 0) == lease);
  if (lease) { assert(m.crtc_id == 235 && m.plane_id == 31); close(passed); }
  return m;
}
static void status_reply(int fd, const hdmi_los_message &request, uint32_t state) {
  auto m = mode(); m.opcode = request.opcode | HDMI_LOS_OP_RESPONSE;
  m.request_id = request.request_id; m.state = state; assert(send_with_fd(fd, m, -1));
}
static void agent_reply(int fd, const hdmi_los_message &request, bool fail = false) {
  auto m = make_message(fail ? HDMI_LOS_OP_AGENT_FAILED : HDMI_LOS_OP_AGENT_READY);
  m.request_id = request.request_id; m.status = fail ? HDMI_LOS_ERR_AGENT : HDMI_LOS_OK;
  snprintf(m.detail, sizeof(m.detail), "%s", fail ? "injected agent failure" : "ready");
  assert(write_full(fd, &m, sizeof(m)));
}
static void stopped_before_release(int composer, int agent) {
  auto stop = receive(agent, HDMI_LOS_OP_AGENT_STOP);
  // Old failures and wrong-request READY must not substitute for STOP ACK.
  agent_reply(agent, stop, true);
  auto stale = stop; --stale.request_id; agent_reply(agent, stale);
  pollfd p = {composer, POLLIN, 0}; assert(poll(&p, 1, 80) == 0);
  agent_reply(agent, stop);
  auto release = receive(composer, HDMI_LOS_OP_RELEASE);
  status_reply(composer, release, HDMI_LOS_STATE_ANDROID);
}
enum class Fault { kNone, kSnapshotDisconnect, kSnapshotDisconnectLate, kPrepareAgent, kPrepare, kPause, kCreate, kStartAgent, kStartDisconnect };
class FixtureBroker : public Broker {
 public:
  int result = HDMI_LOS_ERR_IO;
  std::string detail;
  int AcquireStartupGuards(std::string *) override { return HDMI_LOS_OK; }
  void AcceptClient() override {
    char byte; assert(read(listen_fd_, &byte, 1) == 1);
    // Matches AcceptClient's initial query, which can drain polled hotplug.
    hdmi_los_message status = {};
    assert(ComposerRequest(HDMI_LOS_OP_STATUS, &status));
    result = PauseConnected(&detail);
    if (result == HDMI_LOS_OK) result = ResumeConnected(&detail);
  }
};
static void transaction(Fault fault) {
  int c[2], a[2], trigger[2];
  assert(socketpair(AF_UNIX, SOCK_SEQPACKET, 0, c) == 0);
  assert(socketpair(AF_UNIX, SOCK_STREAM, 0, a) == 0);
  assert(socketpair(AF_UNIX, SOCK_STREAM, 0, trigger) == 0);
  // Both sockets were readable at poll time; the command drains them.
  auto event = mode(); event.opcode = HDMI_LOS_OP_HOTPLUG; event.request_id = 0;
  assert(send_with_fd(c[1], event, -1));
  auto stale = make_message(HDMI_LOS_OP_AGENT_READY); stale.request_id = 999999;
  assert(write_full(a[1], &stale, sizeof(stale)));
  assert(write(trigger[1], "x", 1) == 1);
  pid_t child = fork(); assert(child >= 0);
  if (!child) {
    close(c[0]); close(a[0]); close(trigger[0]); close(trigger[1]);
    for (int i = 0; i < 2; ++i) {
      auto request = receive(c[1], HDMI_LOS_OP_STATUS);
      // Exercise the pre-Pause status query too: comparison is against the
      // active session's generation, not only against Pause's query start.
      if ((i == 0 && fault == Fault::kSnapshotDisconnect) ||
          (i == 1 && fault == Fault::kSnapshotDisconnectLate)) {
        auto unplug = event; unplug.flags = 0; assert(send_with_fd(c[1], unplug, -1));
        assert(send_with_fd(c[1], event, -1));
      }
      status_reply(c[1], request, HDMI_LOS_STATE_LEASED);
    }
    stopped_before_release(c[1], a[1]);
    if (fault == Fault::kSnapshotDisconnect || fault == Fault::kSnapshotDisconnectLate) _exit(0);
    for (int i = 0; i < kModeStableSamples; ++i) {
      auto request = receive(c[1], HDMI_LOS_OP_STATUS);
      status_reply(c[1], request, HDMI_LOS_STATE_ANDROID);
    }
    auto request = receive(a[1], HDMI_LOS_OP_AGENT_PREPARE);
    agent_reply(a[1], request, fault == Fault::kPrepareAgent);
    if (fault == Fault::kPrepareAgent) { stopped_before_release(c[1], a[1]); _exit(0); }
    for (uint32_t phase : {HDMI_LOS_ACQUIRE_PREPARE, HDMI_LOS_ACQUIRE_PAUSE, HDMI_LOS_ACQUIRE_CREATE}) {
      request = receive(c[1], HDMI_LOS_OP_ACQUIRE);
      assert((request.flags & HDMI_LOS_ACQUIRE_PHASE_MASK) == phase);
      bool fail = (phase == 1 && fault == Fault::kPrepare) ||
                  (phase == 2 && fault == Fault::kPause) || (phase == 3 && fault == Fault::kCreate);
      auto ack = make_message(request.opcode | HDMI_LOS_OP_RESPONSE);
      ack.version = HDMI_LOS_VERSION; ack.request_id = request.request_id;
      ack.flags = phase; ack.state = phase == 3 ? HDMI_LOS_STATE_LEASED : HDMI_LOS_STATE_DRAINING;
      ack.connector_id = 79; ack.crtc_id = 235; ack.plane_id = 31;
      ack.status = fail ? HDMI_LOS_ERR_IO : HDMI_LOS_OK;
      if (fail) snprintf(ack.detail, sizeof(ack.detail), "injected acquire failure %u", phase);
      int lease[2] = {-1, -1};
      if (phase == 3 && !fail) assert(pipe(lease) == 0);
      assert(send_with_fd(c[1], ack, lease[0]));
      if (lease[0] >= 0) { close(lease[0]); close(lease[1]); }
      if (fail) { stopped_before_release(c[1], a[1]); _exit(0); }
      request = receive(c[1], HDMI_LOS_OP_STATUS);
      status_reply(c[1], request, ack.state);
    }
    request = receive(a[1], HDMI_LOS_OP_AGENT_START, true);
    if (fault == Fault::kStartDisconnect) {
      event.flags = 0; assert(send_with_fd(c[1], event, -1));
    } else agent_reply(a[1], request, fault == Fault::kStartAgent);
    stopped_before_release(c[1], a[1]);
    _exit(0);
  }
  close(c[1]); close(a[1]); close(trigger[1]);
  FixtureBroker b; b.composer_fd_ = c[0]; b.agent_fd_ = a[0]; b.listen_fd_ = trigger[0];
  b.active_ = b.armed_ = b.preference_applied_ = b.agent_continuous_ = true;
  // Prevent a regression from hanging the test on an old poll indication.
  timeval timeout = {1, 0}; setsockopt(c[0], SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  setsockopt(a[0], SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  assert(b.PollOnce());
  assert(!b.restart_.ready() && !b.starting_);
  if (fault == Fault::kNone) {
    assert(b.result == HDMI_LOS_OK && b.active_ && b.armed_);
    assert(b.Release("fixture normal stop", true));
  } else {
    assert(b.result != HDMI_LOS_OK && !b.active_ && !b.armed_);
    if (fault == Fault::kStartAgent || fault == Fault::kPrepareAgent)
      assert(b.result == HDMI_LOS_ERR_AGENT && b.detail == "injected agent failure");
    if (fault == Fault::kPrepare || fault == Fault::kPause || fault == Fault::kCreate)
      assert(b.result == HDMI_LOS_ERR_IO && b.detail.find("injected acquire failure") == 0);
  }
  assert(b.volumes_.down == -1 && b.volumes_.up == -1 && !b.timing_.generation());
  close(c[0]); if (b.agent_fd_ >= 0) close(b.agent_fd_); close(trigger[0]); child_ok(child);
}
static void partial_reply() {
  int p[2]; assert(socketpair(AF_UNIX, SOCK_STREAM, 0, p) == 0);
  auto late = make_message(HDMI_LOS_OP_AGENT_READY); late.request_id = 41;
  assert(write_full(p[1], &late, 1));
  Broker b; b.agent_fd_ = p[0]; hdmi_los_message response = {};
  int64_t start = monotonic_ms();
  assert(!b.WaitAgent(HDMI_LOS_OP_AGENT_READY, 40, &response, true, 41));
  assert(monotonic_ms() - start < 500); // Socket remains open with a partial message.
  assert(write_full(p[1], reinterpret_cast<char *>(&late) + 1, sizeof(late) - 1));
  auto stop = late; stop.request_id = 42; assert(write_full(p[1], &stop, sizeof(stop)));
  assert(b.WaitAgent(HDMI_LOS_OP_AGENT_READY, 200, &response, true, 42));
  assert(response.request_id == 42); // No stream desynchronization after timeout.
  close(p[1]); assert(!b.WaitAgent(HDMI_LOS_OP_AGENT_READY, 100, &response, true, 43));
  close(p[0]);
  assert(socketpair(AF_UNIX, SOCK_STREAM, 0, p) == 0);
  b.agent_fd_ = p[0];
  assert(!b.StopAgent(40) && b.agent_fd_ == -1);
  // Failed STOP disconnects the old agent and resets its stream framing.
  auto request = receive(p[1], HDMI_LOS_OP_AGENT_STOP);
  assert(request.request_id);
  char byte; assert(read(p[1], &byte, 1) == 0); close(p[1]);
}
int main() {
  assert(getuid() != 0); alarm(30);
  partial_reply();
  for (auto fault : {Fault::kNone, Fault::kSnapshotDisconnect, Fault::kSnapshotDisconnectLate, Fault::kPrepareAgent,
                    Fault::kPrepare, Fault::kPause, Fault::kCreate, Fault::kStartAgent,
                    Fault::kStartDisconnect}) transaction(fault);
  puts("PASS: full pause/resume via event dispatch; ACK-before-release at every failure phase; error preservation; disconnect generation; partial reply deadline and framing");
}
