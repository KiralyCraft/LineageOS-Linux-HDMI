# HDMI lease CPU power guard

This is a separate, additive `hdmi-los-power` Magisk module for the pinned
XQ-DQ72 Lineage 22.2 build. Keep the existing `hdmi-los` module installed. It
does not update Xorg, Mesa, USB/Bluetooth input, the Android application, the
broker, or the graphics kernel companion. No launcher bundle changes are needed.

## Behavior

The existing broker's wake lock prevents suspend during a Linux lease, but does
not prevent Qualcomm's separate display-off CPU policy. The installed kalama
profile applies a 940 MHz performance-cluster cap, two-core limit, and zero
available prime cores when the phone's panel turns off.

The unchanged vendor PowerHAL runs through a small launcher and a private
preload library. Its dynamically resolved `perf_hint` callback remembers the
real Android display state. While the Linux HDMI lease is active, display-off
CPU hints become display-on CPU hints. Android still controls the panel; this
does not report a fake interactive state to Android's framework.

The guard requests four performance cores and one prime core through a finite
vendor performance request, renewed every 400 ms. It does not set a minimum
frequency, write sysfs knobs, or bypass thermal controls. The cores remain
subject to normal DVFS and thermal availability.

A root monitor checks the existing broker every 200 ms. Only a connected,
leased external CRTC authorizes a 1.5-second CLOCK_BOOTTIME deadline in
`vendor.hdmi_los.cpu_lease`. Bad replies, timeouts, broker death, unplugging,
and module disable/removal revoke it. Monitor death or a hang expires it without
renewal. The PowerHAL worker checks every 100 ms; on expiry it releases its
core request and replays the original Android display-off request if the phone
is still off. If the phone is awake, it only releases the HDMI core request.
Suspended time counts toward the deadline.

Core requests additionally expire in one second if PowerHAL dies. Its existing
init/Binder restart lifecycle must restore the framework's current display state;
the guard does not replace that lifecycle or claim recovery from a stopped PerfHAL.

## Packaging and boot gate

All C artifacts are built on `root@192.168.104.201` with NDK 29/API 35. The
manifest hashes source and binary artifacts. The ZIP privately includes an
identical stock PowerHAL, not a modified Qualcomm library. Do not commit or
publish the vendor binary or the generated ZIP.

The runtime checksum list excludes the installer-only `customize.sh`, which
Magisk deletes after installation. Packaging simulates the installer's cleanup
and verifies both checksums and complete coverage of all retained files.
The initial a90311d package remained inactive because its boot checksum list
included that deleted script. Live inspection confirmed stock PowerHAL was
unchanged; the corrected 0.1.1 package supersedes it without rebuilding the
native components.

The 0.1.1 boot gate and preload activated successfully on the phone. Live
inspection found startup retries using zero-initialized OFF metadata for ON
before any OFF event arrived. A same-state native probe, with the phone awake
and HDMI disconnected, returned handle 99 for an ON request with type -1.
Version 0.1.2 caches ON and OFF parameters separately, with type -1 defaults
for a synthetic request before its first framework event. The host fixture
now rejects invalid display-hint types and exercises an initial ON failure
before the first OFF event, followed by successful recovery without repeats.

With the installed 0.1.1 initialized and a 4K30 lease active, both awake and
Dozing workloads reached 2.803 GHz on the performance cluster. The display-off
940.8 MHz cap remained absent. However, the phone's OFF transition reset
minimum cores from 4/1 to 3/0 while the guard continued renewing a positive
vendor handle. A fresh overlapping probe also left hardware minimums unchanged,
consistent with a retained cached vote rather than absence of the request.
Version 0.1.3 retires its own core vote before each display-policy submission,
then acquires a new vote afterwards. A fixture reproduces a hardware reset
with cached votes intact and checks renewal through repeated ON/OFF events.
This ordering change still needs live verification after manual installation;
the existing version's frequency result is not proof of persistent core floors.

Installation and every boot check exact ROM properties and hashes of PowerHAL,
both performance libraries, and the three CPU resource profiles. Before
PowerHAL starts, the gate manually bind-mounts a read-only launcher over its
executable. Magisk adds only the private library and stock service under new
vendor filenames. Gate failure leaves the existing stock service untouched.
The existing HDMI module's manual mounts remain separate. Magisk 29's mount
implementation mirrors existing entries from their live paths, preserving
those earlier bind mounts when adding new filenames.

The library intercepts explicit-handle `dlsym` only for `perf_hint` from `libqti-perfd-client.so`;
ordinary preloading of `perf_hint` alone would miss the HAL's explicit-handle
lookup. Bionic's versioned real `dlsym` resolves all other symbols through an
enforced tail call, retaining the original caller's namespace/RTLD_NEXT semantics.
The extra SELinux rules permit reading the lease property and executing the
unchanged stock service in the same PowerHAL domain.

Magisk boot ordering and mount behavior references:
[developer guide](https://topjohnwu.github.io/Magisk/guides.html#boot-scripts),
[Magisk 29 module.cpp](https://github.com/topjohnwu/Magisk/blob/v29.0/native/src/core/module.cpp),
[installer cleanup](https://github.com/topjohnwu/Magisk/blob/v29.0/scripts/util_functions.sh#L687).

## Validation and deployment

Host ASan/UBSan fixtures exercise actual dynamic loading and the real guard
and monitor: hint forwarding, sleeping/awake unplug, broker timeout/closure,
fragmented/truncated stream replies, wrong opcode/version, Android-only mirror,
no active mode, monitor death and expiry, invalid deadlines, rejected core
requests, failed hint retry, and normal exit. These tests model the vendor API; they do not
establish the installed vendor policy's physical behavior.

A read-only native phone probe verified Bionic interception against a synthetic
client, without calling the real performance service. The real broker query
also correctly reported no active lease with HDMI disconnected. PowerHAL has
not been restarted or modified during preparation.

Install the prepared ZIP manually in Magisk and reboot with HDMI disconnected.
Then verify the gate, guard load, and monitor before arming the normal HDMI
agent. With the lease active, compare awake and phone-panel-off CPU policies,
core_ctl state, scaling under workload, and thermal ceilings. Finally unplug
while the phone remains off and verify Android's original cap/core policy
returns with no new screen event. Also check an ordinary Android mirror without
a Linux lease; it should retain normal Android policy.

Logs: `/data/adb/hdmi-los-power/gate.log`, `monitor.log`, and Android logcat tag
`HdmiCpuGuard`. Disable/remove **only** `hdmi-los-power` and reboot to restore
the original service. Disabling it during a lease stops monitor renewal and
restores policy through expiry; reboot removes the boot-time launcher mount.

Physical screen-off, core-floor arbitration, Binder reconnection and boot
activation remain pending manual installation and live testing. No improvement
in Maps or display cadence is claimed by the CPU guard fixtures.

See [live October 7 results](experiments/CPU-POWER-20261007.md): the initialized
0.1.1 preserved the normal frequency ceiling while dozing and restored Android
policy after unplug. It lost its minimum-core vote on the OFF transition.
The 0.1.3 core-vote correction still awaits manual installation and live testing.
