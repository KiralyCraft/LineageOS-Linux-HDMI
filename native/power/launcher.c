#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
  (void)argc;
  const char *stock = "/vendor/bin/hw/android.hardware.power-service-qti.hdmi-stock";
  const char *guard = "/vendor/lib64/libhdmi_los_power_guard.so";
  const char *prior = getenv("LD_PRELOAD");
  char preload[4096];
  int length = snprintf(preload, sizeof(preload), "%s%s%s", guard, prior && *prior ? ":" : "", prior ? prior : "");
  if (length < 0 || length >= (int)sizeof(preload) || access(guard, R_OK)) {
    fprintf(stderr, "HDMI_LOS_POWER_LAUNCHER_ABI=1 guard unavailable; using stock policy\n");
  } else if (setenv("LD_PRELOAD", preload, 1)) {
    perror("HDMI power preload");
    return 1;
  }
  argv[0] = "/vendor/bin/hw/android.hardware.power-service-qti";
  execv(stock, argv);
  perror("HDMI stock power service");
  return 1;
}
