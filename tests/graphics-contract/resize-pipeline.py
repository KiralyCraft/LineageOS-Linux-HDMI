#!/usr/bin/env python3
"""Leased GLX deferred-draw ordering and unique-size resize diagnostics.

Only owns its test window. Drains all X events and renders at actual geometry.
Ordering captures read the X root after settling; resize timing uses no readback.
No frame counter here represents physical scanout cadence.
"""
import argparse
import ctypes as C
import importlib.util
import json
import math
import os
import pathlib
import time

from xroot_capture import RootCapture
import struct
import zlib

spec = importlib.util.spec_from_file_location('leased_pattern', pathlib.Path(__file__).with_name('leased-pattern.py'))
pattern = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pattern)
V, I, U, L = C.c_void_p, C.c_int, C.c_uint, C.c_ulong


def summary(values):
    v = sorted(values)
    return dict(count=len(v), mean_ms=sum(v) / len(v), p50_ms=v[len(v) // 2],
                p95_ms=v[math.ceil(len(v) * .95) - 1], max_ms=v[-1])


def screenshot_samples(png, coordinates):
    # RootCapture emits opaque RGB PNGs with filter None on every scanline.
    assert png[:8] == b'\x89PNG\r\n\x1a\n'
    offset, compressed = 8, bytearray()
    while offset < len(png):
        length = struct.unpack_from('>I', png, offset)[0]
        kind, payload = png[offset+4:offset+8], png[offset+8:offset+8+length]
        if kind == b'IHDR':
            width, height, depth, color, _, _, _ = struct.unpack('>2I5B', payload)
            assert depth == 8 and color == 2
        if kind == b'IDAT': compressed.extend(payload)
        offset += length+12
    raw = zlib.decompress(compressed)
    stride = width*3+1
    assert len(raw) == stride*height and all(raw[y*stride] == 0 for y in range(height))
    pixels = []
    for x, y in coordinates:
        assert 0 <= x < width and 0 <= y < height
        start = y*stride+1+x*3
        pixels.append(tuple(raw[start:start+3]))
    return pixels


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('output', type=pathlib.Path)
    ap.add_argument('--workload', choices=['ordering', 'resize-pattern', 'unique-resize'], default='ordering')
    ap.add_argument('--samples', type=int, choices=[0,4], default=0)
    ap.add_argument('--flush-control', action='store_true', help='Diagnostic glFlush before swap; never glFinish')
    args = ap.parse_args()
    args.output.mkdir(exist_ok=False, parents=True)
    X, G = C.CDLL('libX11.so.6'), C.CDLL('libGL.so.1')

    def bind(lib, name, ret, types):
        f = getattr(lib, name)
        f.restype, f.argtypes = ret, types
        return f

    d = bind(X, 'XOpenDisplay', V, [C.c_char_p])(None)
    assert d, 'No inherited display'
    screen = bind(X, 'XDefaultScreen', I, [V])(d)
    root = bind(X, 'XRootWindow', L, [V, I])(d, screen)
    sw = bind(X, 'XDisplayWidth', I, [V, I])(d, screen)
    sh = bind(X, 'XDisplayHeight', I, [V, I])(d, screen)
    visual_attributes = [4, 5, 8, 8, 9, 8, 10, 8]
    if args.samples:
        visual_attributes.extend([100000, 1, 100001, args.samples])
    visual_attributes.append(0)
    vi = bind(G, 'glXChooseVisual', C.POINTER(pattern.Visual), [V, I, C.POINTER(I)])(
        d, screen, (I * len(visual_attributes))(*visual_attributes))
    assert vi
    cmap = bind(X, 'XCreateColormap', L, [V, L, V, I])(d, root, vi.contents.visual, 0)
    attrs = pattern.Attributes(background_pixel=0, colormap=cmap, event_mask=(1 << 15) | (1 << 17))
    window = bind(X, 'XCreateWindow', L,
                  [V, L, I, I, U, U, U, I, U, V, L, C.POINTER(pattern.Attributes)])(
                      d, root, 160, 180, 800, 600, 0, vi.contents.depth, 1,
                      vi.contents.visual, (1 << 1) | (1 << 13) | (1 << 11), C.byref(attrs))
    context = bind(G, 'glXCreateContext', V, [V, C.POINTER(pattern.Visual), V, I])(d, vi, None, 1)
    make = bind(G, 'glXMakeCurrent', I, [V, L, V])
    assert window and context
    bind(X, 'XStoreName', I, [V, L, C.c_char_p])(d, window, b'HDMI deferred draw / unique resize diagnostic')
    bind(X, 'XMapRaised', I, [V, L])(d, window)
    sync = bind(X, 'XSync', I, [V, I])
    pending = bind(X, 'XPending', I, [V])
    next_event = bind(X, 'XNextEvent', I, [V, C.POINTER(pattern.Event)])
    resize = bind(X, 'XResizeWindow', I, [V, L, U, U])
    geometry = bind(X, 'XGetGeometry', I,
                    [V, L, C.POINTER(L), C.POINTER(I), C.POINTER(I),
                     C.POINTER(U), C.POINTER(U), C.POINTER(U), C.POINTER(U)])
    sync(d, 0)
    # Complete window mapping before creating drawable attachments. MapWindow
    # is mediated by the WM, so XSync alone does not establish MapNotify.
    event, mapped, deadline = pattern.Event(), False, time.monotonic()+3
    while not mapped and time.monotonic() < deadline:
        while pending(d):
            next_event(d, C.byref(event))
            mapped |= event.type == 19 and event.padding[4] == window
        if not mapped: time.sleep(.01)
    assert mapped, 'Test window did not map'
    assert make(d, window, context)
    renderer = bind(G, 'glGetString', C.c_char_p, [U])(0x1f01).decode()
    assert 'FD740' in renderer, renderer
    assert bind(G, 'glXIsDirect', I, [V, V])(d, context)
    loaded = sorted({line.split()[-1] for line in pathlib.Path('/proc/self/maps').read_text().splitlines()
                     if any(s in line for s in ['libgallium', 'libGLX_mesa', 'libEGL_mesa'])})
    assert loaded and all(not name.startswith('/usr/lib/') for name in loaded), loaded
    viewport = bind(G, 'glViewport', None, [I] * 4)
    clear_color = bind(G, 'glClearColor', None, [C.c_float] * 4)
    clear = bind(G, 'glClear', None, [U])
    color = bind(G, 'glColor3ub', None, [C.c_ubyte] * 3)
    begin = bind(G, 'glBegin', None, [U])
    vertex = bind(G, 'glVertex2f', None, [C.c_float] * 2)
    end = bind(G, 'glEnd', None, [])
    flush = bind(G, 'glFlush', None, [])
    swap = bind(G, 'glXSwapBuffers', None, [V, L])
    get_error = bind(G, 'glGetError', U, [])
    event = pattern.Event()
    capture = RootCapture(d, X, root, sw, sh) if args.workload != 'unique-resize' else None
    records = []
    samples = I()
    bind(G, 'glGetIntegerv', None, [U, C.POINTER(I)])(0x80a9, C.byref(samples))
    assert samples.value == args.samples, samples.value
    result = dict(renderer=renderer, samples=samples.value, loaded=loaded, screen=[sw, sh], workload=args.workload,
                  pipeline_config={name:os.environ.get(name) for name in ['MESA_KGSL_X11_PIPELINE','MESA_KGSL_X11_INTEGRATED_RESOLVE','MESA_KGSL_HDMI_QUEUE','MESA_KGSL_HDMI_RESIZE_CAPACITY']},
                  flush_control=args.flush_control, records=records, physical_scanout_tested=False,
                  note='Ordering screenshots synchronize server readback; unique-resize timings do not measure displayed FPS.')

    def drain_and_size():
        count = 0
        while pending(d):
            next_event(d, C.byref(event)); count += 1
        r, x, y, w, h, border, depth = L(), I(), I(), U(), U(), U(), U()
        assert geometry(d, window, C.byref(r), C.byref(x), C.byref(y), C.byref(w), C.byref(h), C.byref(border), C.byref(depth))
        return w.value, h.value, count

    def draw(serial, width, height, quadrants=False):
        viewport(0, 0, width, height)
        clear_color(229/255, 17/255, 173/255, 1.)
        clear(0x4000)
        rgb = [(serial * factor + 41) % 192 + 32 for factor in [37, 71, 29]]
        # Keep the last draw in the compatibility vertex cache: no GL query,
        # state change or explicit flush intervenes between glEnd and swap.
        colors = [rgb]
        boxes = [(-1., -1., 1., 1.)]
        if quadrants:
            # X screenshots have a top-left origin. These four colors identify
            # both axes, the frame serial, and the current logical extent.
            colors = [[(c+offset) % 192+32 for c in rgb] for offset in [0,41,83,127]]
            boxes = [(-1.,0.,0.,1.), (0.,0.,1.,1.), (-1.,-1.,0.,0.), (0.,-1.,1.,0.)]
        for shade, (x0,y0,x1,y1) in zip(colors,boxes):
            color(*shade)
            begin(0x0007)
            for x,y in [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]: vertex(x,y)
            end()
        if args.flush_control:
            flush()
        before_swap = time.monotonic_ns()
        swap(d, window)
        swap_ms = (time.monotonic_ns() - before_swap) / 1e6
        assert get_error() == 0
        return colors if quadrants else rgb, swap_ms

    try:
        time.sleep(.15)
        if capture:
            sizes = [(639,479),(640,480),(641,481),(767,511),(768,512),(769,513),
                     (895,639),(896,640),(897,641),(800,600),(641,479),(800,600)]*2
            quadrants = args.workload == 'resize-pattern'
            for serial in range(1, len(sizes)+1 if quadrants else 9):
                if quadrants:
                    resize(d,window,*sizes[serial-1]); sync(d,0)
                width, height, events = drain_and_size()
                if quadrants:
                    deadline=time.monotonic()+2
                    while (width,height)!=sizes[serial-1] and time.monotonic()<deadline:
                        time.sleep(.005);sync(d,0)
                        width,height,new_events=drain_and_size();events+=new_events
                    assert (width,height)==sizes[serial-1], 'WM did not settle on requested test geometry'
                rgb, swap_ms = draw(serial, width, height, quadrants)
                time.sleep(.20)
                rec = capture.capture(window, f'frame-{serial}', dict(serial=serial, expected_rgb=rgb))
                cx, cy, cw, ch = rec['client_bounds']
                rx, ry, _, _ = rec['root_crop']
                positions = [(.03,.03),(.25,.25),(.75,.25),(.97,.03),(.03,.97),(.25,.75),(.75,.75),(.97,.97)] if quadrants else [(.1,.1),(.5,.1),(.9,.1),(.1,.5),(.5,.5),(.9,.5),(.1,.9),(.5,.9),(.9,.9)]
                coordinates = [(cx-rx+int(cw*x), cy-ry+int(ch*y)) for x,y in positions]
                samples = screenshot_samples(capture.images[-1][1], coordinates)
                expected = [rgb[int(y>=.5)*2+int(x>=.5)] for x,y in positions] if quadrants else [rgb]*len(positions)
                records.append(dict(serial=serial, geometry=[width, height], drained_events=events,
                                    swap_ms=swap_ms, expected_rgb=rgb, expected_samples=expected, samples=samples,
                                    passed=rec['geometry_stable'] and all(all(abs(a-b)<=1 for a,b in zip(pixel,shade)) for pixel,shade in zip(samples,expected))))
        else:
            start, serial = time.monotonic(), 1
            while time.monotonic()-start < 10:
                frame_start = time.monotonic()
                age = frame_start-start
                phase = 'fixed-before' if age < 1 else ('unique-resize' if age < 7 else 'fixed-after')
                requested = [800, 600] if phase != 'unique-resize' else [641+7*serial, 479+3*serial]
                requested = [min(requested[0], 1800), min(requested[1], 1200)]
                before_resize = time.monotonic_ns()
                resize(d, window, *requested)
                sync(d, 0)
                width, height, events = drain_and_size()
                configure_ms = (time.monotonic_ns()-before_resize)/1e6
                before_draw = time.monotonic_ns()
                rgb, swap_ms = draw(serial, width, height)
                records.append(dict(serial=serial, phase=phase, requested=requested, actual=[width,height],
                                    drained_events=events, configure_ms=configure_ms, swap_ms=swap_ms,
                                    draw_swap_ms=(time.monotonic_ns()-before_draw)/1e6))
                serial += 1
                time.sleep(max(0,1/30-(time.monotonic()-frame_start)))
            result['phases'] = {phase:dict(frames=len(rows), unique_sizes=len({tuple(x['actual']) for x in rows}),
                 configure=summary([x['configure_ms'] for x in rows]), swap=summary([x['swap_ms'] for x in rows]),
                 draw_swap=summary([x['draw_swap_ms'] for x in rows]))
                 for phase in ['fixed-before','unique-resize','fixed-after']
                 if (rows := [x for x in records if x['phase']==phase])}
        result['completed'] = True
    finally:
        bind(G, 'glFinish', None, [])()  # Final lifecycle only.
        make(d, 0, None)
        bind(G, 'glXDestroyContext', None, [V, V])(d, context)
        bind(X, 'XDestroyWindow', I, [V, L])(d, window)
        bind(X, 'XFreeColormap', I, [V, L])(d, cmap)
        bind(X, 'XFree', I, [V])(C.cast(vi, V))
        bind(X, 'XCloseDisplay', I, [V])(d)
        if capture:
            capture.save(args.output/'frames')
            result['captures'] = capture.records
        (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    if capture:
        print(json.dumps(dict(frames=len(records), passed=sum(x['passed'] for x in records), flush_control=args.flush_control)),flush=True)
    else:
        print(json.dumps(result['phases']),flush=True)


if __name__ == '__main__':
    main()
