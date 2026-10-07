#ifndef HDMI_CHILD_PROCESS_H
#define HDMI_CHILD_PROCESS_H

#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

inline int64_t hdmi_child_now_ms() {
  timespec now = {};
  clock_gettime(CLOCK_BOOTTIME, &now);
  return static_cast<int64_t>(now.tv_sec) * 1000 + now.tv_nsec / 1000000;
}

inline void hdmi_terminate_group(pid_t *pid, int grace_ms = 2000) {
  if (!pid || *pid <= 1) return;
  const pid_t target = *pid;
  siginfo_t info = {};
  int result;
  do {
    result = waitid(P_PID, target, &info, WEXITED | WNOHANG | WNOWAIT);
  } while (result < 0 && errno == EINTR);
  // Only signal a child we still own. WNOWAIT pins its PID/PGID until all
  // group signals are sent, including when the leader has already exited.
  if (result < 0) { if (errno == ECHILD) *pid = -1; return; }
  const pid_t group = getpgid(target);
  const pid_t recipient = group == target ? -target : target;
  kill(recipient, SIGTERM);
  const int64_t end = hdmi_child_now_ms() + grace_ms;
  while (!info.si_pid && hdmi_child_now_ms() < end) {
    timespec delay = {0, 10000000};
    nanosleep(&delay, nullptr);
    do {
      result = waitid(P_PID, target, &info, WEXITED | WNOHANG | WNOWAIT);
    } while (result < 0 && errno == EINTR);
    if (result < 0) return;
  }
  // Retire surviving group members even if the leader exited promptly.
  if (group == target || !info.si_pid) kill(recipient, SIGKILL);
  while (waitpid(target, nullptr, 0) < 0 && errno == EINTR) {}
  *pid = -1;
}
#endif
