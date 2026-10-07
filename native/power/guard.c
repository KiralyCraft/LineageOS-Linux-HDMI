#define _GNU_SOURCE
#include "lease.h"
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#ifdef __ANDROID__
#include <android/log.h>
#include <sys/system_properties.h>
#endif
#define DISPLAY_OFF 0x1040
#define DISPLAY_ON 0x1041
typedef void *(*lookup_fn)(void *, const char *);
typedef int (*hint_fn)(int, const char *, int, int);
typedef int (*acquire_fn)(int, int, int *, int);
typedef int (*release_fn)(int);
static lookup_fn real_lookup;
static pthread_once_t lookup_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t state_lock = PTHREAD_MUTEX_INITIALIZER;
static hint_fn real_hint;
static acquire_fn acquire;
static release_fn release;
static pthread_t worker;
static atomic_bool stopping;
static bool enabled, worker_started, known, android_on, applied_known, applied_on;
static int floor_handle;
struct hint_args { char *data; int duration, type; bool seen; };
static struct hint_args on_args = {.type = -1}, off_args = {.type = -1};
static uint64_t last_floor_ms;
__attribute__((used)) static const char abi[] = "HDMI_LOS_POWER_GUARD_ABI=1";
static void note(const char *message) {
#ifdef __ANDROID__
  __android_log_print(ANDROID_LOG_INFO, "HdmiCpuGuard", "%s", message);
#else
  fprintf(stderr, "HdmiCpuGuard: %s\n", message);
#endif
}
/* Loader entry points run before sanitizer interceptors are initialized in
 * host fixtures. Keep just the lookup shim uninstrumented; the state machine
 * and property handling remain instrumented. Production builds use no ASan. */
__attribute__((no_sanitize("address", "undefined"))) static void lookup_init(void) {
#ifdef __ANDROID__
  real_lookup = (lookup_fn)dlvsym(RTLD_NEXT, "dlsym", "LIBC");
#else
  real_lookup = (lookup_fn)dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.2.5");
#endif
}
static bool lease_active(void) {
  char value[96] = {0};
#ifdef __ANDROID__
  __system_property_get(HDMI_CPU_LEASE_PROPERTY, value);
#else
  const char *path = getenv("HDMI_POWER_TEST_LEASE_FILE");
  FILE *file = path ? fopen(path, "r") : NULL;
  if (file) { (void)fread(value, 1, sizeof(value)-1, file); fclose(file); }
#endif
  return hdmi_power_lease_valid(value, hdmi_power_now_ms());
}
static void drop_floor(void) {
  if (floor_handle > 0) release(floor_handle);
  floor_handle = 0; last_floor_ms = 0;
}
static void reconcile(bool active) {
  if (!active) drop_floor();
  if (known) {
    bool desired = android_on || active;
    if (!applied_known || desired != applied_on) {
      const struct hint_args *args = desired ? &on_args : &off_args;
      /* Display-state policy can reset core_ctl without clearing the vendor's
       * cached resource vote. Retire our vote before that transition, then
       * create a fresh one afterwards; renewing an old handle cannot reapply
       * a vote that the backend still considers unchanged. */
      drop_floor();
      int result = real_hint(desired ? DISPLAY_ON : DISPLAY_OFF,
          args->seen ? args->data : "", args->duration, args->type);
      applied_known = result >= 0;
      if (applied_known) {
        applied_on = desired;
        note(desired ? "effective CPU policy interactive" : "restored Android noninteractive CPU policy");
      } else note("vendor display-policy request failed; retrying");
    }
  }
  uint64_t now = hdmi_power_now_ms();
  bool want_floor = active && known && applied_known && applied_on;
  if (!want_floor) drop_floor();
  if (want_floor && now && (!last_floor_ms || now - last_floor_ms >= 400)) {
    /* Core availability only. WALT frequencies and thermal ceilings remain
     * under the vendor policy. Screen-off maximums are removed by the hint
     * selection above, never by racing sysfs writes. */
    int resources[] = {0x41000000, 4, 0x41000200, 1};
    int next = acquire(floor_handle, HDMI_CPU_FLOOR_MS, resources, 4);
    if (next > 0) {
      if (floor_handle > 0 && next != floor_handle) release(floor_handle);
      floor_handle = next;
    } else {
      drop_floor();
      note("core request rejected; normal vendor policy retained");
    }
    last_floor_ms = now;
  }
}
static void *watch_lease(void *unused) {
  (void)unused;
  bool last = false;
  while (!atomic_load(&stopping)) {
    pthread_mutex_lock(&state_lock);
    bool active = enabled && lease_active();
    reconcile(active);
    pthread_mutex_unlock(&state_lock);
    if (active != last) { note(active ? "active HDMI CPU lease" : "HDMI CPU lease ended or expired"); last = active; }
    struct timespec delay = {.tv_nsec = 100000000};
    nanosleep(&delay, NULL);
  }
  return NULL;
}
static void shutdown_guard(void) {
  atomic_store(&stopping, true);
  if (worker_started) pthread_join(worker, NULL);
  pthread_mutex_lock(&state_lock);
  if (enabled) reconcile(false);
  enabled = false;
  free(off_args.data); off_args.data = NULL;
  free(on_args.data); on_args.data = NULL;
  pthread_mutex_unlock(&state_lock);
}
static int guarded_hint(int id, const char *data, int duration, int type) {
  if (id != DISPLAY_OFF && id != DISPLAY_ON) return real_hint(id, data, duration, type);
  pthread_mutex_lock(&state_lock);
  known = true; android_on = id == DISPLAY_ON;
  struct hint_args *args = android_on ? &on_args : &off_args;
  char *saved = data ? strdup(data) : NULL;
  if (data && !saved) {
    enabled = false; known = false; drop_floor();
    note("cannot retain Android policy; using stock hints until restart");
    int result = real_hint(id, data, duration, type);
    pthread_mutex_unlock(&state_lock);
    return result;
  }
  free(args->data); args->data = saved;
  args->duration = duration; args->type = type; args->seen = true;
  bool active = enabled && lease_active();
  drop_floor();
  int effective = android_on || active ? DISPLAY_ON : DISPLAY_OFF;
  int result = real_hint(effective, data, duration, type);
  applied_known = result >= 0;
  if (applied_known) applied_on = effective == DISPLAY_ON;
  else drop_floor();
  pthread_mutex_unlock(&state_lock);
  return result;
}
__attribute__((visibility("default"), no_sanitize("address", "undefined")))
void *dlsym(void *handle, const char *name) {
  pthread_once(&lookup_once, lookup_init);
  if (!real_lookup) return NULL;
  /* ASan itself looks up symbols before its strcmp interceptor is ready. */
  if (handle == RTLD_NEXT || handle == RTLD_DEFAULT ||
      name[0] != 'p' || name[1] != 'e' || name[2] != 'r' ||
      name[3] != 'f' || name[4] != '_' || name[5] != 'h' || name[6] != 'i' ||
      name[7] != 'n' || name[8] != 't' || name[9] != '\0') {
    /* Preserve the original caller for namespace and RTLD_NEXT lookup.
     * Production Clang enforces this tail call rather than relying on O2. */
#ifdef __clang__
    __attribute__((musttail))
#endif
    return real_lookup(handle, name);
  }
  void *symbol = real_lookup(handle, name);
  if (!symbol) return NULL;
  Dl_info origin;
  if (!dladdr(symbol, &origin) || !origin.dli_fname) return symbol;
  const char *base = strrchr(origin.dli_fname, '/');
  if (strcmp(base ? base + 1 : origin.dli_fname, "libqti-perfd-client.so")) return symbol;
  pthread_mutex_lock(&state_lock);
  if (!real_hint) {
    real_hint = (hint_fn)symbol;
    acquire = (acquire_fn)real_lookup(handle, "perf_lock_acq");
    release = (release_fn)real_lookup(handle, "perf_lock_rel");
    if (acquire && release && !pthread_create(&worker, NULL, watch_lease, NULL)) {
      worker_started = enabled = true;
      atexit(shutdown_guard);
      note(abi);
    }
  }
  void *result = enabled ? (void *)guarded_hint : symbol;
  pthread_mutex_unlock(&state_lock);
  return result;
}
