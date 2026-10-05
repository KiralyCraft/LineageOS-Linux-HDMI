#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include <string>
#include <vector>
#include "../../kernel/hdmi_companion/uapi.h"

// Fault injection into the real broker client, without a module or display.
enum Fault { None, MissingDevice, WrongCaps, CreateFailure, EnableFailure,
             StartupStopped, WrongGeneration, NeverReady };
static Fault fault;
static std::vector<unsigned long> operations;
static std::vector<int> closed;
static int64_t now_ms;
static int reads;
static bool invalidated;
static int test_open(const char *path, int flags) {
    assert(!strcmp(path, "/dev/hdmi_companion"));
    assert(flags == (O_RDWR | O_CLOEXEC));
    if (fault == MissingDevice) { errno = ENOENT; return -1; }
    return 10;
}
static int test_close(int fd) { closed.push_back(fd); return 0; }
static int test_clock_gettime(clockid_t clock, timespec *stamp) {
    assert(clock == CLOCK_MONOTONIC);
    stamp->tv_sec = now_ms / 1000;
    stamp->tv_nsec = (now_ms % 1000) * 1000000;
    return 0;
}
static int test_nanosleep(const timespec *delay, timespec *) {
    now_ms += delay->tv_nsec / 1000000;
    return 0;
}
static int test_ioctl(int fd, unsigned long operation, void *argument) {
    operations.push_back(operation);
    if (operation == HDMI_COMPANION_QUERY_CAPS) {
        assert(fd == 10);
        auto *caps = static_cast<hdmi_companion_caps *>(argument);
        assert(caps->size == sizeof(*caps) && !caps->features && !caps->imports);
        caps->features = fault == WrongCaps ? HDMI_COMPANION_FEATURE_PROBE_ONLY : HDMI_COMPANION_FEATURE_TIMING_GUARD;
        caps->imports = HDMI_COMPANION_REQUIRED_IMPORTS;
        strcpy(caps->build_id, "selftest");
        return 0;
    }
    if (operation == HDMI_COMPANION_CREATE_SESSION) {
        assert(fd == 10);
        auto *create = static_cast<hdmi_companion_create *>(argument);
        assert(create->lease_fd == 20 && create->connector_id == 30 &&
               create->crtc_id == 40 && create->plane_id == 50);
        assert(create->session_fd == -1 && !create->generation && !create->flags);
        if (fault == CreateFailure) { errno = EACCES; return -1; }
        create->session_fd = 13;
        create->generation = 7;
        return 0;
    }
    assert(fd == 13);
    if (operation == HDMI_COMPANION_GET_STATUS) {
        auto *status = static_cast<hdmi_companion_status *>(argument);
        assert(status->size == sizeof(*status) && !status->generation && !status->state);
        status->generation = fault == WrongGeneration ? 8 : 7;
        status->state = (fault == StartupStopped || invalidated) ? HDMI_COMPANION_STOPPED :
                        (fault == NeverReady || reads++ == 0) ? HDMI_COMPANION_STARTING : HDMI_COMPANION_TIMING_VALID;
        status->reason = invalidated ? HDMI_COMPANION_REASON_LEASE_REVOKED : HDMI_COMPANION_REASON_START_TIMEOUT;
        return 0;
    }
    auto *control = static_cast<hdmi_companion_control *>(argument);
    assert(control->size == sizeof(*control) && control->abi_version == HDMI_COMPANION_ABI_VERSION && !control->reserved);
    if (operation == HDMI_COMPANION_ENABLE_TIMING && fault == EnableFailure) { errno = EINVAL; return -1; }
    assert(operation == HDMI_COMPANION_ENABLE_TIMING || operation == HDMI_COMPANION_STOP_SESSION);
    return 0;
}

#define open test_open
#define close test_close
#define ioctl test_ioctl
#define clock_gettime test_clock_gettime
#define nanosleep test_nanosleep
#include "../common/hdmi_timing_session.h"
#undef open
#undef close
#undef ioctl
#undef clock_gettime
#undef nanosleep

static int count(unsigned long operation) {
    int result = 0;
    for (auto value : operations) result += value == operation;
    return result;
}
static int close_count(int fd) {
    int result = 0;
    for (auto value : closed) result += value == fd;
    return result;
}
static void reset(Fault next) {
    fault = next; operations.clear(); closed.clear(); reads = 0;
    now_ms = 1000; invalidated = false;
}
int main() {
    for (Fault next : {MissingDevice, WrongCaps, CreateFailure, EnableFailure,
                       StartupStopped, WrongGeneration, NeverReady}) {
        reset(next);
        std::string error;
        { HdmiTimingSession session; assert(!session.Start(20,30,40,50,&error)); assert(!error.empty()); }
        const bool created = next == EnableFailure || next == StartupStopped ||
                             next == WrongGeneration || next == NeverReady;
        assert(close_count(13) == created);
        assert(count(HDMI_COMPANION_STOP_SESSION) == created);
        if (next == NeverReady) assert(now_ms == 1750);
    }
    reset(None);
    std::string error;
    {
        HdmiTimingSession session;
        assert(session.Start(20,30,40,50,&error));
        assert(session.generation() == 7);
        assert(session.Valid(&error));
        invalidated = true;
        assert(!session.Valid(&error));
        session.Stop(); session.Stop();
    }
    assert(count(HDMI_COMPANION_ENABLE_TIMING) == 1);
    assert(count(HDMI_COMPANION_STOP_SESSION) == 1 && close_count(13) == 1);
    reset(None);
    { HdmiTimingSession session; assert(session.Start(20,30,40,50,&error)); }
    assert(count(HDMI_COMPANION_STOP_SESSION) == 1 && close_count(13) == 1);
    reset(None);
    assert(HdmiTimingSession::Available(&error));
    assert(count(HDMI_COMPANION_CREATE_SESSION) == 0 && close_count(10) == 1);
    puts("Timing session capability, startup, invalidation, timeout and cleanup tests: PASS");
}
