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

Version 0.1.4 keeps the vendor's normal interactive CPU policy during the
lease. This removes the panel-off frequency/core-availability limits without
forcing a minimum frequency or keeping idle cores permanently active. The
scheduler can use the performance cores and Cortex-X3 as demand rises. Thermal
controls remain authoritative. The unsuccessful minimum-core requests in
0.1.1–0.1.3 have been removed; a positive perf handle did not prove they applied.

A root monitor checks the existing broker every 200 ms. Only a connected,
leased external CRTC authorizes a 1.5-second CLOCK_BOOTTIME deadline in
`vendor.hdmi_los.cpu_lease`. Bad replies, timeouts, broker death, unplugging,
and module disable/removal revoke it. Monitor death or a hang expires it without
renewal. The PowerHAL worker checks every 100 ms; on expiry it replays the
original Android display-off request if the phone is still off. If the phone is awake, its normal interactive policy stays in place.
Suspended time counts toward the deadline.

PowerHAL's existing init/Binder restart lifecycle must restore the framework's
current display state; the guard does not replace that lifecycle or claim
recovery from a stopped PerfHAL.

### Termux, chroot and CPU affinity

The acceptance criterion is access to all CPUs, including CPU 7 (Cortex-X3),
under workload. An idle core count or a single `sched_getaffinity()` sample is
not sufficient to establish that a process is permanently restricted.

Live inspection of the user's `Termux -> su -> mountChroot.sh -> su - kiraly`
chain found no affinity writes in the mount script. The chroot shells and Xorg
were in the root cpuset with `Cpus_allowed_list: 0-7`. Termux's foreground
cpuset and Termux:X11's current restricted cpuset both allowed `0-7`; all 13
Termux:X11 app threads had that mask. The background cpuset allows `0-2`, so
these are observed memberships, not a guarantee for every future Android app
state. Do not globally widen Android's background cpuset.

In a dozing HDMI session, `sched_getaffinity()` temporarily returned
`0,1,2,3,4,6` even for PID 1 while `/proc/PID/status` continued to report `0-7`.
Workers with inherited affinity subsequently ran on every CPU, including 7,
without any affinity changes. Treat the queried mask as an instantaneous
scheduler result; do not save it into `taskset` and inadvertently pin out cores.
The pinned kernel intersects the stored task mask with active CPUs and calls a
vendor hook in
[`sched_getaffinity()`](https://github.com/LineageOS/android_kernel_sony_sm8550/blob/d00ba216ccda5d4fcc0d864729ae69d5b63d860c/kernel/sched/core.c#L7803).
The exact vendor hook responsible for the additional filtering was not traced.

Ordinary Termux:X11 with the phone awake uses Android's existing interactive
policy; no HDMI lease is required. This module does not grant a screen-off
exception merely because the Termux:X11 app remains installed or running.
The native Termux:X11 server and a standalone GUI workload still need their
own live acceptance test when that session is available.

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

Both 0.1.1 and 0.1.3 preserved the normal performance-cluster ceiling during a
4K30 HDMI lease while Android reported Dozing. Version 0.1.3 initialized on
fresh boot without a manual screen cycle, but its revised minimum-core vote
still fell back to 3/0 after screen-off. Targeted tracing identified
PERFD-SERVER as the writer; the precise trigger remains unproven. A separate
vendor Doze hint exists, but is not evidence that it caused the reset and is
left untouched.

The decisive 0.1.3 workload reached 2803.2 MHz on the performance cluster;
all four performance cores became active. The X3 was sampled active at
3187.2 MHz. A separate placement test observed inherited-affinity workers on
CPUs 0 through 7 while Android remained Dozing. Version 0.1.4 retains the
working display-policy selection and removes the unreliable forced minimums.
Its package must still pass live acceptance after manual installation.

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
no active mode, monitor death and expiry, invalid deadlines, no direct
core/frequency votes, unchanged Doze hints,
failed hint retry, and normal exit. These tests model the vendor API; they do not
establish the installed vendor policy's physical behavior.

A read-only native phone probe verified Bionic interception against a synthetic
client, without calling the real performance service. The real broker query
also correctly reported no active lease with HDMI disconnected. PowerHAL was
not restarted by the validation tools; installed versions changed
through user-managed installation and reboot.

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

See [live October 7 results](experiments/CPU-POWER-20261007.md) for versioned
evidence. Screen-off frequency preservation and unplug restoration were
observed with 0.1.1; all-CPU placement during Doze was observed with 0.1.3.
Version 0.1.4 remains pending manual installation and live workload/unplug
checks. Recovery across service crashes, thermal stress and suspend/resume
has not been established by these live tests. No Maps or display-cadence
improvement is claimed.
