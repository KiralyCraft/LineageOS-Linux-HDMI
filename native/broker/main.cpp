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
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <string>

#include "hdmi_los_protocol.h"

namespace {

constexpr int kSessionSeconds = 60;
constexpr int kChordHoldMs = 3000;
constexpr int kAgentPrepareMs = 5000;
constexpr int kAgentStartMs = 15000;
constexpr int kAgentStopMs = 2000;
constexpr int kComposerRequestSeconds = 10;
constexpr const char *kModuleDir = "/data/adb/modules/hdmi-los";

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
  message.version = HDMI_LOS_VERSION;
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
    result = recvmsg(fd, &header, MSG_WAITALL);
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

bool set_wake_lock(bool acquire) {
  const char *path = acquire ? "/sys/power/wake_lock" : "/sys/power/wake_unlock";
  int fd = open(path, O_WRONLY | O_CLOEXEC);
  if (fd < 0) return false;
  const char value[] = "hdmi-los";
  bool ok = write(fd, value, sizeof(value) - 1) == static_cast<ssize_t>(sizeof(value) - 1);
  close(fd);
  return ok;
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
  int Run() {
    listen_fd_ = listen_abstract(HDMI_LOS_BROKER_SOCKET);
    if (listen_fd_ < 0) {
      log_errno("cannot bind broker socket");
      return 1;
    }
    log_line("info", "broker ready");
    while (!g_stop) {
      pollfd fds[5] = {
          {listen_fd_, POLLIN, 0},
          {composer_fd_, static_cast<short>(POLLERR | POLLHUP), 0},
          {agent_fd_, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
          {volumes_.down, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
          {volumes_.up, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
      };
      int result = poll(fds, 5, active_ ? 100 : 1000);
      if (result < 0 && errno == EINTR) continue;
      if (result < 0) break;
      if (fds[0].revents & POLLIN) AcceptClient();
      if (composer_fd_ >= 0 && (fds[1].revents & (POLLERR | POLLHUP | POLLNVAL))) {
        close(composer_fd_);
        composer_fd_ = -1;
        if (active_) Release("composer disconnected", false);
      }
      if (agent_fd_ >= 0 && (fds[2].revents & (POLLERR | POLLHUP | POLLNVAL))) {
        close(agent_fd_);
        agent_fd_ = -1;
        if (active_) Release("chroot agent disconnected", true);
      } else if (agent_fd_ >= 0 && (fds[2].revents & POLLIN)) {
        hdmi_los_message event = {};
        if (!read_full(agent_fd_, &event, sizeof(event)) || !valid_message(event) ||
            event.opcode == HDMI_LOS_OP_AGENT_FAILED) {
          if (active_) Release("Xorg agent reported failure", true);
        }
      }
      if (active_ && volumes_.Update(fds[3].revents, fds[4].revents)) {
        Release("both-volume escape or volume input loss", true);
      }
      if (active_ && monotonic_ms() >= deadline_ms_) {
        Release("mandatory 60 second timeout", true);
      }
    }
    Release("broker stopping", true);
    if (agent_fd_ >= 0) close(agent_fd_);
    if (composer_fd_ >= 0) close(composer_fd_);
    if (listen_fd_ >= 0) close(listen_fd_);
    return 0;
  }

 private:
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

  bool ComposerRequest(uint16_t opcode, hdmi_los_message *response, int *lease_fd = nullptr) {
    if (!EnsureComposer()) return false;
    hdmi_los_message request = make_message(opcode);
    if (!send_with_fd(composer_fd_, request, -1) ||
        !recv_with_fd(composer_fd_, response, lease_fd) || !valid_message(*response) ||
        response->request_id != request.request_id) {
      close(composer_fd_);
      composer_fd_ = -1;
      return false;
    }
    return true;
  }

  bool WaitAgent(uint16_t wanted, int timeout_ms, hdmi_los_message *response) {
    int64_t end = monotonic_ms() + timeout_ms;
    while (monotonic_ms() < end) {
      pollfd fds[3] = {
          {agent_fd_, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
          {volumes_.down, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
          {volumes_.up, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
      };
      int64_t remaining_ms = std::min<int64_t>(100, end - monotonic_ms());
      if (deadline_ms_ > 0) {
        remaining_ms = std::min<int64_t>(remaining_ms, deadline_ms_ - monotonic_ms());
      }
      if (remaining_ms <= 0) return false;
      int result = poll(fds, 3, static_cast<int>(remaining_ms));
      if (result < 0 && errno == EINTR) continue;
      if (result < 0 || (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL))) return false;
      if (volumes_.Update(fds[1].revents, fds[2].revents)) return false;
      if (deadline_ms_ > 0 && monotonic_ms() >= deadline_ms_) return false;
      if (fds[0].revents & POLLIN) {
        if (!read_full(agent_fd_, response, sizeof(*response)) || !valid_message(*response)) {
          return false;
        }
        if (response->opcode == wanted || response->opcode == HDMI_LOS_OP_AGENT_FAILED) {
          return response->opcode == wanted && response->status == HDMI_LOS_OK;
        }
      }
    }
    return false;
  }

  int Start(std::string *detail) {
    if (active_) return HDMI_LOS_ERR_BUSY;
    if (!compatible()) {
      *detail = "module compatibility gate is closed";
      return HDMI_LOS_ERR_INCOMPATIBLE;
    }
    if (agent_fd_ < 0) {
      *detail = "start the chroot agent first";
      return HDMI_LOS_ERR_AGENT;
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
    // Count preparation and Xorg verification inside the mandatory window.
    deadline_ms_ = monotonic_ms() + kSessionSeconds * 1000;

    hdmi_los_message request = make_message(HDMI_LOS_OP_AGENT_PREPARE);
    hdmi_los_message response = {};
    if (!write_full(agent_fd_, &request, sizeof(request)) ||
        !WaitAgent(HDMI_LOS_OP_AGENT_READY, kAgentPrepareMs, &response)) {
      *detail = "chroot agent preparation failed";
      hdmi_los_message stop = make_message(HDMI_LOS_OP_AGENT_STOP);
      write_full(agent_fd_, &stop, sizeof(stop));
      CleanupGuards();
      return HDMI_LOS_ERR_AGENT;
    }

    int lease_fd = -1;
    if (!ComposerRequest(HDMI_LOS_OP_ACQUIRE, &response, &lease_fd) ||
        response.status != HDMI_LOS_OK || lease_fd < 0) {
      *detail = response.detail[0] ? response.detail : "composer lease request failed";
      hdmi_los_message stop = make_message(HDMI_LOS_OP_AGENT_STOP);
      write_full(agent_fd_, &stop, sizeof(stop));
      if (lease_fd >= 0) close(lease_fd);
      CleanupGuards();
      return response.status ? response.status : HDMI_LOS_ERR_IO;
    }

    request = make_message(HDMI_LOS_OP_AGENT_START);
    request.connector_id = response.connector_id;
    request.crtc_id = response.crtc_id;
    request.plane_id = response.plane_id;
    if (!send_with_fd(agent_fd_, request, lease_fd)) {
      close(lease_fd);
      ComposerRequest(HDMI_LOS_OP_RELEASE, &response);
      CleanupGuards();
      *detail = "could not deliver lease fd to chroot agent";
      return HDMI_LOS_ERR_AGENT;
    }
    close(lease_fd);
    if (!WaitAgent(HDMI_LOS_OP_AGENT_READY, kAgentStartMs, &response)) {
      hdmi_los_message stop = make_message(HDMI_LOS_OP_AGENT_STOP);
      write_full(agent_fd_, &stop, sizeof(stop));
      ComposerRequest(HDMI_LOS_OP_RELEASE, &response);
      CleanupGuards();
      *detail = "Xorg did not become ready within 15 seconds";
      return HDMI_LOS_ERR_TIMEOUT;
    }

    active_ = true;
    state_detail_ = "Xorg owns external display";
    *detail = state_detail_;
    log_line("info", "Xorg session active; 60 second timer armed");
    return HDMI_LOS_OK;
  }

  void CleanupGuards() {
    volumes_.Release();
    set_wake_lock(false);
    deadline_ms_ = 0;
  }

  void Release(const char *reason, bool tell_composer) {
    if (!active_ && volumes_.down < 0 && volumes_.up < 0) return;
    log_line("warning", reason);
    if (agent_fd_ >= 0) {
      hdmi_los_message stop = make_message(HDMI_LOS_OP_AGENT_STOP);
      hdmi_los_message response = {};
      if (write_full(agent_fd_, &stop, sizeof(stop))) {
        WaitAgent(HDMI_LOS_OP_AGENT_READY, kAgentStopMs, &response);
      }
    }
    if (tell_composer && composer_fd_ >= 0) {
      hdmi_los_message response = {};
      ComposerRequest(HDMI_LOS_OP_RELEASE, &response);
    }
    active_ = false;
    CleanupGuards();
    state_detail_ = reason;
  }

  hdmi_los_message Status(uint32_t request_id) {
    hdmi_los_message status = make_message(HDMI_LOS_OP_STATUS | HDMI_LOS_OP_RESPONSE);
    status.request_id = request_id;
    status.status = compatible() ? HDMI_LOS_OK : HDMI_LOS_ERR_INCOMPATIBLE;
    if (!compatible()) {
      status.state = HDMI_LOS_STATE_UNAVAILABLE;
      snprintf(status.detail, sizeof(status.detail), "incompatible or unverified Lineage build");
    } else if (active_) {
      status.state = HDMI_LOS_STATE_LEASED;
      status.remaining_seconds = static_cast<uint32_t>(
          std::max<int64_t>(0, (deadline_ms_ - monotonic_ms() + 999) / 1000));
      snprintf(status.detail, sizeof(status.detail), "%s", state_detail_.c_str());
    } else if (agent_fd_ >= 0) {
      status.state = HDMI_LOS_STATE_AGENT_READY;
      snprintf(status.detail, sizeof(status.detail), "chroot agent ready; Android owns display");
    } else {
      status.state = HDMI_LOS_STATE_ANDROID;
      snprintf(status.detail, sizeof(status.detail), "Android owns display; chroot agent absent");
    }
    return status;
  }

  void AcceptClient() {
    int client = accept4(listen_fd_, nullptr, nullptr, SOCK_CLOEXEC);
    if (client < 0) return;
    timeval timeout = {1, 0};
    setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
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
      if (active_ || agent_fd_ >= 0) {
        hdmi_los_message busy = Status(request.request_id);
        busy.status = HDMI_LOS_ERR_BUSY;
        write_full(client, &busy, sizeof(busy));
        close(client);
      } else {
        agent_fd_ = client;
        hdmi_los_message ready = Status(request.request_id);
        ready.status = HDMI_LOS_OK;
        ready.state = HDMI_LOS_STATE_AGENT_READY;
        write_full(agent_fd_, &ready, sizeof(ready));
        log_line("info", "chroot agent registered");
      }
      return;
    }

    hdmi_los_message response = Status(request.request_id);
    if (request.opcode == HDMI_LOS_OP_TOGGLE) {
      if (active_) {
        Release("Quick Settings tile requested restore", true);
        response = Status(request.request_id);
      } else {
        std::string detail;
        int start_status = Start(&detail);
        response = Status(request.request_id);
        response.status = start_status;
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
  int64_t deadline_ms_ = 0;
  VolumeGuard volumes_;
  std::string state_detail_ = "Android owns display";
};

int client_main(const char *command) {
  uint16_t opcode = HDMI_LOS_OP_STATUS;
  if (strcmp(command, "toggle") == 0) opcode = HDMI_LOS_OP_TOGGLE;
  else if (strcmp(command, "status") != 0) {
    fprintf(stderr, "usage: hdmi-losd [daemon|status|toggle]\n");
    return 2;
  }
  int fd = connect_abstract(HDMI_LOS_BROKER_SOCKET, SOCK_STREAM);
  if (fd < 0) {
    perror("connect broker");
    return 1;
  }
  hdmi_los_message request = make_message(opcode);
  hdmi_los_message response = {};
  bool ok = write_full(fd, &request, sizeof(request)) && read_full(fd, &response, sizeof(response));
  close(fd);
  if (!ok || !valid_message(response)) return 1;
  printf("state=%u status=%d remaining=%u detail=%s\n", response.state, response.status,
         response.remaining_seconds, response.detail);
  return response.status == HDMI_LOS_OK ? 0 : 1;
}

}  // namespace

int main(int argc, char **argv) {
  if (argc == 2 && strcmp(argv[1], "daemon") != 0) return client_main(argv[1]);
  if (argc > 2) {
    fprintf(stderr, "usage: hdmi-losd [daemon|status|toggle]\n");
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
