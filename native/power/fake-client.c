#define _GNU_SOURCE
#include "lease.h"
#include <pthread.h>
#include <stdio.h>
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static int mode = -1, floor, calls_on, calls_off, other, rejected;
static uint64_t expiry;
int perf_hint(int id, const char *data, int duration, int type) {
  (void)data; (void)duration; (void)type;
  pthread_mutex_lock(&lock);
  if ((rejected & 2) && (id == 0x1040 || id == 0x1041)) {
    pthread_mutex_unlock(&lock); return -1;
  }
  if (id == 0x1040) { mode = 0; calls_off++; }
  else if (id == 0x1041) { mode = 1; calls_on++; }
  else other = id;
  pthread_mutex_unlock(&lock);
  return 7;
}
int perf_lock_acq(int handle, int duration, int *resources, int count) {
  (void)handle;
  pthread_mutex_lock(&lock);
  bool valid = !(rejected & 1) && duration == HDMI_CPU_FLOOR_MS && count == 4 &&
      resources[0] == 0x41000000 && resources[1] == 4 &&
      resources[2] == 0x41000200 && resources[3] == 1;
  if (valid) { floor = 1; expiry = hdmi_power_now_ms() + duration; }
  pthread_mutex_unlock(&lock);
  return valid ? 123 : -1;
}
int perf_lock_rel(int handle) {
  pthread_mutex_lock(&lock); if (handle == 123) floor = 0; pthread_mutex_unlock(&lock); return 0;
}
void fake_reject(int value) { pthread_mutex_lock(&lock); rejected = value; pthread_mutex_unlock(&lock); }
void fake_state(void) {
  pthread_mutex_lock(&lock);
  printf("{\"mode\":%d,\"floor\":%d,\"on_calls\":%d,\"off_calls\":%d,\"other\":%d}\n",
      mode, floor && expiry > hdmi_power_now_ms(), calls_on, calls_off, other);
  fflush(stdout); pthread_mutex_unlock(&lock);
}
