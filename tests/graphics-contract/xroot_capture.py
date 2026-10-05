"""Capture window-manager frames from the root drawable of an existing X display.

PNG encoding uses only the standard library. Captures stay in memory until
save() is called after the workload. These are X screenshots, not scanout reads.
"""
import ctypes as C
import pathlib
import struct
import time
import zlib

V, I, U, L = C.c_void_p, C.c_int, C.c_uint, C.c_ulong


class XImage(C.Structure):
    # The prefix through blue_mask is sufficient; XDestroyImage uses Xlib's
    # complete allocation, including the function table following this prefix.
    _fields_ = [('width', I), ('height', I), ('xoffset', I), ('format', I),
                ('data', V), ('byte_order', I), ('bitmap_unit', I),
                ('bitmap_bit_order', I), ('bitmap_pad', I), ('depth', I),
                ('bytes_per_line', I), ('bits_per_pixel', I),
                ('red_mask', L), ('green_mask', L), ('blue_mask', L)]


def encode_png(image, data):
    """Convert padded TrueColor XImage rows to an opaque RGB PNG."""
    width, height = image.width, image.height
    bpp = image.bits_per_pixel // 8
    assert width > 0 and height > 0 and bpp in (3, 4)
    assert image.xoffset == 0 and image.byte_order in (0, 1)
    offsets = []
    for mask in (image.red_mask, image.green_mask, image.blue_mask):
        shift = (mask & -mask).bit_length() - 1
        assert shift >= 0 and shift % 8 == 0 and mask == 255 << shift
        offset = shift // 8
        if image.byte_order == 1:
            offset = bpp - 1 - offset
        assert 0 <= offset < bpp
        offsets.append(offset)
    assert len(set(offsets)) == 3
    stride = image.bytes_per_line
    assert stride >= width * bpp and len(data) == stride * height
    rows = bytearray((width * 3 + 1) * height)
    for y in range(height):
        start = y * (width * 3 + 1) + 1  # preceding byte: PNG filter None
        source = data[y * stride:y * stride + width * bpp]
        for channel, offset in enumerate(offsets):
            rows[start + channel:start + width * 3:3] = source[offset::bpp]

    def chunk(kind, payload):
        return (struct.pack('!I', len(payload)) + kind + payload +
                struct.pack('!I', zlib.crc32(kind + payload) & 0xffffffff))

    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows, 1)) + chunk(b'IEND', b''))


class RootCapture:
    def __init__(self, display, xlib, root, screen_width, screen_height):
        self.display, self.root = display, root
        self.screen_width, self.screen_height = screen_width, screen_height
        self.records, self.images, self.total_bytes = [], [], 0

        def bind(name, ret, types):
            f = getattr(xlib, name)
            f.restype, f.argtypes = ret, types
            return f

        self.query = bind('XQueryTree', I,
                          [V, L, C.POINTER(L), C.POINTER(L),
                           C.POINTER(C.POINTER(L)), C.POINTER(U)])
        self.geometry = bind('XGetGeometry', I,
                             [V, L, C.POINTER(L), C.POINTER(I), C.POINTER(I),
                              C.POINTER(U), C.POINTER(U), C.POINTER(U), C.POINTER(U)])
        self.translate = bind('XTranslateCoordinates', I,
                              [V, L, L, I, I, C.POINTER(I), C.POINTER(I), C.POINTER(L)])
        self.get_image = bind('XGetImage', C.POINTER(XImage),
                              [V, L, I, I, U, U, L, I])
        self.destroy = bind('XDestroyImage', I, [C.POINTER(XImage)])
        self.free = bind('XFree', I, [V])

    def bounds(self, window):
        root, x, y, w, h, border, depth, child = L(), I(), I(), U(), U(), U(), U(), L()
        assert self.geometry(self.display, window, C.byref(root), C.byref(x),
                             C.byref(y), C.byref(w), C.byref(h),
                             C.byref(border), C.byref(depth))
        assert self.translate(self.display, window, self.root, 0, 0,
                              C.byref(x), C.byref(y), C.byref(child))
        return [x.value - border.value, y.value - border.value,
                w.value + 2 * border.value, h.value + 2 * border.value]

    def frame(self, window):
        current = window
        for _ in range(32):
            root, parent, children, count = L(), L(), C.POINTER(L)(), U()
            assert self.query(self.display, current, C.byref(root), C.byref(parent),
                              C.byref(children), C.byref(count))
            if children:
                self.free(C.cast(children, V))
            if parent.value == self.root:
                return current
            assert parent.value and parent.value != current
            current = parent.value
        raise RuntimeError('Window parent chain exceeded capture bound')

    def capture(self, window, label, metadata=None):
        started = time.monotonic_ns()
        frame = self.frame(window)
        frame_bounds, client_bounds = self.bounds(frame), self.bounds(window)
        x, y, w, h = frame_bounds
        x1, y1 = max(0, x - 8), max(0, y - 8)
        x2, y2 = min(self.screen_width, x + w + 8), min(self.screen_height, y + h + 8)
        assert x2 > x1 and y2 > y1, 'Window is entirely offscreen'
        pointer = self.get_image(self.display, self.root, x1, y1, x2 - x1,
                                 y2 - y1, L(-1).value, 2)  # AllPlanes, ZPixmap
        assert pointer, 'Root XGetImage failed'
        try:
            image = pointer.contents
            assert image.data and 0 < image.bytes_per_line * image.height <= 64 * 1024 * 1024
            png = encode_png(image, C.string_at(image.data, image.bytes_per_line * image.height))
        finally:
            self.destroy(pointer)
        assert self.total_bytes + len(png) <= 128 * 1024 * 1024, 'Capture RAM budget exceeded'
        safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in label)
        name = f'{len(self.records):03d}-{safe}.png'
        frame_after = self.frame(window)
        bounds_after, client_after = self.bounds(frame_after), self.bounds(window)
        record = {'file': name, 'source': 'X root drawable; not DRM scanout',
                  'client_window': int(window), 'frame_window': int(frame),
                  'client_bounds': client_bounds, 'frame_bounds': frame_bounds,
                  'client_bounds_after': client_after, 'frame_bounds_after': bounds_after,
                  'geometry_stable': (frame == frame_after and frame_bounds == bounds_after
                                      and client_bounds == client_after),
                  'root_crop': [x1, y1, x2 - x1, y2 - y1],
                  'started_monotonic_ns': started,
                  'capture_ms': (time.monotonic_ns() - started) / 1e6,
                  'metadata': metadata or {}}
        self.records.append(record)
        self.images.append((name, png))
        self.total_bytes += len(png)
        return record

    def save(self, directory):
        directory = pathlib.Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        for name, data in self.images:
            with (directory / name).open('xb') as output:
                output.write(data)
            (directory / name).chmod(0o600)
        self.images.clear()
