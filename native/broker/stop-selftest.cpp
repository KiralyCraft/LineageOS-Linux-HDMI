// Exercise the real broker wait loop with local sockets; no Android/display.
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <linux/input.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <string>
#include <vector>
#include <assert.h>
#include "../common/hdmi_los_protocol.h"
#include "../common/hdmi_timing_session.h"
// Standard-library headers are loaded first so this only exposes Broker's
// state to the regression fixture, without altering production visibility.
#define private public
#define main broker_program_main
#include "main.cpp"
#undef main
#undef private

static void queue_ready(int fd) {
    auto reply = make_message(HDMI_LOS_OP_AGENT_READY);
    reply.status = HDMI_LOS_OK;
    assert(write_full(fd, &reply, sizeof(reply)));
}

int main() {
    int sockets[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    {
        Broker broker;
        broker.agent_fd_ = sockets[0];
        broker.deadline_ms_ = monotonic_ms() - 1;
        queue_ready(sockets[1]);
        hdmi_los_message reply = {};
        // Startup still respects an expired session deadline. Teardown must
        // receive the already-queued acknowledgement rather than revoke early.
        assert(!broker.WaitAgent(HDMI_LOS_OP_AGENT_READY, 100, &reply));
        assert(broker.WaitAgent(HDMI_LOS_OP_AGENT_READY, 100, &reply, true));
    }
    close(sockets[0]); close(sockets[1]);

    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    {
        Broker broker;
        broker.agent_fd_ = sockets[0];
        broker.deadline_ms_ = monotonic_ms() + 60000;
        broker.volumes_.down_pressed = broker.volumes_.up_pressed = true;
        broker.volumes_.both_since = monotonic_ms() - kChordHoldMs - 1;
        queue_ready(sockets[1]);
        hdmi_los_message reply = {};
        assert(broker.WaitAgent(HDMI_LOS_OP_AGENT_READY, 100, &reply, true));
    }
    close(sockets[0]); close(sockets[1]);

    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    {
        Broker broker;
        broker.agent_fd_ = sockets[0];
        queue_ready(sockets[1]);
        close(sockets[1]);
        hdmi_los_message reply = {};
        // A final readable acknowledgement is valid even alongside POLLHUP.
        assert(broker.WaitAgent(HDMI_LOS_OP_AGENT_READY, 100, &reply, true));
        assert(!broker.WaitAgent(HDMI_LOS_OP_AGENT_READY, 100, &reply, true));
    }
    close(sockets[0]);

    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    {
        Broker broker;
        broker.agent_fd_ = sockets[0];
        hdmi_los_message reply = {};
        assert(!broker.WaitAgent(HDMI_LOS_OP_AGENT_READY, 20, &reply, true));
    }
    close(sockets[0]); close(sockets[1]);
    puts("Broker stop acknowledgement after timeout/escape, final HUP and bounded wait tests: PASS");
}
