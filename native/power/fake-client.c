#define _GNU_SOURCE
#include "lease.h"
#include <pthread.h>
#include <stdio.h>
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static int mode = -1, core_calls, calls_on, calls_off, other, rejected;
int perf_hint(int id, const char *data, int duration, int type) {
  (void)data; (void)duration; (void)type;
  pthread_mutex_lock(&lock);
  if ((id == 0x1040 || id == 0x1041) && ((rejected & 2) || duration != 0 || type != -1)) {
    pthread_mutex_unlock(&lock); return -1;
  }
  if (id == 0x1040) { mode = 0; calls_off++; }
  else if (id == 0x1041) { mode = 1; calls_on++; }
  else other = id;
  pthread_mutex_unlock(&lock);
  return 7;
}
/* Any request to force core counts is a regression. Count calls even when
 * the caller ignores failure so the fixture detects accidental renewal. */
int perf_lock_acq(int handle, int duration, int *resources, int count) {
  (void)handle; (void)duration; (void)resources; (void)count;
  pthread_mutex_lock(&lock); core_calls++; pthread_mutex_unlock(&lock);
  return -1;
}
int perf_lock_rel(int handle) {
  (void)handle;
  pthread_mutex_lock(&lock); core_calls++; pthread_mutex_unlock(&lock);
  return -1;
}
void fake_reject(int value) { pthread_mutex_lock(&lock); rejected = value; pthread_mutex_unlock(&lock); }
void fake_state(void) {
  pthread_mutex_lock(&lock);
  printf("{\"mode\":%d,\"core_calls\":%d,\"on_calls\":%d,\"off_calls\":%d,\"other\":%d}\n",
      mode, core_calls, calls_on, calls_off, other);
  fflush(stdout); pthread_mutex_unlock(&lock);
}
