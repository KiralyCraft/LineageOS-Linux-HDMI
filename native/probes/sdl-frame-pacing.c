#define _POSIX_C_SOURCE 200809L

#include <SDL2/SDL.h>
#include <SDL2/SDL_opengl.h>

#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

struct frame_sample {
    uint32_t frame;
    int64_t scheduled_ns;
    int64_t draw_ns;
    int64_t swap_begin_ns;
    int64_t swap_end_ns;
};

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
            "usage: %s [--swap-interval -1|0|1] [--seconds N | --frames N]\n"
            "          [--pace-hz N] [--csv PATH] [--window WxH | --fullscreen]\n"
            "\n"
            "Defaults: fullscreen, swap interval 1, 65 seconds, no software "
            "pacing.\n",
            program);
}

static bool
parse_positive_int(const char *value, int *result)
{
    char *end = NULL;
    long parsed;

    errno = 0;
    parsed = strtol(value, &end, 10);
    if (errno || !end || *end != '\0' || parsed <= 0 || parsed > INT32_MAX)
        return false;
    *result = (int)parsed;
    return true;
}

static bool
parse_positive_double(const char *value, double *result)
{
    char *end = NULL;
    double parsed;

    errno = 0;
    parsed = strtod(value, &end);
    if (errno || !end || *end != '\0' || parsed <= 0.0 || parsed > 10000.0)
        return false;
    *result = parsed;
    return true;
}

static bool
parse_window(const char *value, int *width, int *height)
{
    char tail;

    return sscanf(value, "%dx%d%c", width, height, &tail) == 2 &&
           *width > 0 && *height > 0;
}

static int
compare_i64(const void *left, const void *right)
{
    const int64_t a = *(const int64_t *)left;
    const int64_t b = *(const int64_t *)right;

    return (a > b) - (a < b);
}

static double
percentile_ms(const int64_t *sorted, size_t count, double fraction)
{
    size_t index = (size_t)((count - 1) * fraction + 0.5);

    return sorted[index] / 1000000.0;
}

static void
clear_rect(int x, int y, int width, int height, float red, float green,
           float blue)
{
    glScissor(x, y, width, height);
    glClearColor(red, green, blue, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
}

static void
draw_code_band(uint32_t frame, int width, int y, int height)
{
    const uint32_t gray = frame ^ (frame >> 1);
    const unsigned bars = 20;

    for (unsigned bar = 0; bar < bars; ++bar) {
        int x = (int)(bar * (unsigned)width / bars);
        int next = (int)((bar + 1u) * (unsigned)width / bars);
        bool white;

        if (bar < 4)
            white = (bar & 1u) == 0;
        else
            white = (gray & (1u << (bar - 4))) != 0;
        clear_rect(x, y, next - x, height, white ? 1.0f : 0.0f,
                   white ? 1.0f : 0.0f, white ? 1.0f : 0.0f);
    }
}

static void
draw_frame(uint32_t frame, int width, int height)
{
    const int band_height = height / 8;
    const int sweep_width = width / 24 > 1 ? width / 24 : 1;
    const unsigned sweep_phase = frame % 120u;
    const int sweep_x =
        (int)(sweep_phase * (unsigned)(width - sweep_width) / 119u);
    const float pulse = (frame & 1u) ? 0.16f : 0.12f;

    glDisable(GL_DITHER);
    glClearColor(pulse, pulse, 0.22f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);

    glEnable(GL_SCISSOR_TEST);
    draw_code_band(frame, width, 0, band_height);
    draw_code_band(frame, width, height - band_height, band_height);

    /* The sweep advances through 120 exact positions, wrapping every two
     * seconds when frames are displayed at the intended 60 Hz. */
    clear_rect(sweep_x, band_height, sweep_width,
               height - 2 * band_height, 0.95f, 0.95f, 0.15f);

    /* These fixed calibration patches make black/white thresholds recoverable
     * after HDMI range conversion and MJPEG compression. */
    clear_rect(0, band_height, width / 16, height / 12, 0.0f, 0.0f, 0.0f);
    clear_rect(width - width / 16, band_height, width / 16, height / 12,
               1.0f, 1.0f, 1.0f);
    glDisable(GL_SCISSOR_TEST);
}

static bool
append_sample(struct frame_sample **samples, size_t *capacity, size_t count,
              struct frame_sample sample)
{
    if (count == *capacity) {
        size_t new_capacity = *capacity ? *capacity * 2 : 4096;
        void *resized = realloc(*samples, new_capacity * sizeof(**samples));

        if (!resized)
            return false;
        *samples = resized;
        *capacity = new_capacity;
    }
    (*samples)[count] = sample;
    return true;
}

static bool
write_csv(const char *path, const struct frame_sample *samples, size_t count,
          int64_t started_ns)
{
    FILE *output = fopen(path, "w");

    if (!output) {
        fprintf(stderr, "cannot open CSV %s: %s\n", path, strerror(errno));
        return false;
    }
    fputs("frame,scheduled_ns,draw_ns,swap_begin_ns,swap_end_ns,swap_block_ns\n",
          output);
    for (size_t i = 0; i < count; ++i) {
        const struct frame_sample *sample = &samples[i];

        fprintf(output, "%" PRIu32 ",%" PRId64 ",%" PRId64 ",%" PRId64
                ",%" PRId64 ",%" PRId64 "\n",
                sample->frame,
                sample->scheduled_ns ? sample->scheduled_ns - started_ns : 0,
                sample->draw_ns - started_ns,
                sample->swap_begin_ns - started_ns,
                sample->swap_end_ns - started_ns,
                sample->swap_end_ns - sample->swap_begin_ns);
    }
    if (fclose(output) != 0) {
        fprintf(stderr, "cannot close CSV %s: %s\n", path, strerror(errno));
        return false;
    }
    return true;
}

int
main(int argc, char **argv)
{
    int swap_interval = 1;
    int frame_limit = 0;
    double seconds = 65.0;
    double pace_hz = 0.0;
    int width = 960;
    int height = 540;
    bool fullscreen = true;
    const char *csv_path = NULL;

    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--swap-interval") && i + 1 < argc) {
            swap_interval = atoi(argv[++i]);
            if (swap_interval < -1 || swap_interval > 1) {
                usage(argv[0]);
                return 2;
            }
        } else if (!strcmp(argv[i], "--seconds") && i + 1 < argc) {
            if (!parse_positive_double(argv[++i], &seconds)) {
                usage(argv[0]);
                return 2;
            }
            frame_limit = 0;
        } else if (!strcmp(argv[i], "--frames") && i + 1 < argc) {
            if (!parse_positive_int(argv[++i], &frame_limit)) {
                usage(argv[0]);
                return 2;
            }
            seconds = 0.0;
        } else if (!strcmp(argv[i], "--pace-hz") && i + 1 < argc) {
            if (!parse_positive_double(argv[++i], &pace_hz)) {
                usage(argv[0]);
                return 2;
            }
        } else if (!strcmp(argv[i], "--csv") && i + 1 < argc) {
            csv_path = argv[++i];
        } else if (!strcmp(argv[i], "--window") && i + 1 < argc) {
            if (!parse_window(argv[++i], &width, &height)) {
                usage(argv[0]);
                return 2;
            }
            fullscreen = false;
        } else if (!strcmp(argv[i], "--fullscreen")) {
            fullscreen = true;
        } else if (!strcmp(argv[i], "--help")) {
            usage(argv[0]);
            return 0;
        } else {
            usage(argv[0]);
            return 2;
        }
    }

    if (SDL_Init(SDL_INIT_VIDEO) != 0) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);
    SDL_GL_SetAttribute(SDL_GL_RED_SIZE, 8);
    SDL_GL_SetAttribute(SDL_GL_GREEN_SIZE, 8);
    SDL_GL_SetAttribute(SDL_GL_BLUE_SIZE, 8);

    if (fullscreen) {
        SDL_DisplayMode mode;

        if (SDL_GetCurrentDisplayMode(0, &mode) == 0) {
            width = mode.w;
            height = mode.h;
        } else {
            fprintf(stderr, "warning: SDL_GetCurrentDisplayMode: %s\n",
                    SDL_GetError());
        }
    }
    Uint32 flags = SDL_WINDOW_OPENGL | SDL_WINDOW_SHOWN |
                   SDL_WINDOW_ALLOW_HIGHDPI;
    if (fullscreen)
        flags |= SDL_WINDOW_FULLSCREEN_DESKTOP;
    SDL_Window *window = SDL_CreateWindow(
        "HDMI frame pacing probe", SDL_WINDOWPOS_CENTERED,
        SDL_WINDOWPOS_CENTERED, width, height, flags);
    if (!window) {
        fprintf(stderr, "SDL_CreateWindow: %s\n", SDL_GetError());
        SDL_Quit();
        return 1;
    }
    SDL_GLContext context = SDL_GL_CreateContext(window);
    if (!context) {
        fprintf(stderr, "SDL_GL_CreateContext: %s\n", SDL_GetError());
        SDL_DestroyWindow(window);
        SDL_Quit();
        return 1;
    }
    if (SDL_GL_SetSwapInterval(swap_interval) != 0)
        fprintf(stderr, "warning: SDL_GL_SetSwapInterval(%d): %s\n",
                swap_interval, SDL_GetError());
    int effective_interval = SDL_GL_GetSwapInterval();

    SDL_GL_GetDrawableSize(window, &width, &height);
    SDL_ShowCursor(SDL_DISABLE);
    glViewport(0, 0, width, height);
    fprintf(stderr,
            "renderer=%s version=%s drawable=%dx%d requested_interval=%d "
            "effective_interval=%d pace_hz=%.3f mode=%s\n",
            glGetString(GL_RENDERER), glGetString(GL_VERSION), width, height,
            swap_interval, effective_interval, pace_hz,
            fullscreen ? "fullscreen" : "windowed");

    struct frame_sample *samples = NULL;
    size_t capacity = 0;
    size_t count = 0;
    bool quit = false;
    const int64_t started_ns = monotonic_ns();
    const int64_t deadline_ns =
        seconds > 0.0 ? started_ns + (int64_t)(seconds * 1e9) : INT64_MAX;
    const int64_t pace_period_ns =
        pace_hz > 0.0 ? (int64_t)(1e9 / pace_hz + 0.5) : 0;

    while (!quit && (frame_limit == 0 || count < (size_t)frame_limit) &&
           monotonic_ns() < deadline_ns) {
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_QUIT || event.type == SDL_KEYDOWN)
                quit = true;
            if (event.type == SDL_WINDOWEVENT &&
                (event.window.event == SDL_WINDOWEVENT_RESIZED ||
                 event.window.event == SDL_WINDOWEVENT_SIZE_CHANGED)) {
                SDL_GL_GetDrawableSize(window, &width, &height);
                glViewport(0, 0, width, height);
            }
        }
        if (quit)
            break;

        int64_t scheduled_ns = 0;
        if (pace_period_ns) {
            scheduled_ns = started_ns + (int64_t)count * pace_period_ns;
            sleep_until_ns(scheduled_ns);
        }
        int64_t draw_ns = monotonic_ns();
        draw_frame((uint32_t)count, width, height);
        int64_t swap_begin_ns = monotonic_ns();
        SDL_GL_SwapWindow(window);
        int64_t swap_end_ns = monotonic_ns();
        struct frame_sample sample = {
            .frame = (uint32_t)count,
            .scheduled_ns = scheduled_ns,
            .draw_ns = draw_ns,
            .swap_begin_ns = swap_begin_ns,
            .swap_end_ns = swap_end_ns,
        };
        if (!append_sample(&samples, &capacity, count, sample)) {
            fputs("cannot allocate frame samples\n", stderr);
            quit = true;
            break;
        }
        ++count;
    }
    const int64_t elapsed_ns = monotonic_ns() - started_ns;

    if (count > 0) {
        int64_t *swap_durations = malloc(count * sizeof(*swap_durations));
        size_t period_count = count > 1 ? count - 1 : 0;
        int64_t *periods = period_count ? malloc(period_count * sizeof(*periods))
                                        : NULL;
        if (!swap_durations || (period_count && !periods)) {
            fputs("cannot allocate summary samples\n", stderr);
            free(swap_durations);
            free(periods);
            free(samples);
            return 1;
        }
        for (size_t i = 0; i < count; ++i) {
            swap_durations[i] =
                samples[i].swap_end_ns - samples[i].swap_begin_ns;
            if (i > 0)
                periods[i - 1] =
                    samples[i].swap_end_ns - samples[i - 1].swap_end_ns;
        }
        qsort(swap_durations, count, sizeof(*swap_durations), compare_i64);
        if (period_count)
            qsort(periods, period_count, sizeof(*periods), compare_i64);

        printf("frames=%zu drawable=%dx%d requested_interval=%d "
               "effective_interval=%d elapsed=%.3f fps=%.3f "
               "swap_ms_median=%.3f p95=%.3f p99=%.3f max=%.3f",
               count, width, height, swap_interval, effective_interval,
               elapsed_ns / 1e9, count * 1e9 / elapsed_ns,
               percentile_ms(swap_durations, count, 0.50),
               percentile_ms(swap_durations, count, 0.95),
               percentile_ms(swap_durations, count, 0.99),
               swap_durations[count - 1] / 1000000.0);
        if (period_count) {
            printf(" frame_ms_median=%.3f p95=%.3f p99=%.3f max=%.3f",
                   percentile_ms(periods, period_count, 0.50),
                   percentile_ms(periods, period_count, 0.95),
                   percentile_ms(periods, period_count, 0.99),
                   periods[period_count - 1] / 1000000.0);
        }
        putchar('\n');
        free(swap_durations);
        free(periods);
    }

    bool csv_ok = !csv_path || write_csv(csv_path, samples, count, started_ns);
    free(samples);
    SDL_ShowCursor(SDL_ENABLE);
    SDL_GL_DeleteContext(context);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return csv_ok ? 0 : 1;
}
