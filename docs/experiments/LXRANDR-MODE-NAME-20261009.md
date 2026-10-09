# LXDE Monitor Settings mode-name compatibility

LXRandR 0.3.3 reported "Unable to get monitor information!" although xrandr
returned success and listed the leased DP-1 output and its modes. Its monitor
regex requires the first mode line after the connector header to begin with
numeric WIDTHxHEIGHT. Our preferred mode, hdmi-los-android-current, did not meet
that assumption, so no monitor was added to the GUI.

The agent now generates WIDTHxHEIGHT-android-current for the active Android
mode and uses that same name for Modeline, PreferredMode and Screen Modes.
The suffix keeps the exact selected timing distinct from the EDID mode of the
same dimensions. All timing numbers and flags are preserved.

## Validation on 2026-10-09

- The installed parser's GLib regex rejected the original live xrandr output
  and accepted the resolution-prefixed alias.
- The AArch64 agent compiled on root@192.168.104.201 with -Wall -Wextra -Werror;
  its help command and ELF architecture check passed.
- A connected pause, agent replacement and resume succeeded without a replug.
- The generated alias was 3840x2160-android-current. The modeline remained
  533.250 3840 3888 3920 4000 2160 2163 2168 2222 +HSync -VSync.
- The installed /usr/bin/lxrandr opened Display Settings, detected DP-1 and
  displayed the mode and refresh choices. A cropped X root screenshot confirms
  the dialog; this is a GUI check, not a physical scanout measurement.
- The broader legacy diagnostic-contract test cannot complete in this checkout
  because its third_party Mesa source snapshot is absent. Assertions preceding
  that missing-file read passed. The production GUI was tested directly.

The inherited fullscreen modeset fix, Mesa, Xorg, companion, broker, input
bridge and Android app are unchanged. New builds use the new alias; historical
reports retain the alias used at the time. Raw diagnostic data is in tmpfs
under /tmp/hdmi-lxrandr-20261009 and archived on the build server.

Upstream parser: https://github.com/lxde/lxrandr/blob/0.3.3/src/lxrandr.c#L135
