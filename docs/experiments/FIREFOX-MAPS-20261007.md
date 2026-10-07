# Firefox resize corruption and Maps stutter

The user accepts E for continued use following manual 4K30 testing: the earlier
titlebar fault could not be reproduced manually, and interaction felt better.
Firefox corrupted during circular corner resizing; Konsole did not. Maps
panning remained stuttery. The earlier automated titlebar failures remain valid
records; this manual acceptance does not establish that their cause was fixed.

## Live runtime audit

The subsequent read-only audit found a continuous **3840x2160@60** session from
`hdmi-termux-vblank-min-coexist-usb-bt-bcdef-controls-20261005`. This differs from
the resize-cache bundle last prepared for manual testing. Do not associate the
current runtime identity with the earlier manual report without a matched test.

| Component | Verified current state |
|---|---|
| Mesa | `170a56fa943cc475472d170157efe58d7dd9b8c1` |
| Loaded libgallium SHA256 | `71de3ba44d40e56e784759e34e1ef3eb877a5e30be2b202b2905495cfd45a491` |
| Mesa resize-cache marker | Absent |
| Xorg E implementation | ABI 1, general eligible-copy blits; not the restricted ABI 2 path |
| Desktop acceleration | Glamor on FD740; TearFree enabled |
| Completion/backend | Asynchronous TearFree and F kernel presenter |
| E synchronous diagnostic | Not selected |
| Timeout | Continuous broker/agent mode |
| Firefox | Arch `154.0-1`, private EGL/Gallium loaded, KGSL device open |
| Konsole | Both observed processes have no Mesa libraries loaded |
| GPU governor | `msm-adreno-tz` |

Firefox's preferences request WebRender, forced X11 EGL and acceleration.
Library/device observations establish use of the GPU stack, but are not a
complete `about:support` report. The Firefox/Konsole distinction points toward
the accelerated client path; it does not prove the corruption originates in
Firefox itself or exclude Xorg's E copy path.

## Resize findings

In the current Mesa pipeline, generation creation and destruction occur while
holding `p->lock`. Creation waits for X replies and imports three GPU images.
The same lock protects the event worker's slot and completion processing.
The session log contains a size-changing generation sequence reaching at
least 143; it lacks per-process attribution, so do not treat that entire count
as a separately measured Firefox-only workload.

Commit `e04c1852d386046548676e7004cc9855b3e6bd66` already addresses that
serialization: allocation/import use a separate X connection outside the
event-processing lock, destruction uses the retirement worker, and the cache
retains up to three sizes. The generation count can still grow during continuous
resizing through many distinct sizes. Repeated-size synthetic tests do not
establish performance during a real resize storm.

This is a source-confirmed event-processing stall opportunity in the loaded
version. There is no matched Firefox test proving it causes the visible
corruption or that the newer version fixes it.

Mozilla's current EGL compositor uses buffer age and damage regions for partial
presentation. See [RenderCompositorEGL](https://searchfox.org/firefox-main/source/gfx/webrender_bindings/RenderCompositorEGL.cpp#1179).
That makes resize-time buffer contents and age a useful correctness target.
The inspected Mozilla source is the current upstream snapshot, not the exact
source revision of the installed Firefox build. Whether this particular window
uses that partial-redraw path still needs runtime confirmation. No browser
preference or hardware-acceleration setting was changed.

## Prepared Mesa-only comparison

The folder is:

```text
~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-bcdef-mesa-resize-20261007
```

It combines the current controls bundle with the complete matched Mesa directory
from the verified resize build. Its actual changed compiled artifact is only
`lib/mesa/libgallium-26.2.0-devel.so`. Xorg, E ABI 1, the launcher, native input
bridge, broker and kernel companion are byte-for-byte unchanged. This isolates
the Mesa version from changes to E. All 260 bundle checksums pass; 252 base and
273 donor checksums were verified before assembly. The comparison has not run
on the physical display and is not an accepted corruption fix.

`build-support/package-mesa-resize-comparison.py` reproduces the assembly and
rejects an existing output. `build-info.json`, `control-info.json`, Mesa patches
and validation files identify the mixture explicitly. Older copied acceptance
documents describe their original builds, not this new combination.

When a session switch is authorized, launch:

```sh
cd ~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-bcdef-mesa-resize-20261007
./run-agent.sh --candidate BCDEF --no-timeout --capture none
```

The user's required unplug/arm/reconnect procedure applies. No new Magisk install
or compilation is needed. The current continuous desktop and Firefox were left
running during this investigation.

## Maps timing readiness

`tests/graphics-contract/browser-passive-timing.py` is ready for a user-driven,
30-second Maps pan. It inherits the running Firefox's display/authentication and
collects:

- Xorg request turnaround through its own X connection.
- CPU use and sampled renderer/compositor/content-process wait channels.
- Scheduler counters and GPU frequency/load/residency telemetry.

It neither resizes windows nor reads pixels, attaches a debugger, reads DRM
events, or changes GPU policy. Samples remain in memory during measurement and
are saved privately afterwards. A three-second uncontrolled collector check
returned 30 samples with 21 selected threads and available residency telemetry.
That check is **not** a Maps performance result.

Example capture, started when the user announces the activity:

```sh
sudo -n python3 tests/graphics-contract/browser-passive-timing.py \
  .local/firefox-maps-20261007/maps-pan.json --label maps-pan --seconds 30
```

XSync measures server request processing, not displayed FPS or input-to-display
latency. Wait channels are sampled states; GPU counters include Android and other
clients. The fixed initial thread inventory can miss later-created threads.
The workload label does not itself verify that panning occurred. A Maps result
and a corruption reproduction remain pending.
