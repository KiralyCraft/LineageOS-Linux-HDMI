#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <linux/input.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#if defined(__ANDROID__)
#include <sys/system_properties.h>
#endif
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <string>
#include <vector>

#include "hdmi_los_protocol.h"
#include "hdmi_timing_session.h"
#include "hdmi_connected_restart.h"
#include "hdmi_agent_reader.h"

namespace {

constexpr int kSessionSeconds = 60;
constexpr int kComposerHeartbeatSeconds = 20;
constexpr int kChordHoldMs = 3000;
constexpr int kAgentPrepareMs = 5000;
constexpr int kAgentStartMs = 15000;
// The agent can spend up to two seconds stopping each of desktop, Xorg and
// input bridge. Do not revoke a healthy lease before its stop acknowledgement.
constexpr int kAgentStopMs = 8000;
constexpr int kComposerRequestSeconds = 10;
constexpr int kLeaseHoldMs = 3000;
constexpr const char *kModuleDir = "/data/adb/modules/hdmi-los";
constexpr const char *kModeConfig = "/data/adb/hdmi-los/preferred-mode.conf";
constexpr const char *kModeJournal = "/data/adb/hdmi-los/preferred-mode.journal";
constexpr int kModePollMs = 250;
constexpr int kModeStableSamples = 3;
constexpr int kModeMismatchMs = 5000;

enum class ComposerHotplug {
  kInvalid,
  kConnected,
  kDisconnected,
};

std::atomic<bool> g_stop(false);
uint32_t g_request_id = 1;

int64_t monotonic_ms() {
  timespec now = {};
  clock_gettime(CLOCK_BOOTTIME, &now);
  return static_cast<int64_t>(now.tv_sec) * 1000 + now.tv_nsec / 1000000;
}

void log_line(const char *level, const char *message) {
  timespec now = {};
  clock_gettime(CLOCK_REALTIME, &now);
  fprintf(stderr, "[%lld.%03ld] hdmi-losd %s: %s\n",
          static_cast<long long>(now.tv_sec), now.tv_nsec / 1000000, level, message);
  fflush(stderr);
}

void log_transition(const char *message) {
  log_line("transition", message);
  // stderr is redirected to the persistent module log by service.sh.  Make
  // every transition boundary durable before entering composer or Xorg code,
  // so even a watchdog reset identifies the last operation begun.
  int saved_errno = errno;
  (void)fdatasync(STDERR_FILENO);
  errno = saved_errno;
}

void log_errno(const char *message) {
  std::string text(message);
  text += ": ";
  text += strerror(errno);
  log_line("error", text.c_str());
}

void signal_handler(int) {
  g_stop = true;
}

socklen_t abstract_address(sockaddr_un *address, const char *name) {
  memset(address, 0, sizeof(*address));
  address->sun_family = AF_UNIX;
  size_t length = std::min(strlen(name), sizeof(address->sun_path) - 2);
  memcpy(address->sun_path + 1, name, length);
  return static_cast<socklen_t>(offsetof(sockaddr_un, sun_path) + 1 + length);
}

int connect_abstract(const char *name, int type) {
  int fd = socket(AF_UNIX, type | SOCK_CLOEXEC, 0);
  if (fd < 0) return -1;
  sockaddr_un address = {};
  socklen_t length = abstract_address(&address, name);
  if (connect(fd, reinterpret_cast<sockaddr *>(&address), length) < 0) {
    close(fd);
    return -1;
  }
  return fd;
}

int listen_abstract(const char *name) {
  int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (fd < 0) return -1;
  sockaddr_un address = {};
  socklen_t length = abstract_address(&address, name);
  if (bind(fd, reinterpret_cast<sockaddr *>(&address), length) < 0 || listen(fd, 4) < 0) {
    close(fd);
    return -1;
  }
  return fd;
}

hdmi_los_message make_message(uint16_t opcode) {
  hdmi_los_message message = {};
  message.magic = HDMI_LOS_MAGIC;
  message.version = HDMI_LOS_BROKER_VERSION;
  message.opcode = opcode;
  message.request_id = g_request_id++;
  return message;
}

bool write_full(int fd, const void *buffer, size_t size) {
  const char *cursor = static_cast<const char *>(buffer);
  while (size) {
    ssize_t written = send(fd, cursor, size, MSG_NOSIGNAL);
    if (written > 0) {
      cursor += written;
      size -= static_cast<size_t>(written);
    } else if (written < 0 && errno == EINTR) {
      continue;
    } else {
      return false;
    }
  }
  return true;
}

bool read_full(int fd, void *buffer, size_t size) {
  char *cursor = static_cast<char *>(buffer);
  while (size) {
    ssize_t received = recv(fd, cursor, size, 0);
    if (received > 0) {
      cursor += received;
      size -= static_cast<size_t>(received);
    } else if (received < 0 && errno == EINTR) {
      continue;
    } else {
      return false;
    }
  }
  return true;
}

bool send_with_fd(int fd, const hdmi_los_message &message, int passed_fd) {
  iovec iov = {const_cast<hdmi_los_message *>(&message), sizeof(message)};
  msghdr header = {};
  header.msg_iov = &iov;
  header.msg_iovlen = 1;
  char control[CMSG_SPACE(sizeof(int))] = {};
  if (passed_fd >= 0) {
    header.msg_control = control;
    header.msg_controllen = sizeof(control);
    cmsghdr *cmsg = CMSG_FIRSTHDR(&header);
    cmsg->cmsg_level = SOL_SOCKET;
    cmsg->cmsg_type = SCM_RIGHTS;
    cmsg->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(cmsg), &passed_fd, sizeof(passed_fd));
  }
  ssize_t result;
  do {
    result = sendmsg(fd, &header, MSG_NOSIGNAL);
  } while (result < 0 && errno == EINTR);
  return result == static_cast<ssize_t>(sizeof(message));
}

bool recv_with_fd(int fd, hdmi_los_message *message, int *passed_fd) {
  iovec iov = {message, sizeof(*message)};
  char control[CMSG_SPACE(sizeof(int))] = {};
  msghdr header = {};
  header.msg_iov = &iov;
  header.msg_iovlen = 1;
  header.msg_control = control;
  header.msg_controllen = sizeof(control);
  ssize_t result;
  do {
    result = recvmsg(fd, &header, MSG_WAITALL | MSG_CMSG_CLOEXEC);
  } while (result < 0 && errno == EINTR);
  if (result != static_cast<ssize_t>(sizeof(*message))) return false;
  if (passed_fd) *passed_fd = -1;
  for (cmsghdr *cmsg = CMSG_FIRSTHDR(&header); cmsg; cmsg = CMSG_NXTHDR(&header, cmsg)) {
    if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS && passed_fd) {
      memcpy(passed_fd, CMSG_DATA(cmsg), sizeof(*passed_fd));
      break;
    }
  }
  return true;
}

bool valid_message(const hdmi_los_message &message) {
  return message.magic == HDMI_LOS_MAGIC &&
         message.version == HDMI_LOS_BROKER_VERSION;
}

bool valid_composer_message(const hdmi_los_message &message) {
  return message.magic == HDMI_LOS_MAGIC && message.version == HDMI_LOS_VERSION;
}

uid_t tile_uid() {
  FILE *packages = fopen("/data/system/packages.list", "re");
  if (!packages) return static_cast<uid_t>(-1);
  char line[1024];
  uid_t result = static_cast<uid_t>(-1);
  while (fgets(line, sizeof(line), packages)) {
    char package[256] = {};
    unsigned int uid = 0;
    if (sscanf(line, "%255s %u", package, &uid) == 2 &&
        strcmp(package, "dev.kiraly.hdmilos") == 0) {
      result = static_cast<uid_t>(uid);
      break;
    }
  }
  fclose(packages);
  return result;
}

bool compatible() {
  std::string marker(kModuleDir);
  marker += "/compatible.ok";
  return access(marker.c_str(), R_OK) == 0;
}

bool diagnostic_only() {
  std::string marker(kModuleDir);
  marker += "/diagnostic-only";
  return access(marker.c_str(), R_OK) == 0;
}

bool diagnostic_dump_ready() {
  if (!diagnostic_only()) return true;
#if defined(__ANDROID__)
  char value[PROP_VALUE_MAX] = {};
  return __system_property_get("vendor.display.disable_hw_recovery_dump", value) > 0 &&
         strcmp(value, "0") == 0;
#else
  return true;
#endif
}

bool set_wake_lock(bool acquire) {
  const char *path = acquire ? "/sys/power/wake_lock" : "/sys/power/wake_unlock";
  int fd = open(path, O_WRONLY | O_CLOEXEC);
  if (fd < 0) return false;
  const char value[] = "hdmi-los";
  bool ok = write(fd, value, sizeof(value) - 1) == static_cast<ssize_t>(sizeof(value) - 1);
  close(fd);
  return ok;
}

bool run_display_command(const std::vector<std::string> &arguments, std::string *output) {
  int pipe_fds[2] = {-1, -1};
  if (pipe2(pipe_fds, O_CLOEXEC) != 0) return false;
  pid_t child = fork();
  if (child == 0) {
    dup2(pipe_fds[1], STDOUT_FILENO);
    dup2(pipe_fds[1], STDERR_FILENO);
    close(pipe_fds[0]);
    close(pipe_fds[1]);
    std::vector<char *> argv;
    argv.push_back(const_cast<char *>("/system/bin/cmd"));
    argv.push_back(const_cast<char *>("display"));
    for (const auto &argument : arguments) {
      argv.push_back(const_cast<char *>(argument.c_str()));
    }
    argv.push_back(nullptr);
    execv(argv[0], argv.data());
    _exit(127);
  }
  close(pipe_fds[1]);
  if (child < 0) {
    close(pipe_fds[0]);
    return false;
  }
  std::string captured;
  char buffer[512];
  ssize_t count;
  while ((count = read(pipe_fds[0], buffer, sizeof(buffer))) > 0) {
    if (captured.size() < 4096) captured.append(buffer, static_cast<size_t>(count));
  }
  close(pipe_fds[0]);
  int status = 0;
  while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
  if (output) *output = captured;
  return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

bool write_atomic_text(const char *path, const std::string &text) {
  std::string temporary(path);
  temporary += ".tmp";
  int fd = open(temporary.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  if (fd < 0) return false;
  bool ok = write(fd, text.data(), text.size()) == static_cast<ssize_t>(text.size()) &&
            fsync(fd) == 0;
  if (close(fd) != 0) ok = false;
  if (ok && rename(temporary.c_str(), path) != 0) ok = false;
  if (!ok) unlink(temporary.c_str());
  return ok;
}

bool read_text(const char *path, std::string *text) {
  FILE *file = fopen(path, "re");
  if (!file) return false;
  char buffer[512];
  text->clear();
  while (fgets(buffer, sizeof(buffer), file) && text->size() < 4096) *text += buffer;
  bool ok = !ferror(file);
  fclose(file);
  return ok;
}

bool parse_saved_mode(const std::string &text, uint32_t *width, uint32_t *height,
                      uint32_t *refresh_millihz) {
  unsigned int parsed_width = 0;
  unsigned int parsed_height = 0;
  float parsed_refresh = 0.0f;
  const char *value = strchr(text.c_str(), ':');
  value = value ? value + 1 : text.c_str();
  if (strstr(value, "null")) {
    *width = *height = *refresh_millihz = 0;
    return true;
  }
  if (sscanf(value, " %u %u %f", &parsed_width, &parsed_height, &parsed_refresh) != 3 &&
      sscanf(value, " %ux%u@%f", &parsed_width, &parsed_height, &parsed_refresh) != 3) {
    return false;
  }
  if (!parsed_width || !parsed_height || parsed_refresh < 1.0f) return false;
  *width = parsed_width;
  *height = parsed_height;
  *refresh_millihz = static_cast<uint32_t>(lroundf(parsed_refresh * 1000.0f));
  return true;
}

bool input_has_key(int fd, int key) {
  unsigned long bits[(KEY_MAX + 8 * sizeof(unsigned long)) / (8 * sizeof(unsigned long))] = {};
  if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(bits)), bits) < 0) return false;
  return (bits[key / (8 * sizeof(unsigned long))] >> (key % (8 * sizeof(unsigned long)))) & 1UL;
}

int find_volume_device(const char *wanted_name, int key) {
  glob_t paths = {};
  if (glob("/dev/input/event*", 0, nullptr, &paths) != 0) return -1;
  int selected = -1;
  for (size_t i = 0; i < paths.gl_pathc; ++i) {
    int fd = open(paths.gl_pathv[i], O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOCTTY);
    if (fd < 0) continue;
    char name[256] = {};
    if (ioctl(fd, EVIOCGNAME(sizeof(name)), name) >= 0 && strcmp(name, wanted_name) == 0 &&
        input_has_key(fd, key)) {
      selected = fd;
      break;
    }
    close(fd);
  }
  globfree(&paths);
  return selected;
}

struct VolumeGuard {
  int down = -1;
  int up = -1;
  bool down_pressed = false;
  bool up_pressed = false;
  int64_t both_since = 0;

  bool Acquire() {
    down = find_volume_device("gpio-keys", KEY_VOLUMEDOWN);
    up = find_volume_device("pmic_resin", KEY_VOLUMEUP);
    if (down < 0 || up < 0 || ioctl(down, EVIOCGRAB, reinterpret_cast<void *>(1)) < 0 ||
        ioctl(up, EVIOCGRAB, reinterpret_cast<void *>(1)) < 0) {
      Release();
      return false;
    }
    return true;
  }

  void Release() {
    if (down >= 0) {
      ioctl(down, EVIOCGRAB, reinterpret_cast<void *>(0));
      close(down);
    }
    if (up >= 0) {
      ioctl(up, EVIOCGRAB, reinterpret_cast<void *>(0));
      close(up);
    }
    down = up = -1;
    down_pressed = up_pressed = false;
    both_since = 0;
  }

  bool ReadOne(int fd, int expected_key, bool *pressed) {
    input_event events[16];
    ssize_t size;
    bool ok = true;
    while ((size = read(fd, events, sizeof(events))) > 0) {
      size_t count = static_cast<size_t>(size) / sizeof(events[0]);
      for (size_t i = 0; i < count; ++i) {
        if (events[i].type == EV_KEY && events[i].code == expected_key) {
          *pressed = events[i].value != 0;
        }
      }
    }
    if (size == 0 || (size < 0 && errno != EAGAIN && errno != EINTR)) ok = false;
    return ok;
  }

  bool Update(short down_events, short up_events) {
    if ((down_events & (POLLERR | POLLHUP | POLLNVAL)) ||
        (up_events & (POLLERR | POLLHUP | POLLNVAL))) return true;
    if ((down_events & POLLIN) && !ReadOne(down, KEY_VOLUMEDOWN, &down_pressed)) return true;
    if ((up_events & POLLIN) && !ReadOne(up, KEY_VOLUMEUP, &up_pressed)) return true;
    int64_t now = monotonic_ms();
    if (down_pressed && up_pressed) {
      if (!both_since) both_since = now;
      if (now - both_since >= kChordHoldMs) return true;
    } else {
      both_since = 0;
    }
    return false;
  }
};

class Broker {
 public:
  virtual ~Broker() = default;
  int Run() {
    LoadConfiguredMode();
    RecoverPreferredMode("broker startup");
    listen_fd_ = listen_abstract(HDMI_LOS_BROKER_SOCKET);
    if (listen_fd_ < 0) {
      log_errno("cannot bind broker socket");
      return 1;
    }
    log_line("info", "broker ready");
    while (!g_stop) {
      if (!PollOnce()) break;
      if (active_ && session_disconnect_generation_ != disconnect_generation_)
        Release("HDMI disconnected during a broker request", true);
      if (active_ && composer_renewable_ &&
          monotonic_ms() >= composer_heartbeat_ms_) {
        hdmi_los_message response = {};
        if (!ComposerRequest(HDMI_LOS_OP_PING, &response, nullptr,
                             HDMI_LOS_FLAG_CONTINUOUS) ||
            response.status != HDMI_LOS_OK ||
            response.state != HDMI_LOS_STATE_LEASED ||
            !(response.flags & HDMI_LOS_FLAG_CONTINUOUS)) {
          Release("composer watchdog renewal failed", true);
        } else {
          composer_heartbeat_ms_ = monotonic_ms() + kComposerHeartbeatSeconds * 1000;
        }
      }
      if (active_ && deadline_ms_ > 0 && monotonic_ms() >= deadline_ms_) {
        Release("mandatory 60 second timeout", true);
      }
      if (active_ && agent_timing_required_ && monotonic_ms() >= timing_check_ms_) {
        std::string error;
        if (!timing_.Valid(&error)) Release(error.c_str(), true);
        timing_check_ms_ = monotonic_ms() + 500;
      }
      if (armed_ && !active_) AdvanceArmed();
      if (restart_.Expired(monotonic_ms())) Disarm("connected restart expired");
    }
    Release("broker stopping", true);
    if (armed_ || restart_.ready()) Disarm("broker stopping");
    if (agent_fd_ >= 0) close(agent_fd_);
    if (composer_fd_ >= 0) close(composer_fd_);
    if (listen_fd_ >= 0) close(listen_fd_);
    return 0;
  }

 private:
  // Dispatch one readiness snapshot only. Commands, restore and watchdog
  // helpers can drain or replace other descriptors; always poll again after
  // them. Run still checks timers after every dispatch, including client work.
  bool PollOnce() {
    pollfd fds[5] = {
        {listen_fd_, POLLIN, 0},
        {composer_fd_, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
        {agent_fd_, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
        {volumes_.down, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
        {volumes_.up, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
    };
    int result = poll(fds, 5, (active_ || armed_) ? 100 : 1000);
    if (result < 0) return errno == EINTR;
    if (active_ && volumes_.Update(fds[3].revents, fds[4].revents)) {
      Release("both-volume escape or volume input loss", true);
      return true;
    }
    if (fds[0].revents & POLLIN) { AcceptClient(); return true; }
    if (composer_fd_ >= 0 && (fds[1].revents & (POLLERR | POLLHUP | POLLNVAL))) {
      close(composer_fd_);
      composer_fd_ = -1;
      if (active_) Release("composer disconnected", false);
      if (restart_.ready()) Disarm("connected restart cancelled: composer disconnected");
      return true;
    }
    if (composer_fd_ >= 0 && (fds[1].revents & POLLIN)) {
      ComposerHotplug hotplug = HandleComposerReadable();
      if (hotplug == ComposerHotplug::kInvalid) {
        if (active_) Release("invalid composer hotplug event", true);
        else if (armed_ || restart_.ready()) Disarm("invalid composer hotplug event");
      } else if (hotplug == ComposerHotplug::kDisconnected) {
        if (active_) Release("composer hotplug/disconnect event", true);
        else if (armed_) PreserveArmAcrossDisconnect();
      }
      return true;
    }
    if (agent_fd_ >= 0 && (fds[2].revents & POLLIN)) {
      hdmi_los_message event = {};
      auto read = agent_reader_.ReadAvailable(agent_fd_, &event);
      if (read == HdmiAgentReader::Result::kError ||
          (read == HdmiAgentReader::Result::kMessage && !valid_message(event))) {
        ForgetAgent();
        if (active_) Release("invalid or disconnected Xorg agent", true);
      } else if (read == HdmiAgentReader::Result::kMessage && !HandleAgentEvent(event)) {
        if (active_) Release("Xorg agent reported failure", true);
      }
      return true;
    }
    if (agent_fd_ >= 0 && (fds[2].revents & (POLLERR | POLLHUP | POLLNVAL))) {
      ForgetAgent();
      if (active_) Release("chroot agent disconnected", true);
    }
    return true;
  }

  void ForgetAgent() {
    if (agent_fd_ >= 0) close(agent_fd_);
    agent_fd_ = -1;
    agent_reader_.Reset();
    agent_continuous_ = false;
    agent_timing_required_ = false;
  }

  void ObserveHotplug(const hdmi_los_message &event) {
    const bool connected = event.flags & HDMI_LOS_FLAG_CONNECTED;
    if (!connected) {
      ++disconnect_generation_;
      bool paused = restart_.ready() && !active_ && !starting_;
      restart_.Cancel();
      // Startup rollback must stop Xorg/release the lease before restoring
      // Android's preferred mode; never run that recovery in an acquire read.
      if (paused) Disarm("connected restart cancelled: HDMI unplugged");
    }
    CacheComposerStatus(event);
    composer_disconnect_pending_ = !connected;
  }

  bool EnsureComposer() {
    if (composer_fd_ >= 0) return true;
    composer_fd_ = connect_abstract(HDMI_LOS_COMPOSER_SOCKET, SOCK_SEQPACKET);
    if (composer_fd_ >= 0) {
      timeval timeout = {kComposerRequestSeconds, 0};
      setsockopt(composer_fd_, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
      setsockopt(composer_fd_, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    }
    return composer_fd_ >= 0;
  }

  bool ComposerRequest(uint16_t opcode, hdmi_los_message *response, int *lease_fd = nullptr,
                       uint32_t flags = 0) {
    if (!EnsureComposer()) return false;
    hdmi_los_message request = make_message(opcode);
    request.version = HDMI_LOS_VERSION;
    request.flags = flags;
    if (!send_with_fd(composer_fd_, request, -1)) {
      close(composer_fd_);
      composer_fd_ = -1;
      return false;
    }
    for (;;) {
      int received_fd = -1;
      if (!recv_with_fd(composer_fd_, response, &received_fd) ||
          !valid_composer_message(*response)) {
        if (received_fd >= 0) close(received_fd);
        close(composer_fd_);
        composer_fd_ = -1;
        return false;
      }
      if (response->opcode == HDMI_LOS_OP_HOTPLUG && response->request_id == 0) {
        if (received_fd >= 0) close(received_fd);
        ObserveHotplug(*response);
        continue;
      }
      if (response->request_id != request.request_id) {
        if (received_fd >= 0) close(received_fd);
        close(composer_fd_);
        composer_fd_ = -1;
        return false;
      }
      if (lease_fd) *lease_fd = received_fd;
      else if (received_fd >= 0) close(received_fd);
      CacheComposerStatus(*response);
      if (opcode == HDMI_LOS_OP_ACQUIRE && connected_start_ &&
          response->status == HDMI_LOS_OK) {
        // Acquire replies contain phase/object identity, not timing. Preserve
        // that reply (and its received lease FD) and query the composer's real
        // status after each successful phase. Never infer mode from a phase ACK.
        hdmi_los_message timing_status = {};
        bool queried = ComposerRequest(HDMI_LOS_OP_STATUS, &timing_status);
        if (!queried || timing_status.status != HDMI_LOS_OK ||
            restart_.Expired(monotonic_ms()) || !restart_.Matches(timing_status)) {
          response->status = queried ? HDMI_LOS_ERR_STATE : HDMI_LOS_ERR_IO;
          snprintf(response->detail, sizeof(response->detail),
                   "cannot verify connected restart timing after composer acquire");
        }
      }
      return !composer_disconnect_pending_;
    }
  }

  void CacheComposerStatus(const hdmi_los_message &status) {
    composer_flags_ = status.flags;
    active_width_ = status.active_width;
    active_height_ = status.active_height;
    active_refresh_millihz_ = status.active_refresh_millihz;
    if (status.flags & HDMI_LOS_FLAG_CONNECTED) composer_disconnect_pending_ = false;
  }

  ComposerHotplug HandleComposerReadable() {
    hdmi_los_message event = {};
    int passed_fd = -1;
    if (!recv_with_fd(composer_fd_, &event, &passed_fd) ||
        !valid_composer_message(event) || event.opcode != HDMI_LOS_OP_HOTPLUG ||
        event.request_id != 0) {
      if (passed_fd >= 0) close(passed_fd);
      return ComposerHotplug::kInvalid;
    }
    if (passed_fd >= 0) close(passed_fd);
    ObserveHotplug(event);
    bool connected = event.flags & HDMI_LOS_FLAG_CONNECTED;
    log_transition(connected ? "composer reports external display connected" :
                               "composer reports external display disconnected");
    return connected ? ComposerHotplug::kConnected : ComposerHotplug::kDisconnected;
  }

  bool HandleAgentEvent(const hdmi_los_message &event) {
    if (event.opcode == HDMI_LOS_OP_AGENT_PROGRESS) {
      char text[sizeof(event.detail) + 1] = {};
      memcpy(text, event.detail, sizeof(event.detail));
      log_transition(text);
      hdmi_los_message acknowledgement = make_message(HDMI_LOS_OP_AGENT_PROGRESS_ACK);
      acknowledgement.request_id = event.request_id;
      acknowledgement.status = HDMI_LOS_OK;
      acknowledgement.flags = event.flags;
      return write_full(agent_fd_, &acknowledgement, sizeof(acknowledgement));
    }
    return event.opcode != HDMI_LOS_OP_AGENT_FAILED;
  }

  bool WaitAgent(uint16_t wanted, int timeout_ms, hdmi_los_message *response,
                 bool stopping = false, uint32_t expected_request_id = 0) {
    int64_t end = monotonic_ms() + timeout_ms;
    while (monotonic_ms() < end) {
      pollfd fds[4] = {
          {agent_fd_, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
          {stopping ? -1 : volumes_.down, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
          {stopping ? -1 : volumes_.up, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
          {stopping ? -1 : composer_fd_, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
      };
      int64_t remaining_ms = std::min<int64_t>(100, end - monotonic_ms());
      if (!stopping && deadline_ms_ > 0) {
        remaining_ms = std::min<int64_t>(remaining_ms, deadline_ms_ - monotonic_ms());
      }
      if (remaining_ms <= 0) return false;
      int result = poll(fds, 4, static_cast<int>(remaining_ms));
      if (result < 0 && errno == EINTR) continue;
      if (result < 0 || (!(fds[0].revents & POLLIN) &&
          (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)))) return false;
      if (!stopping && volumes_.Update(fds[1].revents, fds[2].revents)) return false;
      if (!stopping && composer_fd_ >= 0 && (fds[3].revents & (POLLERR | POLLHUP | POLLNVAL))) return false;
      if (!stopping && composer_fd_ >= 0 && (fds[3].revents & POLLIN)) {
        ComposerHotplug hotplug = HandleComposerReadable();
        if (hotplug != ComposerHotplug::kConnected) {
          composer_disconnect_pending_ = true;
          return false;
        }
      }
      if (!stopping && deadline_ms_ > 0 && monotonic_ms() >= deadline_ms_) return false;
      if (fds[0].revents & POLLIN) {
        auto read = agent_reader_.ReadAvailable(agent_fd_, response);
        if (read == HdmiAgentReader::Result::kError) return false;
        if (read == HdmiAgentReader::Result::kPending) continue;
        if (!valid_message(*response) || monotonic_ms() >= end ||
            (!stopping && deadline_ms_ > 0 && monotonic_ms() >= deadline_ms_)) return false;
        if (stopping && response->opcode == HDMI_LOS_OP_AGENT_FAILED) continue;
        if (response->opcode == HDMI_LOS_OP_AGENT_PROGRESS) {
          if (!HandleAgentEvent(*response)) return false;
          continue;
        }
        if (response->opcode == wanted || response->opcode == HDMI_LOS_OP_AGENT_FAILED) {
          if (response->opcode == wanted && expected_request_id &&
              response->request_id != expected_request_id) continue;
          return response->opcode == wanted && response->status == HDMI_LOS_OK;
        }
      }
    }
    return false;
  }

  void LoadConfiguredMode() {
    std::string text;
    uint32_t width = 0, height = 0, refresh = 0;
    if (read_text(kModeConfig, &text) &&
        sscanf(text.c_str(), "%u %u %u", &width, &height, &refresh) == 3 &&
        ((!width && !height && !refresh) ||
         (width >= 640 && height >= 480 && refresh >= 10000 && refresh <= 240000))) {
      requested_width_ = width;
      requested_height_ = height;
      requested_refresh_millihz_ = refresh;
    }
  }

  bool SaveConfiguredMode() {
    char text[96];
    snprintf(text, sizeof(text), "%u %u %u\n", requested_width_, requested_height_,
             requested_refresh_millihz_);
    return write_atomic_text(kModeConfig, text);
  }

  bool ApplyPreferredMode(std::string *detail) {
    std::string previous;
    uint32_t old_width = 0, old_height = 0, old_refresh = 0;
    if (!run_display_command({"get-user-preferred-display-mode"}, &previous) ||
        !parse_saved_mode(previous, &old_width, &old_height, &old_refresh)) {
      *detail = "cannot snapshot Android's global preferred display mode";
      return false;
    }
    char journal[96];
    snprintf(journal, sizeof(journal), "%u %u %u\n", old_width, old_height, old_refresh);
    if (!write_atomic_text(kModeJournal, journal)) {
      *detail = "cannot create the preferred-mode recovery journal";
      return false;
    }

    char width[24], height[24], refresh[32];
    snprintf(width, sizeof(width), "%u", requested_width_);
    snprintf(height, sizeof(height), "%u", requested_height_);
    snprintf(refresh, sizeof(refresh), "%.3f",
             static_cast<double>(requested_refresh_millihz_) / 1000.0);
    std::string output;
    bool applied = requested_width_ == 0 && requested_height_ == 0 &&
                   requested_refresh_millihz_ == 0 ?
        run_display_command({"clear-user-preferred-display-mode"}, &output) :
        run_display_command({"set-user-preferred-display-mode", width, height, refresh},
                            &output);
    if (!applied) {
      *detail = "Android rejected the preferred external display mode: " + output;
      RecoverPreferredMode("preferred-mode apply failure");
      return false;
    }
    return true;
  }

  bool RecoverPreferredMode(const char *reason) {
    std::string journal;
    if (!read_text(kModeJournal, &journal)) return true;
    uint32_t width = 0, height = 0, refresh = 0;
    if (sscanf(journal.c_str(), "%u %u %u", &width, &height, &refresh) != 3) {
      log_line("error", "preferred-mode recovery journal is invalid; preserving it");
      return false;
    }
    std::string output;
    bool ok;
    if (!width && !height && !refresh) {
      ok = run_display_command({"clear-user-preferred-display-mode"}, &output);
    } else {
      char width_text[24], height_text[24], refresh_text[32];
      snprintf(width_text, sizeof(width_text), "%u", width);
      snprintf(height_text, sizeof(height_text), "%u", height);
      snprintf(refresh_text, sizeof(refresh_text), "%.3f",
               static_cast<double>(refresh) / 1000.0);
      ok = run_display_command({"set-user-preferred-display-mode", width_text, height_text,
                                refresh_text}, &output);
    }
    if (ok) {
      unlink(kModeJournal);
      std::string message = "restored Android preferred display mode: ";
      message += reason;
      log_line("info", message.c_str());
    } else {
      log_line("error", "could not restore Android preferred display mode; journal retained");
    }
    return ok;
  }

  bool RequestedModeMatches() const {
    if (!(composer_flags_ & HDMI_LOS_FLAG_CONNECTED) ||
        !(composer_flags_ & HDMI_LOS_FLAG_ACTIVE_MODE)) return false;
    if (!requested_width_ && !requested_height_ && !requested_refresh_millihz_) return true;
    int64_t refresh_delta = static_cast<int64_t>(active_refresh_millihz_) -
                            static_cast<int64_t>(requested_refresh_millihz_);
    return active_width_ == requested_width_ && active_height_ == requested_height_ &&
           std::llabs(refresh_delta) <= 100;
  }

  int SetMode(const hdmi_los_message &request, std::string *detail) {
    if (active_ || armed_ || probing_ || restart_.ready()) {
      *detail = "stop or disarm HDMI Xorg before changing its preferred mode";
      return HDMI_LOS_ERR_BUSY;
    }
    bool native = !request.requested_width && !request.requested_height &&
                  !request.requested_refresh_millihz;
    if (!native && (request.requested_width < 640 || request.requested_height < 480 ||
        request.requested_refresh_millihz < 10000 ||
        request.requested_refresh_millihz > 240000)) {
      *detail = "invalid preferred mode";
      return HDMI_LOS_ERR_PROTOCOL;
    }
    requested_width_ = request.requested_width;
    requested_height_ = request.requested_height;
    requested_refresh_millihz_ = request.requested_refresh_millihz;
    if (!SaveConfiguredMode()) {
      *detail = "could not persist preferred mode";
      return HDMI_LOS_ERR_IO;
    }
    *detail = "preferred mode saved; arm before connecting HDMI";
    return HDMI_LOS_OK;
  }

  int Arm(const hdmi_los_message &request, std::string *detail) {
    if (active_ || probing_) return HDMI_LOS_ERR_BUSY;
    if (restart_.ready()) {
      *detail = "connected session paused; use resume or disarm";
      return HDMI_LOS_ERR_BUSY;
    }
    if (armed_) {
      *detail = state_detail_;
      return HDMI_LOS_OK;
    }
    if (request.requested_width || request.requested_height ||
        request.requested_refresh_millihz) {
      int mode_status = SetMode(request, detail);
      if (mode_status != HDMI_LOS_OK) return mode_status;
    }
    if (!compatible() || !diagnostic_dump_ready()) {
      *detail = "module compatibility gate is closed";
      return HDMI_LOS_ERR_INCOMPATIBLE;
    }
    armed_ = true;
    preference_applied_ = false;
    stable_mode_samples_ = 0;
    armed_since_ms_ = monotonic_ms();
    next_mode_poll_ms_ = 0;
    composer_disconnect_pending_ = false;
    hdmi_los_message status = {};
    if (!ComposerRequest(HDMI_LOS_OP_STATUS, &status)) {
      replug_required_ = true;
      state_detail_ = "armed; waiting for composer to confirm HDMI is disconnected";
    } else if (composer_flags_ & HDMI_LOS_FLAG_CONNECTED) {
      replug_required_ = true;
      state_detail_ = "armed; unplug HDMI so the preferred mode can be set safely";
    } else {
      replug_required_ = false;
      if (!ApplyPreferredMode(detail)) {
        armed_ = false;
        return HDMI_LOS_ERR_IO;
      }
      preference_applied_ = true;
      state_detail_ = "armed; connect HDMI, accept Mirror, and start the chroot agent";
    }
    *detail = state_detail_;
    return HDMI_LOS_OK;
  }

  void Disarm(const char *reason) {
    restart_.Cancel();
    armed_ = false;
    preference_applied_ = false;
    replug_required_ = false;
    stable_mode_samples_ = 0;
    next_mode_poll_ms_ = 0;
    composer_disconnect_pending_ = false;
    RecoverPreferredMode(reason);
    state_detail_ = reason;
  }

  void PreserveArmAcrossDisconnect() {
    // A connected arm request deliberately waits for this unplug before it is
    // allowed to change Android's preferred external mode.  The disconnect is
    // therefore progress, not a reason to cancel the armed request.
    composer_disconnect_pending_ = false;
    replug_required_ = false;
    stable_mode_samples_ = 0;
    next_mode_poll_ms_ = 0;
    state_detail_ = "armed; HDMI disconnected, applying preferred mode";
  }

  void AdvanceArmed() {
    int64_t now = monotonic_ms();
    if (now < next_mode_poll_ms_) return;
    next_mode_poll_ms_ = now + kModePollMs;
    hdmi_los_message status = {};
    if (!ComposerRequest(HDMI_LOS_OP_STATUS, &status)) {
      stable_mode_samples_ = 0;
      state_detail_ = "armed; waiting for the patched composer service";
      return;
    }
    bool connected = composer_flags_ & HDMI_LOS_FLAG_CONNECTED;
    if (!connected) {
      stable_mode_samples_ = 0;
      if (!preference_applied_) {
        std::string detail;
        if (!ApplyPreferredMode(&detail)) {
          std::string failure = detail.empty() ?
              "could not set the preferred mode while HDMI was disconnected" : detail;
          Disarm(failure.c_str());
          state_detail_ = failure;
          return;
        }
        preference_applied_ = true;
      }
      replug_required_ = false;
      state_detail_ = "armed; connect HDMI and accept Mirror";
      return;
    }
    if (replug_required_ || !preference_applied_) {
      state_detail_ = "armed; unplug HDMI so the preferred mode can be set safely";
      return;
    }
    if (!RequestedModeMatches()) {
      stable_mode_samples_ = 0;
      if (now - armed_since_ms_ >= kModeMismatchMs) {
        state_detail_ = "armed; connected HDMI mode does not match the configured preset";
      } else {
        state_detail_ = "armed; waiting for Android to settle on the configured mode";
      }
      return;
    }
    if (!(composer_flags_ & HDMI_LOS_FLAG_LEASE_READY)) {
      stable_mode_samples_ = 0;
      state_detail_ = "armed; Mirror is connected but the display is not lease-ready yet";
      return;
    }
    if (agent_fd_ < 0) {
      stable_mode_samples_ = 0;
      state_detail_ = "armed; matching HDMI is ready, start the chroot run-agent";
      return;
    }
    stable_mode_samples_++;
    state_detail_ = "armed; matching mode is stable, starting Xorg";
    if (stable_mode_samples_ < kModeStableSamples) return;
    std::string detail;
    int status_code = Start(HDMI_LOS_PROBE_XORG_LEGACY, &detail);
    if (status_code != HDMI_LOS_OK) {
      std::string failure = detail.empty() ? "automatic Xorg startup failed" : detail;
      Disarm(failure.c_str());
      state_detail_ = failure;
    }
  }

  // Platform boundary: host lifecycle fixtures replace only these Android
  // prerequisites, while running the production state machine and sockets.
  virtual int AcquireStartupGuards(std::string *detail) {
    if (!compatible()) {
      *detail = "module compatibility gate is closed";
      return HDMI_LOS_ERR_INCOMPATIBLE;
    }
    if (!diagnostic_dump_ready()) {
      *detail = "display recovery dump gate is not enabled";
      return HDMI_LOS_ERR_INCOMPATIBLE;
    }
    if (!volumes_.Acquire()) {
      *detail = "both physical volume inputs are required";
      return HDMI_LOS_ERR_UNAVAILABLE;
    }
    if (!set_wake_lock(true)) {
      *detail = "could not acquire the mandatory suspend blocker";
      CleanupGuards();
      return HDMI_LOS_ERR_UNAVAILABLE;
    }
    return HDMI_LOS_OK;
  }

  int AbortStart(int failure, const std::string &message, std::string *detail) {
    // Keep the originating error separate from STOP/RELEASE replies.
    bool restored = Release(message.c_str(), true);
    *detail = message;
    if (!restored) *detail += "; forced recovery after stop/restore failure";
    return failure;
  }

  int Start(uint32_t probe_mode, std::string *detail) {
    if (active_) return HDMI_LOS_ERR_BUSY;
    if (probe_mode != HDMI_LOS_PROBE_XORG_LEGACY &&
        probe_mode != HDMI_LOS_PROBE_XORG_ATOMIC) {
      *detail = "invalid Xorg probe mode";
      return HDMI_LOS_ERR_PROTOCOL;
    }
    if (agent_fd_ < 0) {
      *detail = "start the chroot agent first";
      return HDMI_LOS_ERR_AGENT;
    }
    int gate = AcquireStartupGuards(detail);
    if (gate != HDMI_LOS_OK) return gate;
    starting_ = true;
    session_disconnect_generation_ = disconnect_generation_;
    session_continuous_ = agent_continuous_;
    // The user's session limit and the composer's crash watchdog have separate
    // lifetimes. Renew the latter for timing-guard sessions, including bounded
    // tests, so the broker can stop Xorg before restoring Android at 60 seconds.
    composer_renewable_ = session_continuous_ || agent_timing_required_;
    // Count preparation and Xorg verification inside the mandatory window.
    // Continuous sessions retain all bounded startup waits and renew the
    // composer's independent watchdog only after Xorg is active.
    deadline_ms_ = session_continuous_ ? 0 :
        monotonic_ms() + kSessionSeconds * 1000;
    auto composer_acquire_flags = [&](uint32_t phase) {
      return phase | (composer_renewable_ ? HDMI_LOS_FLAG_CONTINUOUS : 0);
    };

    log_transition("takeover agent-prepare begin");
    hdmi_los_message request = make_message(HDMI_LOS_OP_AGENT_PREPARE);
    request.flags = probe_mode;
    hdmi_los_message response = {};
    if (!write_full(agent_fd_, &request, sizeof(request)) ||
        !WaitAgent(HDMI_LOS_OP_AGENT_READY, kAgentPrepareMs, &response, false, request.request_id)) {
      return AbortStart(HDMI_LOS_ERR_AGENT,
          response.detail[0] ? response.detail : "chroot agent preparation failed", detail);
    }
    log_transition("takeover agent-prepare complete");

    int lease_fd = -1;
    log_transition("takeover composer-prepare begin");
    response = {};
    if (!ComposerRequest(HDMI_LOS_OP_ACQUIRE, &response, nullptr,
                         composer_acquire_flags(HDMI_LOS_ACQUIRE_PREPARE)) ||
        response.status != HDMI_LOS_OK) {
      *detail = response.detail[0] ? response.detail : "composer lease request failed";
      int failure_status = response.status ? response.status : HDMI_LOS_ERR_IO;
      log_transition("takeover composer-prepare failed");
      return AbortStart(failure_status, *detail, detail);
    }
    log_transition("takeover composer-prepare complete");

    log_transition("takeover composer-pause begin");
    response = {};
    if (!ComposerRequest(HDMI_LOS_OP_ACQUIRE, &response, nullptr,
                         composer_acquire_flags(HDMI_LOS_ACQUIRE_PAUSE)) ||
        response.status != HDMI_LOS_OK) {
      *detail = response.detail[0] ? response.detail : "composer display pause failed";
      int failure_status = response.status ? response.status : HDMI_LOS_ERR_IO;
      log_transition("takeover composer-pause failed");
      return AbortStart(failure_status, *detail, detail);
    }
    log_transition("takeover composer-pause complete");

    log_transition("takeover composer-create begin");
    response = {};
    if (!ComposerRequest(HDMI_LOS_OP_ACQUIRE, &response, &lease_fd,
                         composer_acquire_flags(HDMI_LOS_ACQUIRE_CREATE)) ||
        response.status != HDMI_LOS_OK ||
        lease_fd < 0) {
      *detail = response.detail[0] ? response.detail : "composer lease creation failed";
      int failure_status = response.status ? response.status : HDMI_LOS_ERR_IO;
      log_transition("takeover composer-create failed");
      if (lease_fd >= 0) close(lease_fd);
      return AbortStart(failure_status, *detail, detail);
    }
    log_transition("takeover composer-create complete");

    if (agent_timing_required_) {
      if (!timing_.Start(lease_fd, response.connector_id, response.crtc_id,
                         response.plane_id, detail)) {
        close(lease_fd);
        return AbortStart(HDMI_LOS_ERR_INCOMPATIBLE, *detail, detail);
      }
      std::string message = "timing session valid, " + timing_.Describe();
      log_line("info", message.c_str());
      timing_check_ms_ = monotonic_ms() + 500;
    }

    log_transition("takeover agent-start begin");
    request = make_message(HDMI_LOS_OP_AGENT_START);
    request.connector_id = response.connector_id;
    request.crtc_id = response.crtc_id;
    request.plane_id = response.plane_id;
    request.flags = probe_mode;
    if (!send_with_fd(agent_fd_, request, lease_fd)) {
      close(lease_fd);
      return AbortStart(HDMI_LOS_ERR_AGENT, "could not deliver lease fd to chroot agent", detail);
    }
    close(lease_fd);
    response = {};
    if (!WaitAgent(HDMI_LOS_OP_AGENT_READY, kAgentStartMs, &response, false, request.request_id)) {
      bool failed = response.opcode == HDMI_LOS_OP_AGENT_FAILED;
      return AbortStart(failed ? HDMI_LOS_ERR_AGENT : HDMI_LOS_ERR_TIMEOUT,
          failed && response.detail[0] ? response.detail :
              "Xorg did not complete verified scanout within 15 seconds", detail);
    }
    if (session_disconnect_generation_ != disconnect_generation_ ||
        (connected_start_ && (!restart_.ready() || restart_.Expired(monotonic_ms()))))
      return AbortStart(HDMI_LOS_ERR_STATE, "HDMI session changed during startup", detail);
    log_transition("takeover agent-start complete");

    starting_ = false;
    active_ = true;
    if (composer_renewable_) {
      composer_heartbeat_ms_ = monotonic_ms() + kComposerHeartbeatSeconds * 1000;
    }
    state_detail_ = probe_mode == HDMI_LOS_PROBE_XORG_ATOMIC ?
        "traced atomic Xorg owns external display" :
        "traced legacy Xorg owns external display";
    if (session_continuous_) state_detail_ += "; continuous watchdog mode";
    *detail = state_detail_;
    log_line("info", session_continuous_ ?
        "Xorg session active; continuous composer watchdog armed" :
        "Xorg session active; 60 second timer armed");
    return HDMI_LOS_OK;
  }

  int FinishLeaseProbe(int lease_fd, int status, const std::string &message,
                       std::string *detail) {
    if (lease_fd >= 0) {
      log_transition("probe lease-hold lease-close begin");
      close(lease_fd);
      log_transition("probe lease-hold lease-close complete");
    }
    log_transition("probe lease-hold composer-release begin");
    hdmi_los_message response = {};
    bool released = ComposerRequest(HDMI_LOS_OP_RELEASE, &response) &&
                    response.status == HDMI_LOS_OK;
    log_transition(released ? "probe lease-hold composer-release complete" :
                              "probe lease-hold composer-release failed");
    probing_ = false;
    CleanupGuards();
    state_detail_ = message;
    *detail = message;
    if (status == HDMI_LOS_OK && !released) {
      *detail = "lease probe completed but composer restore failed";
      return HDMI_LOS_ERR_IO;
    }
    return status;
  }

  int ProbeLease(std::string *detail) {
    if (active_ || probing_) return HDMI_LOS_ERR_BUSY;
    if (!compatible()) {
      *detail = "module compatibility gate is closed";
      return HDMI_LOS_ERR_INCOMPATIBLE;
    }
    if (!diagnostic_dump_ready()) {
      *detail = "display recovery dump gate is not enabled";
      return HDMI_LOS_ERR_INCOMPATIBLE;
    }
    if (!volumes_.Acquire()) {
      *detail = "both physical volume inputs are required";
      return HDMI_LOS_ERR_UNAVAILABLE;
    }
    if (!set_wake_lock(true)) {
      *detail = "could not acquire the mandatory suspend blocker";
      CleanupGuards();
      return HDMI_LOS_ERR_UNAVAILABLE;
    }
    probing_ = true;
    deadline_ms_ = monotonic_ms() + 15000;
    state_detail_ = "lease-only diagnostic probe running";

    hdmi_los_message response = {};
    log_transition("probe lease-hold composer-prepare begin");
    if (!ComposerRequest(HDMI_LOS_OP_ACQUIRE, &response, nullptr,
                         HDMI_LOS_ACQUIRE_PREPARE) || response.status != HDMI_LOS_OK) {
      log_transition("probe lease-hold composer-prepare failed");
      int status = response.status ? response.status : HDMI_LOS_ERR_IO;
      std::string message = response.detail[0] ? response.detail :
          "composer lease preparation failed";
      return FinishLeaseProbe(-1, status, message, detail);
    }
    log_transition("probe lease-hold composer-prepare complete");

    log_transition("probe lease-hold composer-pause begin");
    response = {};
    if (!ComposerRequest(HDMI_LOS_OP_ACQUIRE, &response, nullptr,
                         HDMI_LOS_ACQUIRE_PAUSE) || response.status != HDMI_LOS_OK) {
      log_transition("probe lease-hold composer-pause failed");
      int status = response.status ? response.status : HDMI_LOS_ERR_IO;
      std::string message = response.detail[0] ? response.detail :
          "composer display pause failed";
      return FinishLeaseProbe(-1, status, message, detail);
    }
    log_transition("probe lease-hold composer-pause complete");

    int lease_fd = -1;
    log_transition("probe lease-hold composer-create begin");
    response = {};
    if (!ComposerRequest(HDMI_LOS_OP_ACQUIRE, &response, &lease_fd,
                         HDMI_LOS_ACQUIRE_CREATE) || response.status != HDMI_LOS_OK ||
        lease_fd < 0) {
      log_transition("probe lease-hold composer-create failed");
      int status = response.status ? response.status : HDMI_LOS_ERR_IO;
      std::string message = response.detail[0] ? response.detail :
          "composer lease creation failed";
      return FinishLeaseProbe(lease_fd, status, message, detail);
    }
    log_transition("probe lease-hold composer-create complete");
    log_transition("probe lease-hold three-second hold begin");

    int64_t end = monotonic_ms() + kLeaseHoldMs;
    bool escaped = false;
    while (!g_stop && monotonic_ms() < end) {
      pollfd fds[2] = {
          {volumes_.down, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
          {volumes_.up, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
      };
      int result = poll(fds, 2, 50);
      if (result < 0 && errno == EINTR) continue;
      if (result < 0 || volumes_.Update(fds[0].revents, fds[1].revents)) {
        escaped = true;
        break;
      }
    }
    log_transition(escaped ? "probe lease-hold ended by volume escape" :
                             "probe lease-hold three-second hold complete");
    return FinishLeaseProbe(lease_fd, HDMI_LOS_OK,
                            escaped ? "lease-only probe restored early by volume escape" :
                                      "lease-only probe completed and Android restored",
                            detail);
  }

  void StopTiming() {
    if (!timing_.generation()) return;
    std::string report;
    timing_.Stop(&report);
    log_line("info", ("timing session stop: " + report).c_str());
  }

  void CleanupGuards() {
    StopTiming();
    volumes_.Release();
    set_wake_lock(false);
    deadline_ms_ = 0;
    composer_heartbeat_ms_ = 0;
    session_continuous_ = false;
    composer_renewable_ = false;
  }

  bool StopAgent(int timeout_ms = kAgentStopMs) {
    if (agent_fd_ < 0) return true;
    hdmi_los_message stop = make_message(HDMI_LOS_OP_AGENT_STOP);
    hdmi_los_message response = {};
    log_transition("restore agent-stop begin");
    bool stopped = write_full(agent_fd_, &stop, sizeof(stop)) &&
        WaitAgent(HDMI_LOS_OP_AGENT_READY, timeout_ms, &response, true, stop.request_id);
    log_transition(stopped ? "restore agent-stop complete" : "restore agent-stop timeout/failure");
    // Do not reuse a connection whose stop was not acknowledged. The agent's
    // independent disconnect cleanup is the fallback before forced revocation.
    if (!stopped) ForgetAgent();
    return stopped;
  }

  bool Release(const char *reason, bool tell_composer, bool keep_preferred_mode = false) {
    if (!active_ && !starting_ && volumes_.down < 0 && volumes_.up < 0 && !timing_.generation()) return true;
    log_line("warning", reason);
    log_transition("restore begin");
    bool stopped = StopAgent();
    bool restored = !tell_composer;
    StopTiming();
    if (tell_composer && composer_fd_ >= 0) {
      hdmi_los_message response = {};
      restored = ComposerRequest(HDMI_LOS_OP_RELEASE, &response) &&
                 response.status == HDMI_LOS_OK && response.state == HDMI_LOS_STATE_ANDROID;
    }
    active_ = false;
    starting_ = false;
    CleanupGuards();
    if (armed_) {
      armed_ = false;
      if (!keep_preferred_mode) preference_applied_ = false;
      replug_required_ = false;
      stable_mode_samples_ = 0;
      if (!keep_preferred_mode) RecoverPreferredMode(reason);
    }
    state_detail_ = reason;
    log_transition(stopped && restored ? "restore complete" : "restore forced or incomplete");
    return stopped && restored;
  }

  int PauseConnected(std::string *detail) {
    if (probing_ || (!active_ && !restart_.ready())) {
      *detail = "pause requires an active Linux HDMI session";
      return HDMI_LOS_ERR_STATE;
    }
    if (!active_) { *detail = "connected session already paused"; return HDMI_LOS_OK; }
    hdmi_los_message status = {};
    if (!ComposerRequest(HDMI_LOS_OP_STATUS, &status) ||
        status.status != HDMI_LOS_OK ||
        session_disconnect_generation_ != disconnect_generation_ ||
        !restart_.Remember(status, monotonic_ms())) {
      Release("cannot snapshot the original connected session timing", true);
      Disarm("connected pause cancelled: session changed or status unavailable");
      *detail = state_detail_;
      return HDMI_LOS_ERR_STATE;
    }
    if (!Release("explicit connected session pause", true, true) || !restart_.ready()) {
      Disarm("connected pause failed; restart cancelled");
      *detail = state_detail_;
      return HDMI_LOS_ERR_IO;
    }
    state_detail_ = "Linux paused; HDMI stays connected; use resume within five minutes";
    *detail = state_detail_;
    return HDMI_LOS_OK;
  }

  int ResumeConnected(std::string *detail) {
    if (active_ || probing_ || armed_) return HDMI_LOS_ERR_BUSY;
    if (!restart_.ready() || restart_.Expired(monotonic_ms())) {
      if (restart_.ready()) Disarm("connected restart expired");
      *detail = "resume requires a successful connected pause";
      return HDMI_LOS_ERR_STATE;
    }
    if (agent_fd_ < 0) {
      *detail = "start the new chroot agent, then resume";
      return HDMI_LOS_ERR_AGENT;
    }
    const int64_t end = monotonic_ms() + kModeMismatchMs;
    unsigned stable = 0;
    while (!g_stop && monotonic_ms() < end) {
      if (restart_.Expired(monotonic_ms())) break;
      hdmi_los_message status = {};
      if (!ComposerRequest(HDMI_LOS_OP_STATUS, &status) || status.status != HDMI_LOS_OK || !restart_.ready() ||
          !(status.flags & HDMI_LOS_FLAG_CONNECTED)) break;
      if ((status.flags & HDMI_LOS_FLAG_ACTIVE_MODE) && !restart_.Matches(status)) break;
      if (restart_.Matches(status) && (status.flags & HDMI_LOS_FLAG_LEASE_READY)) {
        if (++stable >= kModeStableSamples) {
          // Retain the mode token through every acquire phase, and transfer
          // recovery-journal ownership to the new ordinary active session.
          armed_ = true;
          connected_start_ = true;
          int result = Start(HDMI_LOS_PROBE_XORG_LEGACY, detail);
          connected_start_ = false;
          restart_.Cancel();
          if (result != HDMI_LOS_OK) Disarm("connected resume startup failed");
          return result;
        }
      } else stable = 0;
      timespec delay = {0, kModePollMs * 1000000};
      while (nanosleep(&delay, &delay) < 0 && errno == EINTR && !g_stop) {}
    }
    Disarm("connected resume cancelled: unplug, mode change or readiness timeout");
    *detail = state_detail_;
    return HDMI_LOS_ERR_STATE;
  }

  hdmi_los_message Status(uint32_t request_id) {
    hdmi_los_message status = make_message(HDMI_LOS_OP_STATUS | HDMI_LOS_OP_RESPONSE);
    status.request_id = request_id;
    status.status = compatible() && diagnostic_dump_ready() ?
        HDMI_LOS_OK : HDMI_LOS_ERR_INCOMPATIBLE;
    status.requested_width = requested_width_;
    status.requested_height = requested_height_;
    status.requested_refresh_millihz = requested_refresh_millihz_;
    status.active_width = active_width_;
    status.active_height = active_height_;
    status.active_refresh_millihz = active_refresh_millihz_;
    status.flags |= composer_flags_ & (HDMI_LOS_FLAG_CONNECTED |
                                      HDMI_LOS_FLAG_LEASE_READY |
                                      HDMI_LOS_FLAG_ACTIVE_MODE);
    if (armed_) status.flags |= HDMI_LOS_FLAG_ARMED;
    if (replug_required_) status.flags |= HDMI_LOS_FLAG_REPLUG_REQUIRED;
    if (!compatible()) {
      status.state = HDMI_LOS_STATE_UNAVAILABLE;
      snprintf(status.detail, sizeof(status.detail), "incompatible or unverified Lineage build");
    } else if (!diagnostic_dump_ready()) {
      status.state = HDMI_LOS_STATE_UNAVAILABLE;
      snprintf(status.detail, sizeof(status.detail), "diagnostic display recovery dump gate is closed");
    } else if (probing_) {
      status.state = HDMI_LOS_STATE_PROBING;
      status.remaining_seconds = static_cast<uint32_t>(
          std::max<int64_t>(0, (deadline_ms_ - monotonic_ms() + 999) / 1000));
      snprintf(status.detail, sizeof(status.detail), "%s", state_detail_.c_str());
    } else if (active_) {
      status.state = HDMI_LOS_STATE_LEASED;
      if (session_continuous_) {
        status.flags |= HDMI_LOS_FLAG_CONTINUOUS;
      } else {
        status.remaining_seconds = static_cast<uint32_t>(
            std::max<int64_t>(0, (deadline_ms_ - monotonic_ms() + 999) / 1000));
      }
      snprintf(status.detail, sizeof(status.detail), "%s", state_detail_.c_str());
    } else if (armed_) {
      status.state = RequestedModeMatches() ? HDMI_LOS_STATE_ARMED : HDMI_LOS_STATE_WAITING;
      snprintf(status.detail, sizeof(status.detail), "%s", state_detail_.c_str());
    } else if (restart_.ready()) {
      status.state = HDMI_LOS_STATE_WAITING;
      snprintf(status.detail, sizeof(status.detail), "%s", state_detail_.c_str());
    } else if (agent_fd_ >= 0) {
      status.state = HDMI_LOS_STATE_AGENT_READY;
      if (agent_continuous_) status.flags |= HDMI_LOS_FLAG_CONTINUOUS;
      snprintf(status.detail, sizeof(status.detail), "%s",
               diagnostic_only() ? "diagnostic build ready; use a root probe command" :
               agent_continuous_ ? "continuous chroot agent ready; Android owns display" :
                                   "bounded chroot agent ready; Android owns display");
    } else {
      status.state = HDMI_LOS_STATE_ANDROID;
      snprintf(status.detail, sizeof(status.detail), "Android owns display; chroot agent absent");
    }
    return status;
  }

  virtual void AcceptClient() {
    int client = accept4(listen_fd_, nullptr, nullptr, SOCK_CLOEXEC);
    if (client < 0) return;
    timeval timeout = {1, 0};
    setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    ucred credentials = {};
    socklen_t length = sizeof(credentials);
    if (getsockopt(client, SOL_SOCKET, SO_PEERCRED, &credentials, &length) < 0) {
      close(client);
      return;
    }
    hdmi_los_message request = {};
    if (!read_full(client, &request, sizeof(request)) || !valid_message(request)) {
      close(client);
      return;
    }

    uid_t expected_tile = tile_uid();
    bool root = credentials.uid == 0;
    bool tile = expected_tile != static_cast<uid_t>(-1) && credentials.uid == expected_tile;
    if (!root && !tile) {
      hdmi_los_message denied = Status(request.request_id);
      denied.status = HDMI_LOS_ERR_PERMISSION;
      snprintf(denied.detail, sizeof(denied.detail), "peer uid is not authorized");
      write_full(client, &denied, sizeof(denied));
      close(client);
      return;
    }

    if (request.opcode == HDMI_LOS_OP_AGENT_REGISTER && root) {
      std::string timing_error;
      if (request.flags & ~(HDMI_LOS_FLAG_CONTINUOUS | HDMI_LOS_FLAG_TIMING_REQUIRED)) {
        hdmi_los_message rejected = Status(request.request_id);
        rejected.status = HDMI_LOS_ERR_PROTOCOL;
        snprintf(rejected.detail, sizeof(rejected.detail), "unknown agent registration flag");
        write_full(client, &rejected, sizeof(rejected));
        close(client);
      } else if ((request.flags & HDMI_LOS_FLAG_TIMING_REQUIRED) &&
                 !HdmiTimingSession::Available(&timing_error)) {
        hdmi_los_message rejected = Status(request.request_id);
        rejected.status = HDMI_LOS_ERR_INCOMPATIBLE;
        snprintf(rejected.detail, sizeof(rejected.detail), "%s", timing_error.c_str());
        write_full(client, &rejected, sizeof(rejected));
        close(client);
      } else if (active_ || probing_ || agent_fd_ >= 0) {
        hdmi_los_message busy = Status(request.request_id);
        busy.status = HDMI_LOS_ERR_BUSY;
        write_full(client, &busy, sizeof(busy));
        close(client);
      } else {
        agent_fd_ = client;
        agent_reader_.Reset();
        agent_continuous_ = request.flags & HDMI_LOS_FLAG_CONTINUOUS;
        agent_timing_required_ = request.flags & HDMI_LOS_FLAG_TIMING_REQUIRED;
        hdmi_los_message ready = Status(request.request_id);
        ready.status = HDMI_LOS_OK;
        ready.state = HDMI_LOS_STATE_AGENT_READY;
        // Registration acknowledges the accepted agent capabilities even when
        // Status describes an armed display or a paused connected restart.
        if (agent_continuous_) ready.flags |= HDMI_LOS_FLAG_CONTINUOUS;
        if (agent_timing_required_) ready.flags |= HDMI_LOS_FLAG_TIMING_REQUIRED;
        write_full(agent_fd_, &ready, sizeof(ready));
        log_line("info", "chroot agent registered");
      }
      return;
    }

    hdmi_los_message composer_status = {};
    (void)ComposerRequest(HDMI_LOS_OP_STATUS, &composer_status);
    hdmi_los_message response = Status(request.request_id);
    if (request.opcode == HDMI_LOS_OP_TOGGLE) {
      if (active_) {
        Release("Quick Settings tile requested restore", true);
        response = Status(request.request_id);
      } else if (armed_ || restart_.ready()) {
        Disarm("HDMI Xorg disarmed; Android preferred mode restored");
        response = Status(request.request_id);
      } else if (diagnostic_only()) {
        response.status = HDMI_LOS_ERR_STATE;
        snprintf(response.detail, sizeof(response.detail),
                 "diagnostic build: use a root probe command");
      } else {
        std::string detail;
        int start_status = Arm(request, &detail);
        response = Status(request.request_id);
        response.status = start_status;
        if (!detail.empty()) snprintf(response.detail, sizeof(response.detail), "%s", detail.c_str());
      }
    } else if (request.opcode == HDMI_LOS_OP_PAUSE ||
               request.opcode == HDMI_LOS_OP_RESUME ||
               request.opcode == HDMI_LOS_OP_RESTART) {
      std::string detail;
      int result = HDMI_LOS_ERR_PERMISSION;
      if (!root) detail = "connected session control is root-only";
      else if (request.flags || request.requested_width || request.requested_height ||
               request.requested_refresh_millihz) {
        result = HDMI_LOS_ERR_PROTOCOL;
        detail = "connected session control cannot request flags or a mode";
      } else {
        result = request.opcode == HDMI_LOS_OP_RESUME ? HDMI_LOS_OK : PauseConnected(&detail);
        if (result == HDMI_LOS_OK && request.opcode != HDMI_LOS_OP_PAUSE)
          result = ResumeConnected(&detail);
      }
      response = Status(request.request_id);
      response.status = result;
      snprintf(response.detail, sizeof(response.detail), "%s", detail.c_str());
    } else if (request.opcode == HDMI_LOS_OP_SET_MODE) {
      std::string detail;
      int mode_status = SetMode(request, &detail);
      response = Status(request.request_id);
      response.status = mode_status;
      snprintf(response.detail, sizeof(response.detail), "%s", detail.c_str());
    } else if (request.opcode == HDMI_LOS_OP_ARM) {
      std::string detail;
      int arm_status = Arm(request, &detail);
      response = Status(request.request_id);
      response.status = arm_status;
      snprintf(response.detail, sizeof(response.detail), "%s", detail.c_str());
    } else if (request.opcode == HDMI_LOS_OP_DISARM) {
      if (active_) Release("HDMI Xorg stopped and disarmed", true);
      else Disarm("HDMI Xorg disarmed; Android preferred mode restored");
      response = Status(request.request_id);
    } else if (request.opcode == HDMI_LOS_OP_PROBE) {
      if (!root) {
        response.status = HDMI_LOS_ERR_PERMISSION;
        snprintf(response.detail, sizeof(response.detail), "diagnostic probes require root");
      } else {
        std::string detail;
        int probe_status;
        if (request.flags == HDMI_LOS_PROBE_LEASE_HOLD) {
          probe_status = ProbeLease(&detail);
        } else if (request.flags == HDMI_LOS_PROBE_XORG_LEGACY ||
                   request.flags == HDMI_LOS_PROBE_XORG_ATOMIC) {
          probe_status = Start(request.flags, &detail);
        } else {
          probe_status = HDMI_LOS_ERR_PROTOCOL;
          detail = "unknown diagnostic probe mode";
        }
        response = Status(request.request_id);
        response.status = probe_status;
        if (!detail.empty()) snprintf(response.detail, sizeof(response.detail), "%s", detail.c_str());
      }
    } else if (request.opcode != HDMI_LOS_OP_STATUS && request.opcode != HDMI_LOS_OP_PING) {
      response.status = HDMI_LOS_ERR_PROTOCOL;
      snprintf(response.detail, sizeof(response.detail), "unsupported broker command");
    }
    write_full(client, &response, sizeof(response));
    close(client);
  }

  int listen_fd_ = -1;
  int composer_fd_ = -1;
  int agent_fd_ = -1;
  bool active_ = false;
  bool starting_ = false;
  uint64_t disconnect_generation_ = 0;
  uint64_t session_disconnect_generation_ = 0;
  HdmiAgentReader agent_reader_;
  bool probing_ = false;
  bool agent_continuous_ = false;
  bool session_continuous_ = false;
  bool composer_renewable_ = false;
  bool agent_timing_required_ = false;
  int64_t timing_check_ms_ = 0;
  HdmiTimingSession timing_;
  bool armed_ = false;
  bool preference_applied_ = false;
  bool replug_required_ = false;
  bool composer_disconnect_pending_ = false;
  uint32_t requested_width_ = 1920;
  uint32_t requested_height_ = 1080;
  uint32_t requested_refresh_millihz_ = 60000;
  uint32_t active_width_ = 0;
  uint32_t active_height_ = 0;
  uint32_t active_refresh_millihz_ = 0;
  uint32_t composer_flags_ = 0;
  int stable_mode_samples_ = 0;
  int64_t armed_since_ms_ = 0;
  int64_t next_mode_poll_ms_ = 0;
  int64_t deadline_ms_ = 0;
  int64_t composer_heartbeat_ms_ = 0;
  VolumeGuard volumes_;
  HdmiConnectedRestart restart_;
  bool connected_start_ = false;
  std::string state_detail_ = "Android owns display";
};

void print_usage() {
  fprintf(stderr,
          "usage: hdmi-losd [daemon|status|toggle|arm|disarm|pause|resume|restart|mode native|mode 1080p60|"
          "mode 2160p60|mode WIDTHxHEIGHT@HZ|probe lease-hold|probe xorg-legacy|"
          "probe xorg-atomic]\n");
}

int client_main(int argc, char **argv) {
  uint16_t opcode = HDMI_LOS_OP_STATUS;
  uint32_t flags = HDMI_LOS_PROBE_NONE;
  uint32_t requested_width = 0;
  uint32_t requested_height = 0;
  uint32_t requested_refresh_millihz = 0;
  if (argc == 1 && strcmp(argv[0], "toggle") == 0) {
    opcode = HDMI_LOS_OP_TOGGLE;
  } else if (argc == 1 && strcmp(argv[0], "arm") == 0) {
    opcode = HDMI_LOS_OP_ARM;
  } else if (argc == 1 && strcmp(argv[0], "disarm") == 0) {
    opcode = HDMI_LOS_OP_DISARM;
  } else if (argc == 1 && strcmp(argv[0], "pause") == 0) {
    opcode = HDMI_LOS_OP_PAUSE;
  } else if (argc == 1 && strcmp(argv[0], "resume") == 0) {
    opcode = HDMI_LOS_OP_RESUME;
  } else if (argc == 1 && strcmp(argv[0], "restart") == 0) {
    opcode = HDMI_LOS_OP_RESTART;
  } else if (argc == 1 && strcmp(argv[0], "status") == 0) {
    opcode = HDMI_LOS_OP_STATUS;
  } else if (argc == 2 && strcmp(argv[0], "mode") == 0) {
    opcode = HDMI_LOS_OP_SET_MODE;
    float refresh = 0.0f;
    if (strcmp(argv[1], "native") == 0) {
      requested_width = requested_height = requested_refresh_millihz = 0;
    } else if (strcmp(argv[1], "1080p60") == 0) {
      requested_width = 1920;
      requested_height = 1080;
      requested_refresh_millihz = 60000;
    } else if (strcmp(argv[1], "2160p60") == 0) {
      requested_width = 3840;
      requested_height = 2160;
      requested_refresh_millihz = 60000;
    } else if (sscanf(argv[1], "%ux%u@%f", &requested_width, &requested_height,
                      &refresh) == 3) {
      requested_refresh_millihz = static_cast<uint32_t>(lroundf(refresh * 1000.0f));
    } else {
      print_usage();
      return 2;
    }
  } else if (argc == 2 && strcmp(argv[0], "probe") == 0) {
    opcode = HDMI_LOS_OP_PROBE;
    if (strcmp(argv[1], "lease-hold") == 0) flags = HDMI_LOS_PROBE_LEASE_HOLD;
    else if (strcmp(argv[1], "xorg-legacy") == 0) flags = HDMI_LOS_PROBE_XORG_LEGACY;
    else if (strcmp(argv[1], "xorg-atomic") == 0) flags = HDMI_LOS_PROBE_XORG_ATOMIC;
    else {
      print_usage();
      return 2;
    }
  } else {
    print_usage();
    return 2;
  }
  int fd = connect_abstract(HDMI_LOS_BROKER_SOCKET, SOCK_STREAM);
  if (fd < 0) {
    perror("connect broker");
    return 1;
  }
  hdmi_los_message request = make_message(opcode);
  request.flags = flags;
  request.requested_width = requested_width;
  request.requested_height = requested_height;
  request.requested_refresh_millihz = requested_refresh_millihz;
  hdmi_los_message response = {};
  bool ok = write_full(fd, &request, sizeof(request)) && read_full(fd, &response, sizeof(response));
  close(fd);
  if (!ok || !valid_message(response)) return 1;
  printf("state=%u status=%d remaining=%u flags=0x%x requested=%ux%u@%.3f "
         "active=%ux%u@%.3f detail=%s\n", response.state, response.status,
         response.remaining_seconds, response.flags, response.requested_width,
         response.requested_height, response.requested_refresh_millihz / 1000.0,
         response.active_width, response.active_height,
         response.active_refresh_millihz / 1000.0, response.detail);
  return response.status == HDMI_LOS_OK ? 0 : 1;
}

}  // namespace

int main(int argc, char **argv) {
  if (argc >= 2 && strcmp(argv[1], "daemon") != 0) return client_main(argc - 1, argv + 1);
  if (argc > 2) {
    print_usage();
    return 2;
  }
  struct sigaction action = {};
  action.sa_handler = signal_handler;
  sigemptyset(&action.sa_mask);
  sigaction(SIGTERM, &action, nullptr);
  sigaction(SIGINT, &action, nullptr);
  sigaction(SIGHUP, &action, nullptr);
  Broker broker;
  return broker.Run();
}
