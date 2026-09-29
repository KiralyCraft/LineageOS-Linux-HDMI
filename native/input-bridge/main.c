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
#define ROLE_MOUSE 1U
#define ROLE_KEYBOARD 2U

struct physical_device {
  int fd;
  char path[PATH_MAX];
  char name[256];
  unsigned int roles;
  bool pressed[KEY_CNT];
  bool dropping;
  bool pending_mouse;
  bool pending_keyboard;
};

struct virtual_device {
  const char *name;
  int fd;
  char path[PATH_MAX];
  unsigned int holders[KEY_CNT];
  bool hires_wheel;
  bool hires_hwheel;
};

static volatile sig_atomic_t stop_requested;
static struct physical_device **sources;
static size_t source_count;
static size_t source_capacity;
static struct virtual_device virtual_mouse = {.name = "hdmi-los-mouse", .fd = -1};
static struct virtual_device virtual_keyboard = {.name = "hdmi-los-keyboard", .fd = -1};

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

static unsigned int classify_device(unsigned short bus, const char *name,
                                    bool mouse_capable, bool keyboard_capable) {
  if (bus == BUS_BLUETOOTH) {
    if (mouse_capable && strcmp(name, "ASUS MD100 Mouse") == 0) return ROLE_MOUSE;
    if (keyboard_capable && strcmp(name, "BT Keyboard") == 0) return ROLE_KEYBOARD;
    return 0;
  }
  if (bus != BUS_USB) return 0;
  return (mouse_capable ? ROLE_MOUSE : 0) |
         (keyboard_capable ? ROLE_KEYBOARD : 0);
}

static unsigned int physical_roles(int fd, char *name, size_t name_size) {
  struct input_id id = {0};
  if (ioctl(fd, EVIOCGNAME(name_size), name) < 0 || ioctl(fd, EVIOCGID, &id) < 0)
    return 0;
  name[name_size - 1] = '\0';
  if (id.bustype != BUS_USB && id.bustype != BUS_BLUETOOTH) return 0;
  bool mouse_capable = has_capability(fd, EV_REL, REL_X, REL_MAX) &&
                       has_capability(fd, EV_REL, REL_Y, REL_MAX) &&
                       has_capability(fd, EV_KEY, BTN_LEFT, KEY_MAX);
  bool keyboard_capable = has_capability(fd, EV_KEY, KEY_A, KEY_MAX) &&
                          has_capability(fd, EV_KEY, KEY_ENTER, KEY_MAX);
  return classify_device(id.bustype, name, mouse_capable, keyboard_capable);
}

static bool source_exists(const char *path) {
  for (size_t i = 0; i < source_count; ++i)
    if (strcmp(sources[i]->path, path) == 0) return true;
  return false;
}

static void scan_physical(void) {
  glob_t paths = {0};
  if (glob("/dev/input/event*", 0, NULL, &paths) != 0) return;
  for (size_t i = 0; i < paths.gl_pathc; ++i) {
    if (source_exists(paths.gl_pathv[i])) continue;
    int fd = open(paths.gl_pathv[i], O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOCTTY);
    if (fd < 0) continue;
    char name[256] = {0};
    unsigned int roles = physical_roles(fd, name, sizeof(name));
    if (!roles || ioctl(fd, EVIOCGRAB, (void *)1) < 0) {
      close(fd);
      continue;
    }
    struct physical_device *device = calloc(1, sizeof(*device));
    if (!device) {
      ioctl(fd, EVIOCGRAB, (void *)0);
      close(fd);
      continue;
    }
    if (source_count == source_capacity) {
      size_t capacity = source_capacity ? source_capacity * 2 : 4;
      struct physical_device **grown = realloc(sources, capacity * sizeof(*sources));
      if (!grown) {
        free(device);
        ioctl(fd, EVIOCGRAB, (void *)0);
        close(fd);
        continue;
      }
      sources = grown;
      source_capacity = capacity;
    }
    device->fd = fd;
    device->roles = roles;
    snprintf(device->path, sizeof(device->path), "%s", paths.gl_pathv[i]);
    snprintf(device->name, sizeof(device->name), "%s", name);
    sources[source_count++] = device;
    char message[PATH_MAX + 512];
    snprintf(message, sizeof(message), "grabbed %s at %s (roles=0x%x)",
             device->name, device->path, roles);
    log_message("info", message);
  }
  globfree(&paths);
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
#ifdef REL_WHEEL_HI_RES
    device->hires_wheel = ioctl(fd, UI_SET_RELBIT, REL_WHEEL_HI_RES) == 0;
    device->hires_hwheel = ioctl(fd, UI_SET_RELBIT, REL_HWHEEL_HI_RES) == 0;
#endif
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
  if (write(device->fd, event, sizeof(*event)) != (ssize_t)sizeof(*event) && errno != EAGAIN) {
    log_message("warning", "uinput write failed");
  }
}

static void emit_key(struct virtual_device *device, unsigned int code, int value) {
  struct input_event event = {.type = EV_KEY, .code = (unsigned short)code, .value = value};
  emit_event(device, &event);
}

static void emit_sync(struct virtual_device *device) {
  struct input_event event = {.type = EV_SYN, .code = SYN_REPORT};
  emit_event(device, &event);
}

static void release_keys(struct virtual_device *device) {
  bool changed = false;
  for (int code = 0; code < KEY_CNT; ++code) {
    if (!device->holders[code]) continue;
    device->holders[code] = 0;
    emit_key(device, (unsigned int)code, 0);
    changed = true;
  }
  if (changed) emit_sync(device);
}

static void destroy_virtual(struct virtual_device *device) {
  if (device->fd < 0) return;
  release_keys(device);
  ioctl(device->fd, UI_DEV_DESTROY);
  close(device->fd);
  device->fd = -1;
}

static struct virtual_device *key_target(const struct physical_device *source,
                                         unsigned int code) {
  if (code >= KEY_CNT) return NULL;
  if (code >= BTN_MOUSE && code <= BTN_TASK)
    return (source->roles & ROLE_MOUSE) ? &virtual_mouse : NULL;
  return (source->roles & ROLE_KEYBOARD) ? &virtual_keyboard : NULL;
}

static void flush_frame(struct physical_device *source) {
  if (source->pending_mouse) emit_sync(&virtual_mouse);
  if (source->pending_keyboard) emit_sync(&virtual_keyboard);
  source->pending_mouse = false;
  source->pending_keyboard = false;
}

static void change_key(struct physical_device *source, unsigned int code, int value) {
  struct virtual_device *target = key_target(source, code);
  if (!target) return;
  bool *pending = target == &virtual_mouse ? &source->pending_mouse :
                                              &source->pending_keyboard;
  if (value == 2) {
    if (source->pressed[code] && target->holders[code]) {
      emit_key(target, code, 2);
      *pending = true;
    }
  } else if (value == 1 && !source->pressed[code]) {
    source->pressed[code] = true;
    if (target->holders[code]++ == 0) {
      emit_key(target, code, 1);
      *pending = true;
    }
  } else if (value == 0 && source->pressed[code]) {
    source->pressed[code] = false;
    if (--target->holders[code] == 0) {
      emit_key(target, code, 0);
      *pending = true;
    }
  }
}

static void reconcile_key_bits(struct physical_device *source, const unsigned char *bits) {
  for (unsigned int code = 0; code < KEY_CNT; ++code) {
    if (!key_target(source, code)) continue;
    bool down = (bits[code / 8] & (1U << (code % 8))) != 0;
    if (down != source->pressed[code]) change_key(source, code, down ? 1 : 0);
  }
}

static bool reconcile_keys(struct physical_device *source) {
  unsigned char bits[(KEY_CNT + 7) / 8] = {0};
  if (ioctl(source->fd, EVIOCGKEY(sizeof(bits)), bits) < 0) return false;
  reconcile_key_bits(source, bits);
  return true;
}

static bool relative_supported(unsigned int code) {
  if (code == REL_X || code == REL_Y || code == REL_WHEEL || code == REL_HWHEEL)
    return true;
#ifdef REL_WHEEL_HI_RES
  if (code == REL_WHEEL_HI_RES) return virtual_mouse.hires_wheel;
  if (code == REL_HWHEEL_HI_RES) return virtual_mouse.hires_hwheel;
#endif
  return false;
}

static bool forward_events(struct physical_device *source, short poll_events) {
  if (poll_events & (POLLERR | POLLHUP | POLLNVAL)) return false;
  if (!(poll_events & POLLIN)) return true;
  struct input_event events[32];
  ssize_t size;
  while ((size = read(source->fd, events, sizeof(events))) > 0) {
    size_t count = (size_t)size / sizeof(events[0]);
    for (size_t i = 0; i < count; ++i) {
      const struct input_event *event = &events[i];
      if (event->type == EV_SYN && event->code == SYN_DROPPED) {
        source->dropping = true;
        continue;
      }
      if (event->type == EV_SYN && event->code == SYN_REPORT) {
        if (source->dropping) {
          if (!reconcile_keys(source)) return false;
          source->dropping = false;
        }
        flush_frame(source);
        continue;
      }
      if (source->dropping) continue;
      if (event->type == EV_KEY) {
        change_key(source, event->code, event->value);
      } else if (event->type == EV_REL && (source->roles & ROLE_MOUSE) &&
                 relative_supported(event->code)) {
        emit_event(&virtual_mouse, event);
        source->pending_mouse = true;
      } else if (event->type == EV_MSC && event->code == MSC_SCAN) {
        if (source->roles & ROLE_MOUSE) {
          emit_event(&virtual_mouse, event);
          source->pending_mouse = true;
        }
        if (source->roles & ROLE_KEYBOARD) {
          emit_event(&virtual_keyboard, event);
          source->pending_keyboard = true;
        }
      }
    }
  }
  return size < 0 && (errno == EAGAIN || errno == EINTR);
}

static void disconnect_physical(size_t index) {
  struct physical_device *source = sources[index];
  for (unsigned int code = 0; code < KEY_CNT; ++code)
    if (source->pressed[code]) change_key(source, code, 0);
  flush_frame(source);
  ioctl(source->fd, EVIOCGRAB, (void *)0);
  close(source->fd);
  free(source);
  memmove(sources + index, sources + index + 1,
          (source_count - index - 1) * sizeof(*sources));
  --source_count;
}

static long long monotonic_ms(void) {
  struct timespec now;
  clock_gettime(CLOCK_MONOTONIC, &now);
  return (long long)now.tv_sec * 1000 + now.tv_nsec / 1000000;
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

  long long next_scan = 0;
  while (!stop_requested) {
    long long now = monotonic_ms();
    if (now >= next_scan) {
      scan_physical();
      next_scan = now + SCAN_MS;
    }
    struct pollfd *fds = calloc(source_count ? source_count : 1, sizeof(*fds));
    if (!fds) {
      log_message("error", "cannot allocate input poll set");
      break;
    }
    for (size_t i = 0; i < source_count; ++i) {
      fds[i].fd = sources[i]->fd;
      fds[i].events = POLLIN | POLLERR | POLLHUP;
    }
    now = monotonic_ms();
    int timeout = now >= next_scan ? 0 : (int)(next_scan - now);
    int result = poll(fds, source_count, timeout);
    if (result < 0 && errno != EINTR) {
      free(fds);
      break;
    }
    if (result > 0) {
      for (size_t i = source_count; i > 0; --i) {
        if (!forward_events(sources[i - 1], fds[i - 1].revents)) {
          char message[PATH_MAX + 128];
          snprintf(message, sizeof(message), "%s asleep or disconnected; releasing its input",
                   sources[i - 1]->name);
          disconnect_physical(i - 1);
          log_message("info", message);
        }
      }
    }
    free(fds);
  }

  while (source_count) disconnect_physical(source_count - 1);
  free(sources);
  sources = NULL;
  source_capacity = 0;
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
