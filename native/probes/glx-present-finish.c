#define _POSIX_C_SOURCE 200809L

#include <GL/gl.h>
#include <GL/glx.h>
#include <X11/Xlib.h>
#include <stdio.h>
#include <time.h>

int main(void)
{
    int attributes[] = {GLX_RGBA, GLX_DOUBLEBUFFER, GLX_RED_SIZE, 8,
                        GLX_GREEN_SIZE, 8, GLX_BLUE_SIZE, 8, None};
    Display *display = XOpenDisplay(NULL);
    if (!display) {
        fputs("cannot open DISPLAY\n", stderr);
        return 1;
    }
    int screen = DefaultScreen(display);
    XVisualInfo *visual = glXChooseVisual(display, screen, attributes);
    if (!visual) {
        fputs("cannot select GLX visual\n", stderr);
        return 1;
    }
    Colormap colormap = XCreateColormap(display, RootWindow(display, screen),
                                        visual->visual, AllocNone);
    XSetWindowAttributes window_attributes = {
        .colormap = colormap,
        .event_mask = ExposureMask | StructureNotifyMask,
    };
    Window window = XCreateWindow(display, RootWindow(display, screen),
                                  80, 80, 640, 640, 0, visual->depth,
                                  InputOutput, visual->visual,
                                  CWColormap | CWEventMask,
                                  &window_attributes);
    XStoreName(display, window, "GLX glFinish presentation probe");
    XMapWindow(display, window);

    GLXContext context = glXCreateContext(display, visual, NULL, True);
    if (!context || !glXMakeCurrent(display, window, context)) {
        fputs("cannot create GLX context\n", stderr);
        return 1;
    }

    for (int frame = 0; frame < 300; ++frame) {
        float phase = (float)(frame % 120) / 119.0f;
        glViewport(0, 0, 640, 640);
        glClearColor(1.0f, phase, 0.1f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        glFinish();
        if (frame == 0) {
            unsigned char pixel[4] = {0};
            glReadPixels(320, 320, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
            fprintf(stderr, "readback=%u,%u,%u,%u error=0x%x\n",
                    pixel[0], pixel[1], pixel[2], pixel[3], glGetError());
        }
        glXSwapBuffers(display, window);
        XSync(display, False);
        struct timespec delay = {.tv_nsec = 16667000};
        nanosleep(&delay, NULL);
    }

    glXMakeCurrent(display, None, NULL);
    glXDestroyContext(display, context);
    XDestroyWindow(display, window);
    XFreeColormap(display, colormap);
    XFree(visual);
    XCloseDisplay(display);
    return 0;
}
