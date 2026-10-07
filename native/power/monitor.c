#define _GNU_SOURCE
#include "lease.h"
#include "hdmi_los_protocol.h"
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#ifdef __ANDROID__
#include <sys/system_properties.h>
#endif
static volatile sig_atomic_t stopped;
static void stop_monitor(int value) { (void)value; stopped = 1; }
static bool wait_fd(int fd, short events, uint64_t deadline) {
  uint64_t now = hdmi_power_now_ms();
  if (!now || now >= deadline) return false;
  struct pollfd p = {.fd = fd, .events = events};
  return poll(&p, 1, (int)(deadline - now)) == 1 && (p.revents & events) &&
      !(p.revents & (POLLERR | POLLNVAL)) &&
      (!(p.revents & POLLHUP) || events == POLLIN);
}
static bool transfer(int fd, void *buffer, size_t size, bool writing, uint64_t deadline) {
  char *cursor = buffer;
  while (size) {
    if (!wait_fd(fd, writing ? POLLOUT : POLLIN, deadline)) return false;
    ssize_t count = writing ? send(fd, cursor, size, MSG_NOSIGNAL) : recv(fd, cursor, size, 0);
    if (count < 0 && (errno == EAGAIN || errno == EINTR)) continue;
    if (count <= 0) return false;
    cursor += count; size -= count;
  }
  return true;
}
static bool query_lease(bool *reply_valid) {
  if (reply_valid) *reply_valid = false;
  const char *name = HDMI_LOS_BROKER_SOCKET;
#ifdef HDMI_POWER_TEST
  name = getenv("HDMI_POWER_TEST_SOCKET");
#endif
  if (!name || !*name || strlen(name) >= sizeof(((struct sockaddr_un *)0)->sun_path)-1) return false;
  /* The public root/tile broker endpoint is STREAM. The composer endpoint
   * elsewhere in this stack is SEQPACKET; they are different protocols. */
  int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
  if (fd < 0) return false;
  struct sockaddr_un address = {.sun_family = AF_UNIX};
  memcpy(address.sun_path + 1, name, strlen(name));
  bool active = false;
  uint64_t now = hdmi_power_now_ms();
  if (!now) goto done;
  uint64_t deadline = now + 100;
  int result = connect(fd, (struct sockaddr *)&address,
      offsetof(struct sockaddr_un, sun_path) + 1 + strlen(name));
  if (result && errno != EINPROGRESS) goto done;
  if (result) {
    int error = 0; socklen_t size = sizeof(error);
    if (!wait_fd(fd, POLLOUT, deadline) || getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &size) || error) goto done;
  }
  struct ucred peer; socklen_t length = sizeof(peer);
  if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &length) || peer.uid != 0) goto done;
  struct hdmi_los_message request = {.magic = HDMI_LOS_MAGIC, .version = HDMI_LOS_BROKER_VERSION,
      .opcode = HDMI_LOS_OP_STATUS, .request_id = 1};
  struct hdmi_los_message response;
  if (!transfer(fd, &request, sizeof(request), true, deadline) ||
      !transfer(fd, &response, sizeof(response), false, deadline)) goto done;
  bool valid = response.magic == HDMI_LOS_MAGIC && response.version == HDMI_LOS_BROKER_VERSION &&
      response.opcode == (HDMI_LOS_OP_STATUS | HDMI_LOS_OP_RESPONSE) && response.request_id == request.request_id;
  if (reply_valid) *reply_valid = valid;
  /* STATUS reports active timing, but does not populate lease object IDs.
   * Trust only the root broker's active lease state and connected output. */
  active = valid && response.status == HDMI_LOS_OK && response.state == HDMI_LOS_STATE_LEASED &&
      response.active_width && response.active_height && response.active_refresh_millihz &&
      (response.flags & HDMI_LOS_FLAG_CONNECTED);
done:
  close(fd);
  return active;
}
static bool publish(bool active) {
  char value[32];
  uint64_t now = hdmi_power_now_ms();
  if (!now) active = false;
  snprintf(value, sizeof(value), "%llu", (unsigned long long)(active ? now + HDMI_CPU_LEASE_MS : 0));
#ifdef __ANDROID__
  return __system_property_set(HDMI_CPU_LEASE_PROPERTY, value) == 0;
#else
  const char *path = getenv("HDMI_POWER_TEST_LEASE_FILE");
  if (!path) return false;
  char temp[1024];
  if (snprintf(temp, sizeof(temp), "%s.next", path) >= (int)sizeof(temp)) return false;
  FILE *file = fopen(temp, "w");
  if (!file) return false;
  bool ok = fprintf(file, "%s", value) > 0;
  if (fclose(file)) ok = false;
  return ok && rename(temp, path) == 0;
#endif
}
int main(int argc, char **argv) {
  if (argc == 2 && !strcmp(argv[1], "--check")) {
    bool valid = false;
    bool active = query_lease(&valid);
    printf("HDMI_LOS_CPU_MONITOR_ABI=1 broker_reply_valid=%d active_lease=%d\n", valid, active);
    return valid ? 0 : 1;
  }
  if (argc != 1) return 2;
  signal(SIGTERM, stop_monitor); signal(SIGINT, stop_monitor);
  if (!publish(false)) return 1;
  int previous = -1;
  while (!stopped) {
#ifdef __ANDROID__
    if (!access("/data/adb/modules/hdmi-los-power/disable", F_OK) ||
        !access("/data/adb/modules/hdmi-los-power/remove", F_OK)) break;
#endif
    bool active = query_lease(NULL);
    if (!publish(active)) { fprintf(stderr, "CPU lease publication failed\n"); break; }
    if (previous != active) {
      fprintf(stderr, "HDMI_LOS_CPU_MONITOR_ABI=1 active_lease=%d expiry_ms=%d\n", active, HDMI_CPU_LEASE_MS);
      previous = active;
    }
    struct timespec delay = {.tv_nsec = 200000000};
    nanosleep(&delay, NULL);
  }
  return publish(false) ? 0 : 1;
}
