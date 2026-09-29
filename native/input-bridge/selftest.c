#define _GNU_SOURCE
#include <assert.h>

#define main bridge_program_main
#include "main.c"
#undef main

static int mouse_output[2];
static int keyboard_output[2];

static void start_outputs(void) {
  assert(pipe2(mouse_output, O_NONBLOCK | O_CLOEXEC) == 0);
  assert(pipe2(keyboard_output, O_NONBLOCK | O_CLOEXEC) == 0);
  memset(virtual_mouse.holders, 0, sizeof(virtual_mouse.holders));
  memset(virtual_keyboard.holders, 0, sizeof(virtual_keyboard.holders));
  virtual_mouse.fd = mouse_output[1];
  virtual_keyboard.fd = keyboard_output[1];
}

static void stop_outputs(void) {
  close(mouse_output[0]);
  close(mouse_output[1]);
  close(keyboard_output[0]);
  close(keyboard_output[1]);
  virtual_mouse.fd = -1;
  virtual_keyboard.fd = -1;
}

static void expect_event(int fd, unsigned short type, unsigned short code, int value) {
  struct input_event event = {0};
  assert(read(fd, &event, sizeof(event)) == (ssize_t)sizeof(event));
  assert(event.type == type && event.code == code && event.value == value);
}

static void expect_empty(int fd) {
  struct input_event event;
  assert(read(fd, &event, sizeof(event)) == -1 && errno == EAGAIN);
}

static void test_classification(void) {
  assert(classify_device(BUS_BLUETOOTH, "ASUS MD100 Mouse", true, false) == ROLE_MOUSE);
  assert(classify_device(BUS_BLUETOOTH, "BT Keyboard", false, true) == ROLE_KEYBOARD);
  assert(classify_device(BUS_BLUETOOTH, "Other Keyboard", false, true) == 0);
  assert(classify_device(BUS_USB, "Dell Dell USB Keyboard", false, true) == ROLE_KEYBOARD);
  assert(classify_device(BUS_USB, " USB OPTICAL MOUSE", true, false) == ROLE_MOUSE);
  assert(classify_device(BUS_USB, "Composite", true, true) ==
         (ROLE_MOUSE | ROLE_KEYBOARD));
  assert(classify_device(BUS_USB, "Gamepad", false, false) == 0);
  assert(classify_device(BUS_VIRTUAL, "hdmi-los-keyboard", false, true) == 0);
  assert(classify_device(BUS_HOST, "gpio-keys", false, true) == 0);
}

static void test_held_key_fan_in(void) {
  start_outputs();
  struct physical_device usb = {.roles = ROLE_KEYBOARD};
  struct physical_device bluetooth = {.roles = ROLE_KEYBOARD};
  change_key(&usb, KEY_A, 1);
  flush_frame(&usb);
  expect_event(keyboard_output[0], EV_KEY, KEY_A, 1);
  expect_event(keyboard_output[0], EV_SYN, SYN_REPORT, 0);

  change_key(&bluetooth, KEY_A, 1);
  flush_frame(&bluetooth);
  change_key(&usb, KEY_A, 0);
  flush_frame(&usb);
  expect_empty(keyboard_output[0]);

  change_key(&bluetooth, KEY_A, 2);
  flush_frame(&bluetooth);
  expect_event(keyboard_output[0], EV_KEY, KEY_A, 2);
  expect_event(keyboard_output[0], EV_SYN, SYN_REPORT, 0);
  change_key(&bluetooth, KEY_A, 0);
  flush_frame(&bluetooth);
  expect_event(keyboard_output[0], EV_KEY, KEY_A, 0);
  expect_event(keyboard_output[0], EV_SYN, SYN_REPORT, 0);
  expect_empty(mouse_output[0]);
  stop_outputs();
}

static void test_composite_and_relative_input(void) {
  start_outputs();
  struct physical_device composite = {.roles = ROLE_MOUSE | ROLE_KEYBOARD};
  change_key(&composite, KEY_ENTER, 1);
  change_key(&composite, BTN_LEFT, 1);
  flush_frame(&composite);
  expect_event(keyboard_output[0], EV_KEY, KEY_ENTER, 1);
  expect_event(keyboard_output[0], EV_SYN, SYN_REPORT, 0);
  expect_event(mouse_output[0], EV_KEY, BTN_LEFT, 1);
  expect_event(mouse_output[0], EV_SYN, SYN_REPORT, 0);

  int input[2];
  assert(pipe2(input, O_NONBLOCK | O_CLOEXEC) == 0);
  composite.fd = input[0];
  struct input_event motion = {.type = EV_REL, .code = REL_X, .value = 12};
  struct input_event sync = {.type = EV_SYN, .code = SYN_REPORT};
  assert(write(input[1], &motion, sizeof(motion)) == (ssize_t)sizeof(motion));
  assert(write(input[1], &sync, sizeof(sync)) == (ssize_t)sizeof(sync));
  assert(forward_events(&composite, POLLIN));
  expect_event(mouse_output[0], EV_REL, REL_X, 12);
  expect_event(mouse_output[0], EV_SYN, SYN_REPORT, 0);
  expect_empty(keyboard_output[0]);
  close(input[0]);
  close(input[1]);

  unsigned char bits[(KEY_CNT + 7) / 8] = {0};
  bits[KEY_ENTER / 8] |= 1U << (KEY_ENTER % 8);
  reconcile_key_bits(&composite, bits);
  flush_frame(&composite);
  expect_empty(keyboard_output[0]);
  memset(bits, 0, sizeof(bits));
  reconcile_key_bits(&composite, bits);
  flush_frame(&composite);
  expect_event(keyboard_output[0], EV_KEY, KEY_ENTER, 0);
  expect_event(keyboard_output[0], EV_SYN, SYN_REPORT, 0);
  expect_event(mouse_output[0], EV_KEY, BTN_LEFT, 0);
  expect_event(mouse_output[0], EV_SYN, SYN_REPORT, 0);
  stop_outputs();
}

static void test_disconnect_keeps_other_source_pressed(void) {
  start_outputs();
  sources = calloc(2, sizeof(*sources));
  assert(sources);
  source_capacity = 2;
  source_count = 2;
  for (size_t i = 0; i < 2; ++i) {
    sources[i] = calloc(1, sizeof(**sources));
    assert(sources[i]);
    sources[i]->fd = open("/dev/null", O_RDONLY);
    assert(sources[i]->fd >= 0);
    sources[i]->roles = ROLE_KEYBOARD;
    change_key(sources[i], KEY_A, 1);
    flush_frame(sources[i]);
  }
  expect_event(keyboard_output[0], EV_KEY, KEY_A, 1);
  expect_event(keyboard_output[0], EV_SYN, SYN_REPORT, 0);
  disconnect_physical(0);
  expect_empty(keyboard_output[0]);
  disconnect_physical(0);
  expect_event(keyboard_output[0], EV_KEY, KEY_A, 0);
  expect_event(keyboard_output[0], EV_SYN, SYN_REPORT, 0);
  free(sources);
  sources = NULL;
  source_capacity = 0;
  stop_outputs();
}

int main(void) {
  test_classification();
  test_held_key_fan_in();
  test_composite_and_relative_input();
  test_disconnect_keeps_other_source_pressed();
  puts("HDMI input bridge fan-in tests: PASS");
  return 0;
}
