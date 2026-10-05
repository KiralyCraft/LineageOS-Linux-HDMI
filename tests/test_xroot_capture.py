import ctypes as C
import importlib.util
import os
from pathlib import Path
import select
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

SPEC = importlib.util.spec_from_file_location(
    'xroot_capture', Path(__file__).parent / 'graphics-contract/xroot_capture.py')
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


def png_rgb(data):
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pos, compressed = 8, bytearray()
    while pos < len(data):
        length, kind = struct.unpack('!I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        crc, = struct.unpack('!I', data[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xffffffff
        if kind == b'IHDR':
            width, height, depth, color, _, _, _ = struct.unpack('!2I5B', body)
            assert (depth, color) == (8, 2)
        elif kind == b'IDAT':
            compressed.extend(body)
        pos += length + 12
    raw = zlib.decompress(compressed)
    stride = width * 3 + 1
    assert len(raw) == stride * height
    assert all(raw[y * stride] == 0 for y in range(height))
    return width, height, b''.join(raw[y * stride + 1:(y + 1) * stride] for y in range(height))


class EncodingTests(unittest.TestCase):
    def test_byte_order_padding_and_ignored_alpha(self):
        for order in (0, 1):
            with self.subTest(order=order):
                image = capture.XImage(width=2, height=2, bytes_per_line=12,
                                       bits_per_pixel=32, byte_order=order,
                                       red_mask=0xff0000, green_mask=0xff00, blue_mask=0xff)
                words = [0x00010203, 0xff040506, 0x7f070809, 0x80101112]
                fmt = '<I' if order == 0 else '>I'
                data = (b''.join(struct.pack(fmt, v) for v in words[:2]) + b'PAD!' +
                        b''.join(struct.pack(fmt, v) for v in words[2:]) + b'PAD!')
                width, height, rgb = png_rgb(capture.encode_png(image, data))
                self.assertEqual((width, height), (2, 2))
                self.assertEqual(rgb, bytes.fromhex('010203040506070809101112'))

    def test_reject_non_byte_color_masks(self):
        image = capture.XImage(width=1, height=1, bytes_per_line=4,
                               bits_per_pixel=32, red_mask=0xf800,
                               green_mask=0x7e0, blue_mask=0x1f)
        with self.assertRaises(AssertionError):
            capture.encode_png(image, b'\0' * 4)


@unittest.skipUnless(shutil.which('Xvfb'), 'Xvfb unavailable')
class RootCaptureTests(unittest.TestCase):
    def test_reparented_frame_move_resize_and_deferred_save(self):
        read_fd, write_fd = os.pipe()
        server = subprocess.Popen(['Xvfb', '-displayfd', str(write_fd), '-screen',
                                   '0', '640x480x24', '-nolisten', 'tcp', '-noreset'],
                                  pass_fds=(write_fd,), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        os.close(write_fd)
        display = None
        X = C.CDLL('libX11.so.6')

        def bind(name, ret, args):
            f = getattr(X, name)
            f.restype, f.argtypes = ret, args
            return f

        V, I, U, L = capture.V, capture.I, capture.U, capture.L
        try:
            self.assertTrue(select.select([read_fd], [], [], 8)[0], 'Xvfb startup timeout')
            number = os.read(read_fd, 32).strip()
            self.assertTrue(number.isdigit())
            display = bind('XOpenDisplay', V, [C.c_char_p])(b':' + number)
            self.assertTrue(display)
            root = bind('XDefaultRootWindow', L, [V])(display)
            create = bind('XCreateSimpleWindow', L, [V, L, I, I, U, U, U, L, L])
            frame = create(display, root, 20, 30, 180, 140, 0, 0, 0x0000ff)
            child = create(display, frame, 4, 24, 172, 112, 0, 0, 0x00ff00)
            map_window = bind('XMapWindow', I, [V, L])
            map_window(display, frame)
            map_window(display, child)
            sync = bind('XSync', I, [V, I])
            sync(display, 0)
            recorder = capture.RootCapture(display, X, root, 640, 480)
            first = recorder.capture(child, 'mapped')
            self.assertEqual(first['frame_window'], frame)
            self.assertEqual(first['frame_bounds'], [20, 30, 180, 140])
            self.assertEqual(first['client_bounds'], [24, 54, 172, 112])
            self.assertTrue(first['geometry_stable'])
            width, height, rgb = png_rgb(recorder.images[0][1])
            x0, y0, _, _ = first['root_crop']

            def pixel(x, y):
                offset = ((y - y0) * width + x - x0) * 3
                return rgb[offset:offset + 3]

            self.assertEqual(pixel(30, 40), b'\0\0\xff')  # frame/title area
            self.assertEqual(pixel(40, 70), b'\0\xff\0')  # child/client area
            bind('XMoveResizeWindow', I, [V, L, I, I, U, U])(
                display, frame, -4, 100, 240, 160)
            sync(display, 0)
            moved = recorder.capture(child, 'moved-resized')
            self.assertEqual(moved['frame_bounds'], [-4, 100, 240, 160])
            self.assertEqual(moved['root_crop'], [0, 92, 244, 176])
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'captures'
                self.assertFalse(target.exists())
                recorder.save(target)
                self.assertEqual(len(list(target.glob('*.png'))), 2)
                self.assertFalse(recorder.images)
                self.assertEqual((target / first['file']).stat().st_mode & 0o777, 0o600)
        finally:
            if display:
                bind('XCloseDisplay', I, [V])(display)
            os.close(read_fd)
            server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    unittest.main()
