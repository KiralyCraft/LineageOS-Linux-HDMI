#define _POSIX_C_SOURCE 200809L

#include <X11/Xlib.h>
#include <X11/extensions/Xfixes.h>
#include <X11/extensions/XTest.h>

#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int64_t
monotonic_ns(void)
{
    struct timespec now;

    clock_gettime(CLOCK_MONOTONIC, &now);
    return (int64_t)now.tv_sec * 1000000000LL + now.tv_nsec;
}

static void
sleep_until_ns(int64_t deadline_ns)
{
    struct timespec deadline = {
        .tv_sec = deadline_ns / 1000000000LL,
        .tv_nsec = deadline_ns % 1000000000LL,
    };

    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline,
                           NULL) == EINTR) {
    }
}

static void
usage(const char *program)
{
    fprintf(stderr,
            "usage: %s [--seconds N] [--hz N] [--box X,Y,WIDTH,HEIGHT]\n"
            "          [--hide-cursor]\n"
            "\n"
            "Moves the X pointer around a deterministic rectangular path.\n"
            "Defaults: 15 seconds, 120 Hz, box 120,120,640,420.\n",
            program);
}

static int
parse_positive_int(const char *value, int *result)
{
    char *end = NULL;
    long parsed;

    errno = 0;
    parsed = strtol(value, &end, 10);
    if (errno || !end || *end != '\0' || parsed <= 0 || parsed > 10000)
        return 0;
    *result = (int)parsed;
    return 1;
}

static int
parse_box(const char *value, int *x, int *y, int *width, int *height)
{
    char tail;

    return sscanf(value, "%d,%d,%d,%d%c", x, y, width, height, &tail) == 4 &&
           *x >= 0 && *y >= 0 && *width > 1 && *height > 1;
}

int
main(int argc, char **argv)
{
    int seconds = 15;
    int hz = 120;
    int box_x = 120;
    int box_y = 120;
    int box_width = 640;
    int box_height = 420;
    int hide_cursor = 0;
    Display *display;
    int event_base;
    int error_base;
    int major;
    int minor;

    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--seconds") && i + 1 < argc) {
            if (!parse_positive_int(argv[++i], &seconds)) {
                usage(argv[0]);
                return 2;
            }
        } else if (!strcmp(argv[i], "--hz") && i + 1 < argc) {
            if (!parse_positive_int(argv[++i], &hz)) {
                usage(argv[0]);
                return 2;
            }
        } else if (!strcmp(argv[i], "--box") && i + 1 < argc) {
            if (!parse_box(argv[++i], &box_x, &box_y, &box_width,
                           &box_height)) {
                usage(argv[0]);
                return 2;
            }
        } else if (!strcmp(argv[i], "--help")) {
            usage(argv[0]);
            return 0;
        } else if (!strcmp(argv[i], "--hide-cursor")) {
            hide_cursor = 1;
        } else {
            usage(argv[0]);
            return 2;
        }
    }

    display = XOpenDisplay(NULL);
    if (!display) {
        fprintf(stderr, "cannot open X display\n");
        return 1;
    }
    if (!XTestQueryExtension(display, &event_base, &error_base, &major,
                             &minor)) {
        fprintf(stderr, "XTEST extension is unavailable\n");
        XCloseDisplay(display);
        return 1;
    }
    if (hide_cursor &&
        !XFixesQueryExtension(display, &event_base, &error_base)) {
        fprintf(stderr, "XFIXES extension is unavailable\n");
        XCloseDisplay(display);
        return 1;
    }

    if (hide_cursor) {
        XFixesHideCursor(display, DefaultRootWindow(display));
        XFlush(display);
    }

    const int64_t period_ns = 1000000000LL / hz;
    const int64_t started_ns = monotonic_ns();
    const int64_t end_ns = started_ns + (int64_t)seconds * 1000000000LL;
    int64_t next_ns = started_ns;
    uint64_t submitted = 0;
    int64_t worst_submit_ns = 0;

    while (next_ns < end_ns) {
        const uint64_t perimeter =
            2u * (uint64_t)(box_width - 1) + 2u * (uint64_t)(box_height - 1);
        uint64_t phase = submitted % perimeter;
        int x;
        int y;

        if (phase < (uint64_t)box_width - 1u) {
            x = box_x + (int)phase;
            y = box_y;
        } else if ((phase -= (uint64_t)box_width - 1u) <
                   (uint64_t)box_height - 1u) {
            x = box_x + box_width - 1;
            y = box_y + (int)phase;
        } else if ((phase -= (uint64_t)box_height - 1u) <
                   (uint64_t)box_width - 1u) {
            x = box_x + box_width - 1 - (int)phase;
            y = box_y + box_height - 1;
        } else {
            phase -= (uint64_t)box_width - 1u;
            x = box_x;
            y = box_y + box_height - 1 - (int)phase;
        }

        int64_t submit_begin_ns = monotonic_ns();
        XTestFakeMotionEvent(display, DefaultScreen(display), x, y,
                             CurrentTime);
        XFlush(display);
        int64_t submit_ns = monotonic_ns() - submit_begin_ns;

        if (submit_ns > worst_submit_ns)
            worst_submit_ns = submit_ns;
        ++submitted;
        next_ns += period_ns;
        sleep_until_ns(next_ns);
    }

    printf("submitted=%" PRIu64 " elapsed_ms=%.3f worst_submit_ms=%.3f\n",
           submitted, (monotonic_ns() - started_ns) / 1000000.0,
           worst_submit_ns / 1000000.0);
    if (hide_cursor) {
        XFixesShowCursor(display, DefaultRootWindow(display));
        XFlush(display);
    }
    XCloseDisplay(display);
    return 0;
}
