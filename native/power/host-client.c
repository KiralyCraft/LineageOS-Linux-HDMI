#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv) {
  if (argc != 2) return 2;
  void *handle = dlopen(argv[1], RTLD_NOW);
  if (!handle) { fprintf(stderr, "%s\n", dlerror()); return 1; }
  int (*hint)(int,const char *,int,int) = dlsym(handle, "perf_hint");
  void (*state)(void) = dlsym(handle, "fake_state");
  void (*reject)(int) = dlsym(handle, "fake_reject");
  if (!hint || !state || !reject) return 1;
  char command[32];
  puts("READY"); fflush(stdout);
  while (fgets(command, sizeof(command), stdin)) {
    if (!strcmp(command, "off\n")) hint(0x1040, NULL, 0, -1);
    else if (!strcmp(command, "on\n")) hint(0x1041, NULL, 0, -1);
    else if (!strcmp(command, "other\n")) hint(0x1080, "unrelated", 2, 3);
    else if (!strcmp(command, "reject\n")) reject(1);
    else if (!strcmp(command, "reject-hint\n")) reject(2);
    else if (!strcmp(command, "accept\n")) reject(0);
    else if (!strcmp(command, "state\n")) { state(); continue; }
    else return 2;
    puts("OK"); fflush(stdout);
  }
  return 0;
}
