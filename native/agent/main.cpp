#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <limits.h>
#include <poll.h>
#include <pwd.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/ioctl.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <drm/drm.h>
#include <drm/drm_mode.h>

#include <algorithm>
#include <atomic>
#include <string>
#include <vector>

#include "hdmi_los_protocol.h"
#include "hdmi_los_trace.h"

namespace {

constexpr const char *kRuntime = "/run/hdmi-los";
constexpr const char *kDisplay = ":1";
// The kernel UAPI stores drmModeConnection's DRM_MODE_CONNECTED value here
// but intentionally does not publish libdrm's userspace enum.
constexpr uint32_t kDrmModeConnected = 1;
std::atomic<bool> g_stop(false);

pid_t g_bridge = -1;
pid_t g_xorg = -1;
pid_t g_session = -1;
int g_broker = -1;
int g_trace = -1;
int g_verify_lease = -1;
uint32_t g_probe_mode = HDMI_LOS_PROBE_NONE;
std::string g_bundle;
bool g_kgsl_glamor = false;
bool g_kgsl_kms_bridge = false;
bool g_overlay_cursor = false;
enum class ClientPresentMode { kBridge, kShadow, kDirect };
ClientPresentMode g_client_present = ClientPresentMode::kBridge;
bool g_start_lxde = true;
bool g_no_timeout = false;
std::string g_mouse;
std::string g_keyboard;
drm_mode_modeinfo g_android_mode = {};
bool g_android_mode_valid = false;
uint32_t g_expected_connector = 0;
uint32_t g_expected_crtc = 0;
uint32_t g_scanout_sequence = 0;
uint32_t g_scanout_fb = 0;
bool g_scanout_connector_seen = false;
bool g_scanout_confirmed = false;
bool g_scanout_failed = false;
std::string g_scanout_failure;

bool relay_trace_record(int timeout_ms);

int64_t monotonic_ms() {
  timespec now = {};
  clock_gettime(CLOCK_BOOTTIME, &now);
  return static_cast<int64_t>(now.tv_sec) * 1000 + now.tv_nsec / 1000000;
}

void log_message(const char *level, const std::string &message) {
  timespec now = {};
  clock_gettime(CLOCK_REALTIME, &now);
  fprintf(stderr, "[%lld.%03ld] hdmi-los-agent %s: %s\n",
          static_cast<long long>(now.tv_sec), now.tv_nsec / 1000000,
          level, message.c_str());
  fflush(stderr);
}

void on_signal(int) {
  g_stop = true;
}

socklen_t abstract_address(sockaddr_un *address, const char *name) {
  memset(address, 0, sizeof(*address));
  address->sun_family = AF_UNIX;
  size_t length = std::min(strlen(name), sizeof(address->sun_path) - 2);
  memcpy(address->sun_path + 1, name, length);
  return static_cast<socklen_t>(offsetof(sockaddr_un, sun_path) + 1 + length);
}

int connect_broker() {
  int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (fd < 0) return -1;
  sockaddr_un address = {};
  socklen_t length = abstract_address(&address, HDMI_LOS_BROKER_SOCKET);
  if (connect(fd, reinterpret_cast<sockaddr *>(&address), length) < 0) {
    close(fd);
    return -1;
  }
  return fd;
}

hdmi_los_message response_for(const hdmi_los_message &request, uint16_t opcode, int status,
                              const char *detail) {
  hdmi_los_message response = {};
  response.magic = HDMI_LOS_MAGIC;
  response.version = HDMI_LOS_BROKER_VERSION;
  response.opcode = opcode;
  response.request_id = request.request_id;
  response.status = status;
  response.state = status == HDMI_LOS_OK ? HDMI_LOS_STATE_AGENT_READY : HDMI_LOS_STATE_ERROR;
  snprintf(response.detail, sizeof(response.detail), "%s", detail);
  return response;
}

bool write_full(int fd, const void *buffer, size_t size) {
  const char *cursor = static_cast<const char *>(buffer);
  while (size) {
    ssize_t result = send(fd, cursor, size, MSG_NOSIGNAL);
    if (result > 0) {
      cursor += result;
      size -= static_cast<size_t>(result);
    } else if (result < 0 && errno == EINTR) {
      continue;
    } else {
      return false;
    }
  }
  return true;
}

bool recv_message(int fd, hdmi_los_message *message, int *passed_fd) {
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
  *passed_fd = -1;
  for (cmsghdr *cmsg = CMSG_FIRSTHDR(&header); cmsg; cmsg = CMSG_NXTHDR(&header, cmsg)) {
    if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS) {
      memcpy(passed_fd, CMSG_DATA(cmsg), sizeof(*passed_fd));
      break;
    }
  }
  return message->magic == HDMI_LOS_MAGIC &&
         message->version == HDMI_LOS_BROKER_VERSION;
}

bool valid_trace_record(const hdmi_los_trace_record &record) {
  return record.magic == HDMI_LOS_TRACE_MAGIC && record.version == HDMI_LOS_TRACE_VERSION &&
         record.phase >= HDMI_LOS_TRACE_LOADED && record.phase <= HDMI_LOS_TRACE_AFTER;
}

void observe_scanout_record(const hdmi_los_trace_record &record) {
  if (!g_expected_crtc || record.request != DRM_IOCTL_MODE_SETCRTC) return;
  if (record.phase == HDMI_LOS_TRACE_BEFORE && record.argument[0] == g_expected_crtc &&
      record.argument[1] != 0 && record.argument[3] != 0 &&
      (!g_scanout_sequence || g_scanout_confirmed)) {
    g_scanout_sequence = record.sequence;
    g_scanout_fb = static_cast<uint32_t>(record.argument[1]);
    g_scanout_connector_seen = false;
    return;
  }
  if (record.sequence != g_scanout_sequence) return;
  if (record.phase == HDMI_LOS_TRACE_DETAIL &&
      strncmp(record.name, "SETCRTC_CONNECTOR", sizeof(record.name)) == 0 &&
      record.argument[2] == g_expected_connector) {
    g_scanout_connector_seen = true;
  } else if (record.phase == HDMI_LOS_TRACE_AFTER) {
    if (record.result == 0 && g_scanout_connector_seen) {
      g_scanout_confirmed = true;
    } else {
      g_scanout_failed = true;
      g_scanout_failure = record.result == 0 ?
          "Xorg enabled the expected CRTC without the leased connector" :
          "Xorg's first external scanout commit failed errno=" + std::to_string(record.error);
    }
  }
}

bool forward_trace_record(const hdmi_los_trace_record &record) {
  observe_scanout_record(record);
  char name[sizeof(record.name) + 1] = {};
  char detail[sizeof(record.detail) + 1] = {};
  memcpy(name, record.name, sizeof(record.name));
  memcpy(detail, record.detail, sizeof(record.detail));

  hdmi_los_message progress = {};
  progress.magic = HDMI_LOS_MAGIC;
  progress.version = HDMI_LOS_BROKER_VERSION;
  progress.opcode = HDMI_LOS_OP_AGENT_PROGRESS;
  progress.request_id = record.sequence;
  progress.flags = record.sequence;
  if (record.phase == HDMI_LOS_TRACE_AFTER) {
    snprintf(progress.detail, sizeof(progress.detail),
             "drm A #%u r=%lld e=%d %.44s", record.sequence,
             (long long)record.result, record.error, detail);
  } else {
    char phase = record.phase == HDMI_LOS_TRACE_LOADED ? 'L' :
                 record.phase == HDMI_LOS_TRACE_DETAIL ? 'D' : 'B';
    snprintf(progress.detail, sizeof(progress.detail),
             "drm %c #%u %.20s %.48s", phase, record.sequence, name, detail);
  }
  if (!write_full(g_broker, &progress, sizeof(progress))) return false;
  hdmi_los_message acknowledgement = {};
  int passed_fd = -1;
  bool ok = recv_message(g_broker, &acknowledgement, &passed_fd) &&
            acknowledgement.opcode == HDMI_LOS_OP_AGENT_PROGRESS_ACK &&
            acknowledgement.request_id == progress.request_id &&
            acknowledgement.status == HDMI_LOS_OK;
  if (passed_fd >= 0) close(passed_fd);
  return ok;
}

bool acknowledge_trace_record(int fd, uint32_t sequence) {
  hdmi_los_trace_record acknowledgement = {};
  acknowledgement.magic = HDMI_LOS_TRACE_MAGIC;
  acknowledgement.version = HDMI_LOS_TRACE_VERSION;
  acknowledgement.phase = HDMI_LOS_TRACE_ACK;
  acknowledgement.sequence = sequence;
  ssize_t result;
  do {
    result = send(fd, &acknowledgement, sizeof(acknowledgement), MSG_NOSIGNAL);
  } while (result < 0 && errno == EINTR);
  return result == static_cast<ssize_t>(sizeof(acknowledgement));
}

bool relay_trace_record(int timeout_ms) {
  if (g_trace < 0) return true;
  pollfd descriptor = {g_trace, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0};
  int result;
  do {
    result = poll(&descriptor, 1, timeout_ms);
  } while (result < 0 && errno == EINTR);
  if (result == 0) return true;
  if (result < 0) return false;
  // SOCK_SEQPACKET can report POLLIN together with POLLHUP for the final
  // record. Drain and acknowledge readable data before treating HUP as a
  // relay failure.
  if (!(descriptor.revents & POLLIN)) {
    return !(descriptor.revents & (POLLERR | POLLHUP | POLLNVAL));
  }
  hdmi_los_trace_record record = {};
  ssize_t received;
  do {
    received = recv(g_trace, &record, sizeof(record), MSG_WAITALL);
  } while (received < 0 && errno == EINTR);
  return received == static_cast<ssize_t>(sizeof(record)) && valid_trace_record(record) &&
         forward_trace_record(record) && acknowledge_trace_record(g_trace, record.sequence);
}

bool process_alive(pid_t pid) {
  return pid > 1 && kill(pid, 0) == 0;
}

std::string xorg_binary() {
  if (g_kgsl_kms_bridge) return g_bundle + "/libexec/Xorg";
  return "/usr/lib/Xorg";
}

void terminate_group(pid_t *pid) {
  if (!pid || *pid <= 1) return;
  pid_t target = *pid;
  if (process_alive(target)) {
    pid_t group = getpgid(target);
    if (group == target) kill(-target, SIGTERM);
    else kill(target, SIGTERM);
    int64_t end = monotonic_ms() + 2000;
    while (process_alive(target) && monotonic_ms() < end) usleep(50000);
    if (process_alive(target)) {
      if (group == target) kill(-target, SIGKILL);
      else kill(target, SIGKILL);
    }
  }
  while (waitpid(target, nullptr, 0) < 0 && errno == EINTR) {}
  *pid = -1;
}

int run_wait(const std::vector<std::string> &arguments, uid_t uid = 0, gid_t gid = 0,
             const char *output_path = nullptr, int timeout_ms = 5000) {
  pid_t child = fork();
  if (child < 0) return -1;
  if (child == 0) {
    if (output_path) {
      int output = open(output_path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
      if (output >= 0) {
        dup2(output, STDOUT_FILENO);
        dup2(output, STDERR_FILENO);
        close(output);
      }
    }
    if (uid != 0) {
      passwd *user = getpwuid(uid);
      if (!user || initgroups(user->pw_name, gid) != 0 || setgid(gid) != 0 ||
          setuid(uid) != 0) {
        _exit(126);
      }
    }
    std::vector<char *> argv;
    for (const auto &argument : arguments) argv.push_back(const_cast<char *>(argument.c_str()));
    argv.push_back(nullptr);
    execv(argv[0], argv.data());
    _exit(127);
  }
  int status = 0;
  int64_t end = monotonic_ms() + timeout_ms;
  for (;;) {
    pid_t waited = waitpid(child, &status, WNOHANG);
    if (waited == child) break;
    if (waited < 0 && errno == EINTR) continue;
    if (waited < 0) return -1;
    if (monotonic_ms() >= end) {
      kill(child, SIGKILL);
      while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
      return -1;
    }
    if (g_trace >= 0) {
      if (!relay_trace_record(50)) {
        kill(child, SIGKILL);
        while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
        return -1;
      }
    } else {
      usleep(50000);
    }
  }
  return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

std::string random_cookie() {
  unsigned char bytes[16] = {};
  int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
  if (fd < 0 || read(fd, bytes, sizeof(bytes)) != static_cast<ssize_t>(sizeof(bytes))) {
    if (fd >= 0) close(fd);
    return {};
  }
  close(fd);
  char output[33] = {};
  for (size_t i = 0; i < sizeof(bytes); ++i) snprintf(output + 2 * i, 3, "%02x", bytes[i]);
  return output;
}

bool trace_preflight() {
  std::string library = g_bundle + "/lib/libhdmi-los-drmtrace.so";
  std::string xorg_path = xorg_binary();
  std::string glamor_module = g_bundle + "/lib/xorg/modules/libglamoregl.so";
  std::string modesetting_module =
      g_bundle + "/lib/xorg/modules/drivers/modesetting_drv.so";
  std::string kgsl_dri = g_bundle + "/lib/mesa/kgsl_dri.so";
  std::string gbm_library = g_bundle + "/lib/mesa/libgbm.so.1";
  std::string gbm_backend = g_bundle + "/lib/mesa/gbm/dri_gbm.so";
  struct stat xorg = {};
  if (stat(xorg_path.c_str(), &xorg) != 0 || !S_ISREG(xorg.st_mode) ||
      access(xorg_path.c_str(), X_OK) != 0 ||
      (xorg.st_mode & (S_ISUID | S_ISGID)) != 0 || access(library.c_str(), R_OK) != 0) {
    log_message("error", "Xorg or DRM tracer failed the secure preload gate");
    return false;
  }
  if (g_kgsl_kms_bridge &&
      (access(glamor_module.c_str(), R_OK) != 0 ||
       access(modesetting_module.c_str(), R_OK) != 0 ||
       access(kgsl_dri.c_str(), R_OK) != 0 ||
       access(gbm_library.c_str(), R_OK) != 0 ||
       access(gbm_backend.c_str(), R_OK) != 0)) {
    log_message("error", "a matched private Xorg or Mesa DRI module is missing");
    return false;
  }

  int sockets[2] = {-1, -1};
  if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) != 0) return false;
  pid_t child = fork();
  if (child == 0) {
    close(sockets[0]);
    int flags = fcntl(sockets[1], F_GETFD);
    if (flags < 0 || fcntl(sockets[1], F_SETFD, flags & ~FD_CLOEXEC) != 0) _exit(126);
    char descriptor[32];
    snprintf(descriptor, sizeof(descriptor), "%d", sockets[1]);
    setenv("HDMI_LOS_TRACE_FD", descriptor, 1);
    setenv("LD_PRELOAD", library.c_str(), 1);
    execl(xorg_path.c_str(), xorg_path.c_str(), "-version", static_cast<char *>(nullptr));
    _exit(127);
  }
  close(sockets[1]);
  if (child < 0) {
    close(sockets[0]);
    return false;
  }

  g_trace = sockets[0];
  pollfd descriptor = {g_trace, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0};
  int polled;
  do {
    polled = poll(&descriptor, 1, 2000);
  } while (polled < 0 && errno == EINTR);
  bool loaded = polled > 0 && (descriptor.revents & POLLIN) && relay_trace_record(0);

  int status = 0;
  bool exited = false;
  bool trace_open = true;
  int64_t end = monotonic_ms() + 3000;
  while (monotonic_ms() < end) {
    pid_t waited = waitpid(child, &status, WNOHANG);
    if (waited == child) {
      exited = true;
      break;
    }
    if (waited < 0 && errno != EINTR) break;
    // The peer can close the trace socket just before its clean process exit
    // becomes visible to waitpid(). Stop polling a closed relay, but retain the
    // existing bounded wait for the authoritative child status.
    if (trace_open) {
      trace_open = relay_trace_record(50);
    } else {
      usleep(10000);
    }
  }
  // A clean child exit and the trace socket hangup can become visible in
  // either order. Reap once more after the relay loop before deciding that
  // preflight timed out.
  if (!exited) {
    pid_t waited;
    do {
      waited = waitpid(child, &status, WNOHANG);
    } while (waited < 0 && errno == EINTR);
    exited = waited == child;
  }
  if (!exited) {
    kill(child, SIGKILL);
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
  }
  close(g_trace);
  g_trace = -1;
  if (!loaded || !exited || !WIFEXITED(status) || WEXITSTATUS(status) == 125) {
    log_message("error", "the actual Xorg binary did not complete the tracer preload handshake");
    return false;
  }
  return true;
}

bool read_input_paths(std::string *mouse, std::string *keyboard) {
  std::string path = std::string(kRuntime) + "/input.env";
  FILE *file = fopen(path.c_str(), "re");
  if (!file) return false;
  char line[PATH_MAX + 64];
  while (fgets(line, sizeof(line), file)) {
    char *newline = strchr(line, '\n');
    if (newline) *newline = '\0';
    if (strncmp(line, "MOUSE_EVENT=", 12) == 0) *mouse = line + 12;
    if (strncmp(line, "KEYBOARD_EVENT=", 15) == 0) *keyboard = line + 15;
  }
  fclose(file);
  return mouse->rfind("/dev/input/event", 0) == 0 &&
         keyboard->rfind("/dev/input/event", 0) == 0;
}

bool same_mode_timing(const drm_mode_modeinfo &left, const drm_mode_modeinfo &right) {
  return left.clock == right.clock &&
         left.hdisplay == right.hdisplay && left.hsync_start == right.hsync_start &&
         left.hsync_end == right.hsync_end && left.htotal == right.htotal &&
         left.hskew == right.hskew && left.vdisplay == right.vdisplay &&
         left.vsync_start == right.vsync_start && left.vsync_end == right.vsync_end &&
         left.vtotal == right.vtotal && left.vscan == right.vscan &&
         left.flags == right.flags;
}

bool read_connector_modes(int fd, uint32_t connector_id,
                          drm_mode_get_connector *connector,
                          std::vector<drm_mode_modeinfo> *modes) {
  if (!connector || !modes) return false;
  drm_mode_get_connector query = {};
  query.connector_id = connector_id;
  if (ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &query) != 0) return false;

  for (int attempt = 0; attempt < 3; ++attempt) {
    std::vector<uint32_t> encoders(query.count_encoders);
    std::vector<uint32_t> properties(query.count_props);
    std::vector<uint64_t> property_values(query.count_props);
    modes->resize(query.count_modes);

    drm_mode_get_connector filled = query;
    filled.encoders_ptr = reinterpret_cast<uint64_t>(encoders.data());
    filled.modes_ptr = reinterpret_cast<uint64_t>(modes->data());
    filled.props_ptr = reinterpret_cast<uint64_t>(properties.data());
    filled.prop_values_ptr = reinterpret_cast<uint64_t>(property_values.data());
    if (ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &filled) != 0) return false;
    if (filled.count_encoders <= encoders.size() &&
        filled.count_props <= properties.size() &&
        filled.count_modes <= modes->size()) {
      modes->resize(filled.count_modes);
      *connector = filled;
      return true;
    }
    query = filled;
    query.encoders_ptr = 0;
    query.modes_ptr = 0;
    query.props_ptr = 0;
    query.prop_values_ptr = 0;
  }
  return false;
}

bool read_android_mode(int lease_fd, uint32_t connector_id, uint32_t crtc_id,
                       drm_mode_modeinfo *mode) {
  if (lease_fd < 0 || connector_id == 0 || crtc_id == 0 || !mode) return false;

  drm_mode_get_connector connector = {};
  std::vector<drm_mode_modeinfo> modes;
  if (!read_connector_modes(lease_fd, connector_id, &connector, &modes)) {
    log_message("error", "cannot read the leased external connector");
    return false;
  }
  bool connected = connector.connection == kDrmModeConnected;

  drm_mode_crtc crtc = {};
  crtc.crtc_id = crtc_id;
  if (ioctl(lease_fd, DRM_IOCTL_MODE_GETCRTC, &crtc) != 0) {
    log_message("error", "cannot read Android's leased external CRTC");
    return false;
  }

  bool advertised = false;
  if (crtc.mode_valid) {
    for (const auto &advertised_mode : modes) {
      if (same_mode_timing(crtc.mode, advertised_mode)) {
        advertised = true;
        break;
      }
    }
  }
  if (connected && crtc.mode_valid && crtc.mode.hdisplay > 0 &&
      crtc.mode.vdisplay > 0 && advertised) {
    *mode = crtc.mode;
  }

  if (!connected) log_message("error", "leased external connector is disconnected");
  else if (!mode->hdisplay || !mode->vdisplay) {
    log_message("error", "Android's active external mode is unavailable or no longer advertised");
  }
  return connected && mode->hdisplay > 0 && mode->vdisplay > 0;
}

std::string xorg_mode_flags(uint32_t flags) {
  std::string result;
  if (flags & DRM_MODE_FLAG_PHSYNC) result += " +HSync";
  else if (flags & DRM_MODE_FLAG_NHSYNC) result += " -HSync";
  if (flags & DRM_MODE_FLAG_PVSYNC) result += " +VSync";
  else if (flags & DRM_MODE_FLAG_NVSYNC) result += " -VSync";
  if (flags & DRM_MODE_FLAG_INTERLACE) result += " Interlace";
  if (flags & DRM_MODE_FLAG_DBLSCAN) result += " DoubleScan";
  if (flags & DRM_MODE_FLAG_CSYNC) result += " Composite";
  if (flags & DRM_MODE_FLAG_PCSYNC) result += " +CSync";
  else if (flags & DRM_MODE_FLAG_NCSYNC) result += " -CSync";
  return result;
}

bool write_xorg_config(const std::string &mouse, const std::string &keyboard,
                       uint32_t probe_mode, const drm_mode_modeinfo &mode) {
  constexpr const char *kModeName = "hdmi-los-android-current";
  std::string mode_flags = xorg_mode_flags(mode.flags);
  const char *overlay_cursor_option =
      g_overlay_cursor ? "  Option \"OverlayCursor\" \"true\"\n" : "";
  std::string path = std::string(kRuntime) + "/xorg.conf";
  FILE *file = fopen(path.c_str(), "we");
  if (!file) return false;
  int result = fprintf(file,
      "Section \"ServerFlags\"\n"
      "  Option \"AutoAddDevices\" \"false\"\n"
      "  Option \"AutoEnableDevices\" \"false\"\n"
      "  Option \"DontVTSwitch\" \"true\"\n"
      "  Option \"DontZap\" \"true\"\n"
      "EndSection\n"
      "Section \"InputDevice\"\n"
      "  Identifier \"HDMI Mouse\"\n"
      "  Driver \"evdev\"\n"
      "  Option \"Device\" \"%s\"\n"
      "  Option \"GrabDevice\" \"true\"\n"
      "  Option \"CorePointer\" \"true\"\n"
      "EndSection\n"
      "Section \"InputDevice\"\n"
      "  Identifier \"HDMI Keyboard\"\n"
      "  Driver \"evdev\"\n"
      "  Option \"Device\" \"%s\"\n"
      "  Option \"GrabDevice\" \"true\"\n"
      "  Option \"CoreKeyboard\" \"true\"\n"
      "  Option \"XkbRules\" \"evdev\"\n"
      "  Option \"XkbModel\" \"pc105\"\n"
      "  Option \"XkbLayout\" \"us\"\n"
      "EndSection\n"
      "Section \"Monitor\"\n"
      "  Identifier \"HDMI Monitor\"\n"
      "  Option \"DPMS\" \"false\"\n"
      "  Modeline \"%s\" %.3f %u %u %u %u %u %u %u %u%s\n"
      "  Option \"PreferredMode\" \"%s\"\n"
      "EndSection\n"
      "Section \"Device\"\n"
      "  Identifier \"HDMI Modesetting\"\n"
      "  Driver \"modesetting\"\n"
      "  Option \"kmsdev\" \"/dev/dri/card0\"\n"
      "  Option \"AccelMethod\" \"%s\"\n"
      "  Option \"PageFlip\" \"%s\"\n"
      "  Option \"ShadowFB\" \"%s\"\n"
      "  Option \"Atomic\" \"%s\"\n"
      "  Option \"SWcursor\" \"%s\"\n"
      "%s"
      "  Option \"Monitor-DP-1\" \"HDMI Monitor\"\n"
      "EndSection\n"
      "Section \"Screen\"\n"
      "  Identifier \"HDMI Screen\"\n"
      "  Device \"HDMI Modesetting\"\n"
      "  Monitor \"HDMI Monitor\"\n"
      "  DefaultDepth 24\n"
      "  SubSection \"Display\"\n"
      "    Depth 24\n"
      "    Modes \"%s\"\n"
      "  EndSubSection\n"
      "EndSection\n"
      "Section \"ServerLayout\"\n"
      "  Identifier \"HDMI Layout\"\n"
      "  Screen 0 \"HDMI Screen\"\n"
      "  InputDevice \"HDMI Mouse\" \"CorePointer\"\n"
      "  InputDevice \"HDMI Keyboard\" \"CoreKeyboard\"\n"
      "EndSection\n", mouse.c_str(), keyboard.c_str(), kModeName,
      static_cast<double>(mode.clock) / 1000.0,
      mode.hdisplay, mode.hsync_start, mode.hsync_end, mode.htotal,
      mode.vdisplay, mode.vsync_start, mode.vsync_end, mode.vtotal,
      mode_flags.c_str(), kModeName,
      g_kgsl_glamor ? "glamor" : "none",
      g_kgsl_glamor ? "true" : "false",
      g_kgsl_glamor ? "false" : "true",
      probe_mode == HDMI_LOS_PROBE_XORG_ATOMIC ? "true" : "false",
      g_overlay_cursor ? "false" : "true",
      overlay_cursor_option, kModeName);
  bool ok = result > 0;
  if (fflush(file) != 0 || fsync(fileno(file)) != 0) ok = false;
  if (fclose(file) != 0) ok = false;
  if (chmod(path.c_str(), 0600) != 0) ok = false;
  return ok;
}

void configure_gpu_environment(bool kms_scanout_server) {
  if (!g_kgsl_glamor) return;
  unsetenv("GALLIUM_DRIVER");
  unsetenv("VK_DRIVER_FILES");
  unsetenv("FD_KGSL_USE_KMS_DUMB");
  unsetenv("FD_KGSL_KMS_DEVICE");
  unsetenv("FD_KGSL_RENDERONLY");
  unsetenv("MESA_KGSL_X11_SHM_BRIDGE");
  unsetenv("MESA_KGSL_X11_GPU_BRIDGE");
  unsetenv("MESA_KGSL_X11_SHADOW");
  unsetenv("FD_MESA_DEBUG");
  setenv("MESA_LOADER_DRIVER_OVERRIDE", "kgsl", 1);
  setenv("FD_FORCE_KGSL", "1", 1);
  setenv("FD_KGSL_ENABLE_DMABUF", "1", 1);
  if (g_kgsl_kms_bridge) {
    std::string mesa = g_bundle + "/lib/mesa";
    std::string gbm_backends = mesa + "/gbm";
    setenv("LD_LIBRARY_PATH", mesa.c_str(), 1);
    setenv("LIBGL_DRIVERS_PATH", mesa.c_str(), 1);
    setenv("GBM_BACKENDS_PATH", gbm_backends.c_str(), 1);
    if (kms_scanout_server) {
      // Xorg owns the leased display.  Its renderonly screen allocates exact
      // KMS scanout resources and imports them into KGSL for glamor.
      setenv("FD_KGSL_RENDERONLY", "1", 1);
    } else if (g_client_present == ClientPresentMode::kBridge) {
      // This downstream KGSL stack cannot consume an ordinary client's
      // dma-buf from Xorg's separate GPU context.  Keep fast, tiled native
      // KGSL rendering and present completed frames through the explicit,
      // event-driven bridge into Xorg-owned pixmaps.
      setenv("MESA_KGSL_X11_SHM_BRIDGE", "1", 1);
      setenv("MESA_KGSL_X11_GPU_BRIDGE", "1", 1);
      // CPU readback is used below the adaptive GPU-bridge threshold.  UBWC
      // must be disabled for that mapping; ordinary Freedreno tiling remains.
      setenv("FD_MESA_DEBUG", "noubwc", 1);
    } else {
      // Direct and shadow clients receive Xorg's leased DRM render node and
      // use KGSL only for submission.  Renderonly owns the KMS-compatible
      // presentation allocation; shadow mode keeps application rendering in
      // a separate tiled/UBWC image and resolves it before exporting a fence.
      setenv("FD_KGSL_RENDERONLY", "1", 1);
      if (g_client_present == ClientPresentMode::kShadow)
        setenv("MESA_KGSL_X11_SHADOW", "1", 1);
    }
  }
}

bool prepare_session(uint32_t probe_mode) {
  if (probe_mode != HDMI_LOS_PROBE_XORG_LEGACY &&
      probe_mode != HDMI_LOS_PROBE_XORG_ATOMIC) return false;
  terminate_group(&g_session);
  terminate_group(&g_xorg);
  terminate_group(&g_bridge);
  g_mouse.clear();
  g_keyboard.clear();
  g_android_mode = {};
  g_android_mode_valid = false;
  g_expected_connector = 0;
  g_expected_crtc = 0;
  g_scanout_sequence = 0;
  g_scanout_fb = 0;
  g_scanout_connector_seen = false;
  g_scanout_confirmed = false;
  g_scanout_failed = false;
  g_scanout_failure.clear();
  passwd *user = getpwnam("kiraly");
  if (!user) return false;
  if ((mkdir(kRuntime, 0710) < 0 && errno != EEXIST) ||
      chown(kRuntime, 0, user->pw_gid) != 0 || chmod(kRuntime, 0710) != 0) {
    return false;
  }
  unlink((std::string(kRuntime) + "/input.env").c_str());
  if (!trace_preflight()) return false;

  std::string bridge = g_bundle + "/bin/hdmi-input-bridge";
  g_bridge = fork();
  if (g_bridge == 0) {
    setpgid(0, 0);
    execl(bridge.c_str(), bridge.c_str(), "--runtime", kRuntime, static_cast<char *>(nullptr));
    _exit(127);
  }
  if (g_bridge < 0) return false;
  setpgid(g_bridge, g_bridge);

  int64_t end = monotonic_ms() + 4500;
  while (monotonic_ms() < end && process_alive(g_bridge)) {
    if (read_input_paths(&g_mouse, &g_keyboard)) break;
    usleep(100000);
  }
  if (g_mouse.empty() || g_keyboard.empty()) return false;

  FILE *lock = fopen("/tmp/.X1-lock", "re");
  if (lock) {
    long owner = -1;
    if (fscanf(lock, "%ld", &owner) == 1 && owner > 1 && kill(static_cast<pid_t>(owner), 0) == 0) {
      fclose(lock);
      log_message("error", "X display :1 is already owned by a live process");
      return false;
    }
    fclose(lock);
    unlink("/tmp/.X1-lock");
    unlink("/tmp/.X11-unix/X1");
  } else if (access("/tmp/.X11-unix/X1", F_OK) == 0) {
    log_message("error", "X display :1 socket exists without a verifiable lock");
    return false;
  }
  std::string auth = std::string(kRuntime) + "/Xauthority";
  int auth_fd = open(auth.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  if (auth_fd < 0) return false;
  close(auth_fd);
  std::string cookie = random_cookie();
  if (cookie.empty() || run_wait({"/usr/bin/xauth", "-f", auth, "add", kDisplay,
                                  "MIT-MAGIC-COOKIE-1", cookie}) != 0) return false;

  if (chown(auth.c_str(), user->pw_uid, user->pw_gid) != 0) return false;
  std::string user_runtime = std::string(kRuntime) + "/user-runtime";
  if ((mkdir(user_runtime.c_str(), 0700) < 0 && errno != EEXIST) ||
      chmod(user_runtime.c_str(), 0700) != 0 ||
      chown(user_runtime.c_str(), user->pw_uid, user->pw_gid) != 0) {
    return false;
  }
  return true;
}

pid_t spawn_xorg(int lease_fd) {
  int sockets[2] = {-1, -1};
  if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) != 0) return -1;
  pid_t child = fork();
  if (child != 0) {
    close(sockets[1]);
    if (child > 0) {
      setpgid(child, child);
      g_trace = sockets[0];
    } else {
      close(sockets[0]);
    }
    return child;
  }
  close(sockets[0]);
  setpgid(0, 0);
  int flags = fcntl(lease_fd, F_GETFD);
  if (flags < 0 || fcntl(lease_fd, F_SETFD, flags & ~FD_CLOEXEC) < 0) _exit(126);
  flags = fcntl(sockets[1], F_GETFD);
  if (flags < 0 || fcntl(sockets[1], F_SETFD, flags & ~FD_CLOEXEC) < 0) _exit(126);
  char fd_text[32];
  char trace_fd_text[32];
  char connector_text[32];
  char crtc_text[32];
  snprintf(fd_text, sizeof(fd_text), "%d", lease_fd);
  snprintf(trace_fd_text, sizeof(trace_fd_text), "%d", sockets[1]);
  snprintf(connector_text, sizeof(connector_text), "%u", g_expected_connector);
  snprintf(crtc_text, sizeof(crtc_text), "%u", g_expected_crtc);
  std::string config = std::string(kRuntime) + "/xorg.conf";
  std::string config_dir = std::string(kRuntime) + "/xorg.conf.d";
  std::string auth = std::string(kRuntime) + "/Xauthority";
  std::string log = std::string(kRuntime) + "/Xorg.1.log";
  std::string tracer = g_bundle + "/lib/libhdmi-los-drmtrace.so";
  std::string xorg_path = xorg_binary();
  std::string module_path = g_bundle + "/lib/xorg/modules,/usr/lib/xorg/modules";
  mkdir(config_dir.c_str(), 0700);
  setenv("HDMI_LOS_TRACE_FD", trace_fd_text, 1);
  setenv("HDMI_LOS_SUPPRESS_CONNECTOR_PROPERTY_NOOPS", "1", 1);
  setenv("HDMI_LOS_IGNORE_XORG_BITMASK_PROPERTIES", "1", 1);
  setenv("HDMI_LOS_IGNORE_XORG_POINTER_PROPERTIES", "1", 1);
  setenv("HDMI_LOS_SAME_MODE_PAGEFLIP_FALLBACK", "1", 1);
  setenv("HDMI_LOS_EXPECTED_CONNECTOR", connector_text, 1);
  setenv("HDMI_LOS_EXPECTED_CRTC", crtc_text, 1);
  setenv("LD_PRELOAD", tracer.c_str(), 1);
  configure_gpu_environment(true);
  execl(xorg_path.c_str(), xorg_path.c_str(), kDisplay, "-masterfd", fd_text,
        "-config", config.c_str(), "-configdir", config_dir.c_str(),
        "-auth", auth.c_str(), "-logfile", log.c_str(), "-nolisten", "tcp",
        "-modulepath", module_path.c_str(),
        "-novtswitch", "-noreset", static_cast<char *>(nullptr));
  _exit(127);
}

pid_t spawn_lxde() {
  passwd *user = getpwnam("kiraly");
  if (!user) return -1;
  pid_t child = fork();
  if (child != 0) {
    if (child > 0) setpgid(child, child);
    return child;
  }
  setpgid(0, 0);
  if (initgroups(user->pw_name, user->pw_gid) != 0 || setgid(user->pw_gid) != 0 ||
      setuid(user->pw_uid) != 0) {
    _exit(126);
  }
  setenv("HOME", user->pw_dir, 1);
  setenv("USER", user->pw_name, 1);
  setenv("LOGNAME", user->pw_name, 1);
  setenv("DISPLAY", kDisplay, 1);
  // Android commonly exports TMPDIR=/data/local/tmp, which is outside the
  // chroot. GTK/gdk-pixbuf otherwise loses icons when its temporary decoder
  // files are directed at that nonexistent path.
  setenv("TMPDIR", "/tmp", 1);
  setenv("XAUTHORITY", (std::string(kRuntime) + "/Xauthority").c_str(), 1);
  setenv("XDG_RUNTIME_DIR", (std::string(kRuntime) + "/user-runtime").c_str(), 1);
  configure_gpu_environment(false);
  execl("/usr/bin/dbus-run-session", "/usr/bin/dbus-run-session", "--",
        "/usr/bin/startlxde", static_cast<char *>(nullptr));
  _exit(127);
}

bool xorg_ready() {
  passwd *user = getpwnam("kiraly");
  if (!user) return false;
  std::string auth = std::string(kRuntime) + "/Xauthority";
  std::string output = std::string(kRuntime) + "/xdpyinfo.txt";
  setenv("DISPLAY", kDisplay, 1);
  setenv("XAUTHORITY", auth.c_str(), 1);
  return run_wait({"/usr/bin/xdpyinfo", "-display", kDisplay}, user->pw_uid, user->pw_gid,
                  output.c_str()) == 0;
}

bool verify_xorg() {
  passwd *user = getpwnam("kiraly");
  if (!user) return false;
  std::string xrandr = std::string(kRuntime) + "/xrandr.txt";
  std::string xinput = std::string(kRuntime) + "/xinput.txt";
  setenv("DISPLAY", kDisplay, 1);
  setenv("XAUTHORITY", (std::string(kRuntime) + "/Xauthority").c_str(), 1);
  if (run_wait({"/usr/bin/xrandr", "--display", kDisplay, "--query"}, user->pw_uid,
               user->pw_gid, xrandr.c_str()) != 0 ||
      run_wait({"/usr/bin/xinput", "list"}, user->pw_uid, user->pw_gid,
               xinput.c_str()) != 0) return false;

  auto contains = [](const std::string &path, const char *wanted, const char *forbidden) {
    FILE *file = fopen(path.c_str(), "re");
    if (!file) return false;
    char line[1024];
    bool found = false;
    bool rejected = false;
    while (fgets(line, sizeof(line), file)) {
      if (strstr(line, wanted)) found = true;
      if (forbidden && strstr(line, forbidden)) rejected = true;
    }
    fclose(file);
    return found && !rejected;
  };
  char current_mode[64] = {};
  snprintf(current_mode, sizeof(current_mode), "current %u x %u",
           g_android_mode.hdisplay, g_android_mode.vdisplay);
  return g_android_mode_valid &&
         contains(xrandr, "DP-1 connected", "DSI-") &&
         contains(xrandr, current_mode, nullptr) &&
         contains(xinput, "HDMI Mouse", nullptr) &&
         contains(xinput, "HDMI Keyboard", nullptr);
}

bool verify_scanout() {
  if (!g_scanout_confirmed || g_scanout_failed || g_verify_lease < 0 ||
      !g_scanout_fb || !g_android_mode_valid) {
    if (g_scanout_failed) log_message("error", g_scanout_failure);
    else log_message("error", "Xorg has not completed a verified external scanout commit");
    return false;
  }
  drm_mode_crtc current = {};
  current.crtc_id = g_expected_crtc;
  if (ioctl(g_verify_lease, DRM_IOCTL_MODE_GETCRTC, &current) != 0) {
    log_message("error", "cannot verify the leased CRTC after Xorg startup");
    return false;
  }
  if (!current.mode_valid || current.fb_id != g_scanout_fb ||
      !same_mode_timing(current.mode, g_android_mode)) {
    log_message("error", "leased CRTC does not scan out Xorg's framebuffer at Android's timing");
    return false;
  }
  log_message("info", "verified Xorg framebuffer " + std::to_string(g_scanout_fb) +
      " on leased CRTC " + std::to_string(g_expected_crtc));
  return true;
}

bool start_xorg(int lease_fd, uint32_t connector_id, uint32_t crtc_id) {
  if (!read_android_mode(lease_fd, connector_id, crtc_id, &g_android_mode) ||
      !write_xorg_config(g_mouse, g_keyboard, g_probe_mode, g_android_mode)) {
    close(lease_fd);
    return false;
  }
  g_android_mode_valid = true;
  g_expected_connector = connector_id;
  g_expected_crtc = crtc_id;
  g_scanout_sequence = 0;
  g_scanout_fb = 0;
  g_scanout_connector_seen = false;
  g_scanout_confirmed = false;
  g_scanout_failed = false;
  g_scanout_failure.clear();
  log_message("info", "inheriting Android external mode " +
      std::to_string(g_android_mode.hdisplay) + "x" +
      std::to_string(g_android_mode.vdisplay) + " (DRM mode " +
      std::string(g_android_mode.name) + ")");
  g_verify_lease = dup(lease_fd);
  if (g_verify_lease < 0) {
    close(lease_fd);
    return false;
  }
  fcntl(g_verify_lease, F_SETFD, FD_CLOEXEC);
  g_xorg = spawn_xorg(lease_fd);
  close(lease_fd);
  if (g_xorg < 0) return false;
  int64_t end = monotonic_ms() + 14000;
  while (monotonic_ms() < end && process_alive(g_xorg)) {
    if (!relay_trace_record(100)) return false;
    if (xorg_ready()) {
      if (g_scanout_failed) return false;
      if (!g_scanout_confirmed) {
        usleep(100000);
        continue;
      }
      if (!verify_scanout() || !verify_xorg()) return false;
      if (!g_start_lxde) return true;
      g_session = spawn_lxde();
      return g_session > 0;
    }
    usleep(200000);
  }
  return false;
}

void cleanup_session() {
  terminate_group(&g_session);
  terminate_group(&g_xorg);
  if (g_trace >= 0) close(g_trace);
  g_trace = -1;
  if (g_verify_lease >= 0) close(g_verify_lease);
  g_verify_lease = -1;
  terminate_group(&g_bridge);
  unlink((std::string(kRuntime) + "/input.env").c_str());
}

std::string executable_dir() {
  char path[PATH_MAX];
  ssize_t size = readlink("/proc/self/exe", path, sizeof(path) - 1);
  if (size < 0) return ".";
  path[size] = '\0';
  char *slash = strrchr(path, '/');
  if (!slash) return ".";
  *slash = '\0';
  slash = strrchr(path, '/');
  if (!slash) return path;
  *slash = '\0';
  return path;
}

int run_agent() {
  g_broker = connect_broker();
  if (g_broker < 0) {
    log_message("error", "cannot connect to Android broker; is the module active?");
    return 1;
  }
  hdmi_los_message registration = {};
  registration.magic = HDMI_LOS_MAGIC;
  registration.version = HDMI_LOS_BROKER_VERSION;
  registration.opcode = HDMI_LOS_OP_AGENT_REGISTER;
  registration.request_id = static_cast<uint32_t>(getpid());
  registration.flags = g_no_timeout ? HDMI_LOS_FLAG_CONTINUOUS : 0;
  if (!write_full(g_broker, &registration, sizeof(registration))) return 1;
  int ignored_fd = -1;
  hdmi_los_message reply = {};
  if (!recv_message(g_broker, &reply, &ignored_fd) || reply.status != HDMI_LOS_OK) {
    log_message("error", "broker rejected agent registration");
    return 1;
  }
  if (g_no_timeout && !(reply.flags & HDMI_LOS_FLAG_CONTINUOUS)) {
    log_message("error", "installed broker does not support continuous sessions");
    return 1;
  }
  log_message("info", g_no_timeout ?
      "registered for continuous sessions; composer watchdog renewal required" :
      "registered for bounded sessions; waiting for a takeover request");

  while (!g_stop) {
    pollfd fds[2] = {
        {g_broker, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
        {g_trace, static_cast<short>(POLLIN | POLLERR | POLLHUP), 0},
    };
    int result = poll(fds, 2, 250);
    if (result < 0 && errno == EINTR) continue;
    if (result < 0 || (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL))) break;

    if (g_xorg > 0) {
      int status = 0;
      pid_t waited = waitpid(g_xorg, &status, WNOHANG);
      if (waited == g_xorg) {
        g_xorg = -1;
        cleanup_session();
        hdmi_los_message failed = response_for(registration, HDMI_LOS_OP_AGENT_FAILED,
                                                HDMI_LOS_ERR_AGENT, "Xorg exited unexpectedly");
        write_full(g_broker, &failed, sizeof(failed));
      }
    }

    if (g_trace >= 0 && (fds[1].revents & (POLLIN | POLLERR | POLLHUP | POLLNVAL)) &&
        !relay_trace_record(0)) {
      cleanup_session();
      hdmi_los_message failed = response_for(registration, HDMI_LOS_OP_AGENT_FAILED,
                                              HDMI_LOS_ERR_AGENT,
                                              "DRM tracer relay failed closed");
      write_full(g_broker, &failed, sizeof(failed));
    }
    if (g_xorg > 0 && g_scanout_failed) {
      std::string reason = g_scanout_failure.empty() ?
          "a later external scanout commit failed" : g_scanout_failure;
      cleanup_session();
      hdmi_los_message failed = response_for(registration, HDMI_LOS_OP_AGENT_FAILED,
                                              HDMI_LOS_ERR_AGENT, reason.c_str());
      write_full(g_broker, &failed, sizeof(failed));
    }

    if (!(fds[0].revents & POLLIN)) continue;
    hdmi_los_message request = {};
    int lease_fd = -1;
    if (!recv_message(g_broker, &request, &lease_fd)) break;
    if (request.opcode == HDMI_LOS_OP_AGENT_PREPARE) {
      bool ok = prepare_session(request.flags);
      if (!ok) cleanup_session();
      if (ok) g_probe_mode = request.flags;
      else g_probe_mode = HDMI_LOS_PROBE_NONE;
      reply = response_for(request, HDMI_LOS_OP_AGENT_READY,
                           ok ? HDMI_LOS_OK : HDMI_LOS_ERR_AGENT,
                           ok ? "input bridge and Xorg runtime prepared" :
                                "input bridge preparation failed");
      write_full(g_broker, &reply, sizeof(reply));
    } else if (request.opcode == HDMI_LOS_OP_AGENT_START) {
      bool ok = false;
      if (lease_fd >= 0 && request.flags == g_probe_mode &&
          (request.flags == HDMI_LOS_PROBE_XORG_LEGACY ||
           request.flags == HDMI_LOS_PROBE_XORG_ATOMIC)) {
        int owned_lease_fd = lease_fd;
        lease_fd = -1;
        ok = start_xorg(owned_lease_fd, request.connector_id, request.crtc_id);
      }
      if (!ok) {
        cleanup_session();
      }
      const char *failure_detail = g_scanout_failure.empty() ?
          "Xorg startup verification failed" : g_scanout_failure.c_str();
      reply = response_for(request, ok ? HDMI_LOS_OP_AGENT_READY : HDMI_LOS_OP_AGENT_FAILED,
                           ok ? HDMI_LOS_OK : HDMI_LOS_ERR_AGENT,
                           ok ? (g_start_lxde ? "Xorg and LXDE ready on DP-1" :
                                                   "Xorg ready on DP-1; session disabled") :
                                failure_detail);
      write_full(g_broker, &reply, sizeof(reply));
    } else if (request.opcode == HDMI_LOS_OP_AGENT_STOP) {
      if (lease_fd >= 0) close(lease_fd);
      cleanup_session();
      g_probe_mode = HDMI_LOS_PROBE_NONE;
      reply = response_for(request, HDMI_LOS_OP_AGENT_READY, HDMI_LOS_OK,
                           "Xorg stopped and input returned to Android");
      write_full(g_broker, &reply, sizeof(reply));
    } else {
      if (lease_fd >= 0) close(lease_fd);
      reply = response_for(request, HDMI_LOS_OP_AGENT_FAILED, HDMI_LOS_ERR_PROTOCOL,
                           "unknown broker request");
      write_full(g_broker, &reply, sizeof(reply));
    }
  }

  cleanup_session();
  close(g_broker);
  g_broker = -1;
  log_message("info", "agent stopped; all input grabs released");
  return 0;
}

}  // namespace

int main(int argc, char **argv) {
  if (geteuid() != 0) {
    fprintf(stderr, "hdmi-los-agent must run as root inside the chroot\n");
    return 1;
  }
  g_bundle = executable_dir();
  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "--bundle") == 0 && i + 1 < argc) {
      g_bundle = argv[++i];
    } else if (strcmp(argv[i], "--xorg-accel") == 0 && i + 1 < argc) {
      const char *value = argv[++i];
      if (strcmp(value, "safe") == 0) {
        g_kgsl_glamor = false;
        g_kgsl_kms_bridge = false;
      } else if (strcmp(value, "kgsl-glamor") == 0) {
        g_kgsl_glamor = true;
        g_kgsl_kms_bridge = false;
      } else if (strcmp(value, "kgsl-kms-bridge") == 0) {
        g_kgsl_glamor = true;
        g_kgsl_kms_bridge = true;
      }
      else {
        fprintf(stderr, "invalid Xorg acceleration mode: %s\n", value);
        return 2;
      }
    } else if (strcmp(argv[i], "--session") == 0 && i + 1 < argc) {
      const char *value = argv[++i];
      if (strcmp(value, "lxde") == 0) g_start_lxde = true;
      else if (strcmp(value, "none") == 0) g_start_lxde = false;
      else {
        fprintf(stderr, "invalid session mode: %s\n", value);
        return 2;
      }
    } else if (strcmp(argv[i], "--client-present") == 0 && i + 1 < argc) {
      const char *value = argv[++i];
      if (strcmp(value, "bridge") == 0)
        g_client_present = ClientPresentMode::kBridge;
      else if (strcmp(value, "shadow") == 0)
        g_client_present = ClientPresentMode::kShadow;
      else if (strcmp(value, "direct") == 0)
        g_client_present = ClientPresentMode::kDirect;
      else {
        fprintf(stderr, "invalid client presentation mode: %s\n", value);
        return 2;
      }
    } else if (strcmp(argv[i], "--no-timeout") == 0) {
      g_no_timeout = true;
    } else if (strcmp(argv[i], "--overlay-cursor") == 0) {
      g_overlay_cursor = true;
    } else {
      fprintf(stderr, "usage: hdmi-los-agent [--bundle DIR] "
                      "[--xorg-accel safe|kgsl-glamor|kgsl-kms-bridge] "
                      "[--client-present bridge|shadow|direct] "
                      "[--session lxde|none] [--overlay-cursor] "
                      "[--no-timeout]\n");
      return 2;
    }
  }
  if (!g_kgsl_kms_bridge && g_client_present != ClientPresentMode::kBridge) {
    fprintf(stderr, "--client-present shadow/direct requires --xorg-accel kgsl-kms-bridge\n");
    return 2;
  }
  if (g_overlay_cursor && !g_kgsl_kms_bridge) {
    fprintf(stderr, "--overlay-cursor requires --xorg-accel kgsl-kms-bridge\n");
    return 2;
  }
  if (g_kgsl_kms_bridge) {
    struct stat mesa = {};
    std::string mesa_path = g_bundle + "/lib/mesa";
    if (stat(mesa_path.c_str(), &mesa) != 0 || !S_ISDIR(mesa.st_mode)) {
      fprintf(stderr, "kgsl-kms-bridge requires a matched private Mesa set in %s\n",
              mesa_path.c_str());
      return 1;
    }
  }
  struct sigaction action = {};
  action.sa_handler = on_signal;
  sigemptyset(&action.sa_mask);
  sigaction(SIGTERM, &action, nullptr);
  sigaction(SIGINT, &action, nullptr);
  sigaction(SIGHUP, &action, nullptr);
  return run_agent();
}
