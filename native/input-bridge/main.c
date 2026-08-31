#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <limits.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define SCAN_MS 250

struct physical_device {
  const char *name;
  int fd;
  char path[PATH_MAX];
};

struct virtual_device {
  const char *name;
  int fd;
  char path[PATH_MAX];
  bool pressed[KEY_CNT];
};

static volatile sig_atomic_t stop_requested;
static struct physical_device mouse = {"ASUS MD100 Mouse", -1, ""};
static struct physical_device keyboard = {"BT Keyboard", -1, ""};
static struct virtual_device virtual_mouse = {"hdmi-los-mouse", -1, "", {false}};
static struct virtual_device virtual_keyboard = {"hdmi-los-keyboard", -1, "", {false}};

static void on_signal(int signal_number) {
  (void)signal_number;
  stop_requested = 1;
}

static void log_message(const char *level, const char *message) {
  struct timespec now;
  clock_gettime(CLOCK_REALTIME, &now);
  fprintf(stderr, "[%lld.%03ld] hdmi-input-bridge %s: %s\n",
          (long long)now.tv_sec, now.tv_nsec / 1000000, level, message);
  fflush(stderr);
}

static bool has_capability(int fd, unsigned int event_type, unsigned int code,
                           unsigned int maximum) {
  size_t words = (maximum / (sizeof(unsigned long) * 8U)) + 1U;
  unsigned long *bits = calloc(words, sizeof(*bits));
  bool result = false;
  if (bits && ioctl(fd, EVIOCGBIT(event_type, words * sizeof(*bits)), bits) >= 0) {
    result = ((bits[code / (sizeof(unsigned long) * 8U)] >>
               (code % (sizeof(unsigned long) * 8U))) & 1UL) != 0;
  }
  free(bits);
  return result;
}

static bool physical_matches(int fd, const struct physical_device *device) {
  char name[256] = {0};
  struct input_id id;
  if (ioctl(fd, EVIOCGNAME(sizeof(name)), name) < 0 || ioctl(fd, EVIOCGID, &id) < 0 ||
      id.bustype != BUS_BLUETOOTH || strcmp(name, device->name) != 0) {
    return false;
  }
  if (device == &mouse) {
    return has_capability(fd, EV_REL, REL_X, REL_MAX) &&
           has_capability(fd, EV_REL, REL_Y, REL_MAX) &&
           has_capability(fd, EV_KEY, BTN_LEFT, KEY_MAX);
  }
  return has_capability(fd, EV_KEY, KEY_A, KEY_MAX) &&
         has_capability(fd, EV_KEY, KEY_ENTER, KEY_MAX);
}

static void disconnect_physical(struct physical_device *device) {
  if (device->fd >= 0) {
    ioctl(device->fd, EVIOCGRAB, (void *)0);
    close(device->fd);
  }
  device->fd = -1;
  device->path[0] = '\0';
}

static bool connect_physical(struct physical_device *device) {
  glob_t paths = {0};
  if (glob("/dev/input/event*", 0, NULL, &paths) != 0) return false;
  for (size_t i = 0; i < paths.gl_pathc; ++i) {
    int fd = open(paths.gl_pathv[i], O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOCTTY);
    if (fd < 0) continue;
    if (physical_matches(fd, device) && ioctl(fd, EVIOCGRAB, (void *)1) == 0) {
      device->fd = fd;
      snprintf(device->path, sizeof(device->path), "%s", paths.gl_pathv[i]);
      char message[512];
      snprintf(message, sizeof(message), "grabbed %s at %s", device->name, device->path);
      log_message("info", message);
      globfree(&paths);
      return true;
    }
    close(fd);
  }
  globfree(&paths);
  return false;
}

static bool locate_virtual_event(int uinput_fd, char *path, size_t path_size) {
  char sysname[128] = {0};
  if (ioctl(uinput_fd, UI_GET_SYSNAME(sizeof(sysname)), sysname) < 0) return false;
  for (int attempt = 0; attempt < 50; ++attempt) {
    glob_t events = {0};
    if (glob("/sys/class/input/event*", 0, NULL, &events) == 0) {
      for (size_t i = 0; i < events.gl_pathc; ++i) {
        char link[PATH_MAX];
        char resolved[PATH_MAX];
        snprintf(link, sizeof(link), "%s/device", events.gl_pathv[i]);
        if (!realpath(link, resolved)) continue;
        const char *base = strrchr(resolved, '/');
        if (base && strcmp(base + 1, sysname) == 0) {
          snprintf(path, path_size, "/dev/input/%s", strrchr(events.gl_pathv[i], '/') + 1);
          globfree(&events);
          return true;
        }
      }
      globfree(&events);
    }
    struct timespec delay = {0, 50000000L};
    nanosleep(&delay, NULL);
  }
  return false;
}

static int create_virtual(struct virtual_device *device, bool is_mouse) {
  int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC | O_NOCTTY);
  if (fd < 0) return -1;
  if (ioctl(fd, UI_SET_EVBIT, EV_SYN) < 0 || ioctl(fd, UI_SET_EVBIT, EV_KEY) < 0 ||
      ioctl(fd, UI_SET_EVBIT, EV_MSC) < 0 || ioctl(fd, UI_SET_MSCBIT, MSC_SCAN) < 0) {
    close(fd);
    return -1;
  }
  if (is_mouse) {
    if (ioctl(fd, UI_SET_EVBIT, EV_REL) < 0 || ioctl(fd, UI_SET_RELBIT, REL_X) < 0 ||
        ioctl(fd, UI_SET_RELBIT, REL_Y) < 0 || ioctl(fd, UI_SET_RELBIT, REL_WHEEL) < 0 ||
        ioctl(fd, UI_SET_RELBIT, REL_HWHEEL) < 0) {
      close(fd);
      return -1;
    }
    for (int code = BTN_LEFT; code <= BTN_TASK; ++code) ioctl(fd, UI_SET_KEYBIT, code);
  } else {
    for (int code = 1; code < KEY_CNT; ++code) ioctl(fd, UI_SET_KEYBIT, code);
    ioctl(fd, UI_SET_EVBIT, EV_REP);
  }

  struct uinput_setup setup;
  memset(&setup, 0, sizeof(setup));
  snprintf(setup.name, sizeof(setup.name), "%s", device->name);
  setup.id.bustype = BUS_VIRTUAL;
  setup.id.vendor = 0x1d6b;
  setup.id.product = is_mouse ? 0x1001 : 0x1002;
  setup.id.version = 1;
  if (ioctl(fd, UI_DEV_SETUP, &setup) < 0 || ioctl(fd, UI_DEV_CREATE) < 0 ||
      !locate_virtual_event(fd, device->path, sizeof(device->path))) {
    ioctl(fd, UI_DEV_DESTROY);
    close(fd);
    return -1;
  }
  device->fd = fd;
  return 0;
}

static void emit_event(struct virtual_device *device, const struct input_event *event) {
  if (event->type == EV_KEY && event->code < KEY_CNT) {
    if (event->value == 0) device->pressed[event->code] = false;
    if (event->value == 1) device->pressed[event->code] = true;
  }
  if (write(device->fd, event, sizeof(*event)) != (ssize_t)sizeof(*event) && errno != EAGAIN) {
    log_message("warning", "uinput write failed");
  }
}

static void release_keys(struct virtual_device *device) {
  struct input_event event;
  bool changed = false;
  memset(&event, 0, sizeof(event));
  event.type = EV_KEY;
  for (int code = 0; code < KEY_CNT; ++code) {
    if (!device->pressed[code]) continue;
    event.code = (unsigned short)code;
    event.value = 0;
    emit_event(device, &event);
    changed = true;
  }
  if (changed) {
    memset(&event, 0, sizeof(event));
    event.type = EV_SYN;
    event.code = SYN_REPORT;
    emit_event(device, &event);
  }
}

static void destroy_virtual(struct virtual_device *device) {
  if (device->fd < 0) return;
  release_keys(device);
  ioctl(device->fd, UI_DEV_DESTROY);
  close(device->fd);
  device->fd = -1;
}

static bool event_allowed(bool is_mouse, const struct input_event *event) {
  if (event->type == EV_SYN || event->type == EV_MSC) return true;
  if (is_mouse) {
    return event->type == EV_REL ||
           (event->type == EV_KEY && event->code >= BTN_MOUSE && event->code <= BTN_TASK);
  }
  return event->type == EV_KEY;
}

static bool forward_events(struct physical_device *physical, struct virtual_device *virtual,
                           bool is_mouse, short poll_events) {
  if (poll_events & (POLLERR | POLLHUP | POLLNVAL)) return false;
  if (!(poll_events & POLLIN)) return true;
  struct input_event events[32];
  ssize_t size;
  while ((size = read(physical->fd, events, sizeof(events))) > 0) {
    size_t count = (size_t)size / sizeof(events[0]);
    for (size_t i = 0; i < count; ++i) {
      if (event_allowed(is_mouse, &events[i])) emit_event(virtual, &events[i]);
    }
  }
  return size < 0 && (errno == EAGAIN || errno == EINTR);
}

static bool write_ready(const char *runtime) {
  char output[PATH_MAX];
  char temporary[PATH_MAX];
  snprintf(output, sizeof(output), "%s/input.env", runtime);
  snprintf(temporary, sizeof(temporary), "%s/input.env.tmp.%ld", runtime, (long)getpid());
  int fd = open(temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  if (fd < 0) return false;
  bool ok = dprintf(fd, "MOUSE_EVENT=%s\nKEYBOARD_EVENT=%s\n",
                    virtual_mouse.path, virtual_keyboard.path) > 0 &&
            fsync(fd) == 0 && close(fd) == 0 && rename(temporary, output) == 0;
  if (!ok) unlink(temporary);
  return ok;
}

int run(const char *runtime) {
  if (mkdir(runtime, 0700) < 0 && errno != EEXIST) return 1;
  if (create_virtual(&virtual_mouse, true) < 0 ||
      create_virtual(&virtual_keyboard, false) < 0 || !write_ready(runtime)) {
    log_message("error", "could not create stable virtual input devices");
    destroy_virtual(&virtual_keyboard);
    destroy_virtual(&virtual_mouse);
    return 1;
  }
  log_message("info", "stable virtual mouse and keyboard ready");

  while (!stop_requested) {
    if (mouse.fd < 0) connect_physical(&mouse);
    if (keyboard.fd < 0) connect_physical(&keyboard);
    struct pollfd fds[2] = {
        {mouse.fd, (short)(POLLIN | POLLERR | POLLHUP), 0},
        {keyboard.fd, (short)(POLLIN | POLLERR | POLLHUP), 0},
    };
    int result = poll(fds, 2, SCAN_MS);
    if (result < 0 && errno != EINTR) break;
    if (mouse.fd >= 0 && !forward_events(&mouse, &virtual_mouse, true, fds[0].revents)) {
      release_keys(&virtual_mouse);
      disconnect_physical(&mouse);
      log_message("info", "mouse asleep or disconnected; waiting for it to return");
    }
    if (keyboard.fd >= 0 &&
        !forward_events(&keyboard, &virtual_keyboard, false, fds[1].revents)) {
      release_keys(&virtual_keyboard);
      disconnect_physical(&keyboard);
      log_message("info", "keyboard asleep or disconnected; waiting for it to return");
    }
  }

  disconnect_physical(&keyboard);
  disconnect_physical(&mouse);
  destroy_virtual(&virtual_keyboard);
  destroy_virtual(&virtual_mouse);
  return 0;
}

int main(int argc, char **argv) {
  if (argc != 3 || strcmp(argv[1], "--runtime") != 0) {
    fprintf(stderr, "usage: hdmi-input-bridge --runtime DIR\n");
    return 2;
  }
  struct sigaction action;
  memset(&action, 0, sizeof(action));
  action.sa_handler = on_signal;
  sigemptyset(&action.sa_mask);
  sigaction(SIGTERM, &action, NULL);
  sigaction(SIGINT, &action, NULL);
  sigaction(SIGHUP, &action, NULL);
  return run(argv[2]);
}
