/* SPDX-License-Identifier: MIT
 * CPU-only diagnostic ownership regression. Build this file twice: a shared
 * mock with -DPROBE_MOCK, then an executable linked to that mock. Preload the
 * frame probe and verify two event-3 records survive the vfork child's _exit.
 * No X server, GL context or GPU device is used.
 */
#define _GNU_SOURCE
#include <sys/wait.h>
#include <unistd.h>

#ifdef PROBE_MOCK
void glViewport(int x, int y, int width, int height)
{
    (void)x;
    (void)y;
    (void)width;
    (void)height;
}
#else
extern void glViewport(int x, int y, int width, int height);

int main(void)
{
    glViewport(0, 0, 640, 480);
    pid_t child = vfork();
    if (child < 0)
        return 1;
    if (!child)
        _exit(0);
    int status;
    if (waitpid(child, &status, 0) != child ||
        !WIFEXITED(status) || WEXITSTATUS(status))
        return 1;
    glViewport(0, 0, 800, 600);
    return 0;
}
#endif
