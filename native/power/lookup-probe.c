#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
/* Load only the synthetic client in live checks. Never call perf_hint or
 * perf_lock_* on the phone's real performance service. */
int main(int argc, char **argv) {
  if (argc != 2) return 2;
  void *client = dlopen(argv[1], RTLD_NOW);
  if (!client) { fprintf(stderr, "%s\n", dlerror()); return 1; }
  void *hint = dlsym(client, "perf_hint");
  Dl_info origin;
  if (!hint || !dladdr(hint, &origin) || !origin.dli_fname) return 1;
  const char *base = strrchr(origin.dli_fname, '/');
  int intercepted = !strcmp(base ? base + 1 : origin.dli_fname, "libhdmi_los_power_guard.so");
  printf("HDMI_LOS_POWER_LOOKUP_ABI=1 intercepted=%d\n", intercepted);
  return intercepted ? 0 : 1;
}
