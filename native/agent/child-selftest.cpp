#include <assert.h>
#include <stdio.h>
#include <sys/prctl.h>
#include "../common/hdmi_child_process.h"

static void quick_child(bool already_exited) {
  pid_t child = fork(); assert(child >= 0);
  if (!child) { if (already_exited) _exit(0); for (;;) pause(); }
  if (already_exited) {
    siginfo_t info = {}; assert(waitid(P_PID, child, &info, WEXITED | WNOWAIT) == 0);
  }
  int64_t start = hdmi_child_now_ms(); hdmi_terminate_group(&child);
  assert(child == -1 && hdmi_child_now_ms() - start < 1000);
}
static void surviving_group() {
  assert(prctl(PR_SET_CHILD_SUBREAPER, 1) == 0);
  int ready[2]; assert(pipe(ready) == 0);
  pid_t leader = fork(); assert(leader >= 0);
  if (!leader) {
    close(ready[0]); assert(setpgid(0, 0) == 0);
    pid_t member = fork(); assert(member >= 0);
    if (!member) {
      signal(SIGTERM, SIG_IGN);
      pid_t self = getpid(); assert(write(ready[1], &self, sizeof(self)) == sizeof(self));
    }
    for (;;) pause();
  }
  close(ready[1]); pid_t member;
  assert(read(ready[0], &member, sizeof(member)) == sizeof(member)); close(ready[0]);
  hdmi_terminate_group(&leader);
  int status = 0; assert(waitpid(member, &status, 0) == member);
  assert(WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL && leader == -1);
}
static void stubborn_child() {
  int ready[2]; assert(pipe(ready) == 0);
  pid_t child = fork(); assert(child >= 0);
  if (!child) { close(ready[0]); signal(SIGTERM, SIG_IGN); assert(write(ready[1], "x", 1) == 1); for (;;) pause(); }
  close(ready[1]); char byte; assert(read(ready[0], &byte, 1) == 1); close(ready[0]);
  int64_t start = hdmi_child_now_ms(); hdmi_terminate_group(&child, 100);
  assert(child == -1 && hdmi_child_now_ms() - start >= 100 && hdmi_child_now_ms() - start < 1000);
}
int main() {
  assert(getuid() != 0); alarm(10);
  quick_child(false); quick_child(true); surviving_group(); stubborn_child();
  puts("PASS: prompt child exit, already-exited leader, surviving process group, bounded TERM-to-KILL escalation");
}
