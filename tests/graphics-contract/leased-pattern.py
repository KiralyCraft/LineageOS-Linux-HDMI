#!/usr/bin/env python3
"""Visible pattern test on an already active leased X display.

Uses the inherited private Mesa environment and owns only its two test windows.
Does not arm, stop, modeset or read a DRM event queue. Optional screenshots
read the X root image, including the window-manager frame, after transitions.
The fixed X11 reference and GLX window have defined black backgrounds. Movement
and resize are separate phases, and rendering uses actual configured geometry.
Swap-call timings are not physical display cadence or input-to-display latency.
"""
import argparse
import ctypes as C
import json
import pathlib
import time

V, I, U, L = C.c_void_p, C.c_int, C.c_uint, C.c_ulong


class Visual(C.Structure):
    _fields_ = [('visual', V), ('id', L), ('screen', I), ('depth', I),
                ('klass', I), ('red', L), ('green', L), ('blue', L),
                ('colormap_size', I), ('bits', I)]


class DefaultVisual(C.Structure):
    _fields_ = [('ext_data', V), ('id', L), ('klass', I), ('red', L),
                ('green', L), ('blue', L), ('bits', I), ('colormap_size', I)]


class Attributes(C.Structure):
    _fields_ = [('background_pixmap', L), ('background_pixel', L),
                ('border_pixmap', L), ('border_pixel', L), ('bit_gravity', I),
                ('win_gravity', I), ('backing_store', I), ('backing_planes', L),
                ('backing_pixel', L), ('save_under', I), ('event_mask', C.c_long),
                ('do_not_propagate', C.c_long), ('override_redirect', I),
                ('colormap', L), ('cursor', L)]


class Event(C.Union):
    _fields_ = [('type', I), ('padding', L * 24)]


def rectangles(width, height, serial):
    """Black surround, four solid panels, and a changing white frame marker."""
    margin = 20
    mx, my = width // 2, height // 2
    return [
        (margin, margin, mx - margin, my - margin, (1., 0., 1.)),
        (mx, margin, width - margin - mx, my - margin, (1., 1., 0.)),
        (margin, my, mx - margin, height - margin - my, (0., 1., 0.)),
        (mx, my, width - margin - mx, height - margin - my, (0., 0., 1.)),
        (margin, 4, 10 + (serial % 60) * 5, 8, (1., 1., 1.)),
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('output', type=pathlib.Path)
    ap.add_argument('--label', default='BCDEF')
    ap.add_argument('--phase-seconds', type=float, default=4.)
    ap.add_argument('--screenshot-dir', type=pathlib.Path,
                    help='Opt-in root screenshots; saved after rendering, changes timing')
    args = ap.parse_args()
    assert not args.output.exists()
    assert not args.screenshot_dir or not args.screenshot_dir.exists()
    assert 1 <= args.phase_seconds <= 5
    X, G = C.CDLL('libX11.so.6'), C.CDLL('libGL.so.1')

    def bind(lib, name, ret, types):
        f = getattr(lib, name)
        f.restype, f.argtypes = ret, types
        return f

    d = bind(X, 'XOpenDisplay', V, [C.c_char_p])(None)
    assert d, 'Inherited display unavailable'
    screen = bind(X, 'XDefaultScreen', I, [V])(d)
    root = bind(X, 'XRootWindow', L, [V, I])(d, screen)
    screen_width = bind(X, 'XDisplayWidth', I, [V, I])(d, screen)
    screen_height = bind(X, 'XDisplayHeight', I, [V, I])(d, screen)
    assert screen_width >= 3000 and screen_height >= 1500, 'Test expects 4K output'
    sync = bind(X, 'XSync', I, [V, I])
    pending = bind(X, 'XPending', I, [V])
    next_event = bind(X, 'XNextEvent', I, [V, C.POINTER(Event)])
    store_name = bind(X, 'XStoreName', I, [V, L, C.c_char_p])
    move = bind(X, 'XMoveWindow', I, [V, L, I, I])
    resize = bind(X, 'XResizeWindow', I, [V, L, U, U])
    move_resize = bind(X, 'XMoveResizeWindow', I, [V, L, I, I, U, U])
    geometry = bind(X, 'XGetGeometry', I,
                    [V, L, C.POINTER(L), C.POINTER(I), C.POINTER(I),
                     C.POINTER(U), C.POINTER(U), C.POINTER(U), C.POINTER(U)])
    default_visual = bind(X, 'XDefaultVisual', C.POINTER(DefaultVisual), [V, I])(d, screen)
    assert default_visual.contents.klass == 4, 'Reference requires TrueColor'
    reference = bind(X, 'XCreateSimpleWindow', L,
                     [V, L, I, I, U, U, U, L, L])(
                         d, root, screen_width - 880, 150, 800, 600, 0, 0, 0)
    store_name(d, reference, b'Fixed X11 reference - four panels, black border')
    bind(X, 'XSelectInput', I, [V, L, C.c_long])(d, reference, 1 << 15)
    gc = bind(X, 'XCreateGC', V, [V, L, L, V])(d, reference, 0, None)
    assert reference and gc
    foreground = bind(X, 'XSetForeground', I, [V, V, L])
    fill = bind(X, 'XFillRectangle', I, [V, L, V, I, I, U, U])

    def draw_reference():
        foreground(d, gc, 0)
        fill(d, reference, gc, 0, 0, 800, 600)
        # Convert GL's bottom-left coordinates to X's top-left coordinates.
        masks = [default_visual.contents.red, default_visual.contents.green,
                 default_visual.contents.blue]
        for x, y, w, h, rgb in rectangles(800, 600, 0):
            pixel = sum(mask for mask, component in zip(masks, rgb) if component)
            foreground(d, gc, pixel)
            fill(d, reference, gc, x, 600 - y - h, w, h)

    def drain_events():
        counts = {}
        event = Event()
        while pending(d):
            next_event(d, C.byref(event))
            counts[event.type] = counts.get(event.type, 0) + 1
        if counts.get(12):
            draw_reference()
        return counts

    def size():
        r, x, y, w, h, border, depth = L(), I(), I(), U(), U(), U(), U()
        assert geometry(d, window, C.byref(r), C.byref(x), C.byref(y),
                        C.byref(w), C.byref(h), C.byref(border), C.byref(depth))
        return w.value, h.value

    visual = bind(G, 'glXChooseVisual', C.POINTER(Visual), [V, I, C.POINTER(I)])(
        d, screen, (I * 9)(4, 5, 8, 8, 9, 8, 10, 8, 0))
    assert visual
    colormap = bind(X, 'XCreateColormap', L, [V, L, V, I])(
        d, root, visual.contents.visual, 0)
    attrs = Attributes(background_pixel=0, colormap=colormap,
                       event_mask=(1 << 15) | (1 << 17))
    window = bind(X, 'XCreateWindow', L,
                  [V, L, I, I, U, U, U, I, U, V, L, C.POINTER(Attributes)])(
                      d, root, 120, 150, 800, 600, 0, visual.contents.depth, 1,
                      visual.contents.visual, (1 << 1) | (1 << 13) | (1 << 11),
                      C.byref(attrs))
    context = bind(G, 'glXCreateContext', V, [V, C.POINTER(Visual), V, I])(
        d, visual, None, 1)
    assert window and context
    make_current = bind(G, 'glXMakeCurrent', I, [V, L, V])
    assert make_current(d, window, context)
    renderer = bind(G, 'glGetString', C.c_char_p, [U])(0x1f01).decode()
    assert 'FD740' in renderer, renderer
    assert bind(G, 'glXIsDirect', I, [V, V])(d, context)
    loaded = sorted({line.split()[-1]
                     for line in pathlib.Path('/proc/self/maps').read_text().splitlines()
                     if any(s in line for s in ['libgallium', 'libGLX_mesa', 'libEGL_mesa'])})
    assert loaded and all(not p.startswith('/usr/lib/') for p in loaded), loaded
    viewport = bind(G, 'glViewport', None, [I, I, I, I])
    clear_color = bind(G, 'glClearColor', None, [C.c_float] * 4)
    clear = bind(G, 'glClear', None, [U])
    enable, disable = [bind(G, n, None, [U]) for n in ('glEnable', 'glDisable')]
    scissor = bind(G, 'glScissor', None, [I, I, I, I])
    swap = bind(G, 'glXSwapBuffers', None, [V, L])
    error = bind(G, 'glGetError', U, [])
    map_window = bind(X, 'XMapWindow', I, [V, L])
    capture = None
    if args.screenshot_dir:
        from xroot_capture import RootCapture
        capture = RootCapture(d, X, root, screen_width, screen_height)
    result = {'renderer': renderer, 'loaded': loaded, 'label': args.label,
              'background': 'defined black', 'readback': bool(capture), 'phases': [],
              'note': 'Swap-call timing is not physical display cadence or latency.'}
    if capture:
        result.update(screenshots=capture.records, performance_result=False,
                      screenshot_note='Root readback can synchronize GPU work and conceal timing faults; PNG data stays in RAM until the workload ends.')
    try:
        store_name(d, window, (args.label + ' - mapping, black background').encode())
        map_window(d, reference)
        draw_reference()
        map_window(d, window)
        sync(d, 0)
        deadline = time.monotonic() + 3
        mapped = False
        while time.monotonic() < deadline and not mapped:
            mapped = bool(drain_events().get(19))
            if not mapped:
                time.sleep(.01)
        assert mapped, 'No MapNotify for test window'
        if capture:
            capture.capture(window, 'mapped-primary')
        serial = 0
        phases = ['stationary', 'move-only', 'resize-only', 'move-and-resize',
                  'stationary-after']
        for phase in phases:
            store_name(d, window, (args.label + ' - ' + phase).encode())
            move_resize(d, window, 120, 150, 800, 600)
            sync(d, 0)
            start, samples, changes, frames, previous = time.monotonic(), [], [], 0, -1
            capture_due = None
            if capture:
                capture.capture(reference, phase + '-reference', {'phase': phase})
            while time.monotonic() - start < args.phase_seconds:
                before_frame = time.monotonic()
                step = int((before_frame - start) / .65)
                if step != previous:
                    w, h = [(800, 600), (1001, 701), (1279, 719)][step % 3]
                    x, y = [(120, 150), (1450, 900)][step % 2]
                    if phase == 'move-only':
                        move(d, window, x, y)
                    elif phase == 'resize-only':
                        resize(d, window, w, h)
                    elif phase == 'move-and-resize':
                        move_resize(d, window, x, y, w, h)
                    sync(d, 0)
                    previous = step
                    changes.append({'seconds': before_frame - start, 'step': step})
                    if capture and (step == 0 or phase in ('move-only', 'resize-only', 'move-and-resize')):
                        capture.capture(window, f'{phase}-{step}-immediate',
                                        {'phase': phase, 'step': step, 'kind': 'immediate',
                                         'requested_xy': [x, y] if 'move' in phase else None,
                                         'requested_size': [w, h] if 'resize' in phase else None})
                        capture_due = (time.monotonic() + .15, step)
                drain_events()
                # Geometry replies follow the requests; re-query also handles
                # WM-mediated configurations that arrive after the first reply.
                width, height = size()
                changes[-1]['last_actual_size'] = [width, height]
                viewport(0, 0, width, height)
                disable(0x0c11)
                clear_color(0., 0., 0., 1.)
                clear(0x4000)
                enable(0x0c11)
                for x, y, w, h, rgb in rectangles(width, height, serial):
                    scissor(x, y, w, h)
                    clear_color(*rgb, 1.)
                    clear(0x4000)
                disable(0x0c11)
                before_swap = time.monotonic()
                swap(d, window)
                samples.append((time.monotonic() - before_swap) * 1000)
                assert error() == 0, (phase, frames)
                serial, frames = serial + 1, frames + 1
                if capture_due and time.monotonic() >= capture_due[0]:
                    capture.capture(window, f'{phase}-{capture_due[1]}-settled',
                                    {'phase': phase, 'step': capture_due[1], 'kind': 'settled',
                                     'frame_serial': serial, 'actual_size': [width, height]})
                    capture_due = None
                time.sleep(max(0, 1 / 30 - (time.monotonic() - before_frame)))
            if capture_due:
                capture.capture(window, f'{phase}-{capture_due[1]}-phase-end',
                                {'phase': phase, 'step': capture_due[1], 'kind': 'phase-end'})
            ordered = sorted(samples)
            entry = {'phase': phase, 'frames': frames,
                     'seconds': time.monotonic() - start, 'changes': changes,
                     'swap_p50_ms': ordered[len(ordered) // 2],
                     'swap_p95_ms': ordered[int(len(ordered) * .95)],
                     'swap_max_ms': ordered[-1]}
            result['phases'].append(entry)
            print(json.dumps(entry), flush=True)
        result['completed'] = True
    finally:
        bind(G, 'glFinish', None, [])()  # Lifecycle only, never per frame.
        make_current(d, 0, None)
        bind(G, 'glXDestroyContext', None, [V, V])(d, context)
        for own_window in (window, reference):
            bind(X, 'XDestroyWindow', I, [V, L])(d, own_window)
        bind(X, 'XFreeGC', I, [V, V])(d, gc)
        bind(X, 'XFreeColormap', I, [V, L])(d, colormap)
        bind(X, 'XFree', I, [V])(C.cast(visual, V))
        bind(X, 'XCloseDisplay', I, [V])(d)
        if capture:
            capture.save(args.screenshot_dir)
        args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
