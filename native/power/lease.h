#ifndef HDMI_POWER_LEASE_H
#define HDMI_POWER_LEASE_H
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>
#define HDMI_CPU_LEASE_PROPERTY "vendor.hdmi_los.cpu_lease"
#define HDMI_CPU_LEASE_MS 1500
#define HDMI_CPU_FLOOR_MS 1000
static inline uint64_t hdmi_power_now_ms(void) {
  struct timespec t;
  if (clock_gettime(CLOCK_BOOTTIME, &t)) return 0;
  return (uint64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static inline bool hdmi_power_lease_valid(const char *text, uint64_t now) {
  if (!text || !*text || !now) return false;
  for (const char *p = text; *p; p++) if (*p < '0' || *p > '9') return false;
  errno = 0;
  char *end;
  unsigned long long deadline = strtoull(text, &end, 10);
  return !errno && !*end && deadline > now && deadline - now <= HDMI_CPU_LEASE_MS;
}
#endif
