# Live glxgears resize replay: event backlog

The Xorg 26 BCDEF bundle, Mesa 557306b5c4, was tested at 3840x2160@30.
The user independently confirmed the first screenshot test visibly reproduced
the resize replay. OpenJK did not reproduce its earlier rubberbanding in this
session; that remains a separate user observation.

The corrected workload sends only changed sizes at 20 Hz: grow, shrink, then
oscillate between 640x480 and 1280x960, followed by ten seconds at 960x720.
X root screenshots are taken at 5 Hz and buffered in memory. Subsequent captures
and trace files are under `/tmp/hdmi-resize-20261007`, on the phone's zram-backed
RAM disk. They disappear when that RAM filesystem is recreated. No full new
capture dump is copied to the SD card.

A test-only LD_PRELOAD library records XNextEvent, XPending, glViewport and
glXSwapBuffers timestamps in memory and writes the trace at child termination.
The actual application requests obsolete viewport dimensions after X geometry
is already stable. Source glxgears 9.0.0 stops draining events at each Expose,
and this workload produces two Expose events per changed size.

| Control | Maximum pending X events | Last obsolete viewport after stop |
| --- | ---: | ---: |
| Installed glxgears, interval one, captures | 180 | 4.266 s |
| Installed glxgears, interval one, no captures | 188 | approximately 4.0 s |
| Installed glxgears, interval zero, captures | 11 | 0.002 s |
| Source-built stock glxgears, interval one | 185 | 4.433 s |
| Same source/compiler, drain events before drawing, interval one | 16 | None |

The source controls differ only by removal of `else if (op == DRAW) break;`
from the inner event loop. Every ConfigureNotify is still handled; rendering,
viewport calls, synchronization, Mesa and Xorg are unchanged. The draining
control reaches its final requested viewport about 150 ms after the stop and
does not replay intermediate dimensions. Later screenshots show consistent
geometry with rotating gears. This control was not installed over glxgears.
Interval zero is a diagnostic, not a proposal to disable synchronized output.

This identifies the event backlog as the cause of this particular replay.
A driver cannot legally replace an application's viewport commands with current
window geometry. It does not establish the cause of browser resize corruption,
all desktop sluggishness, or physical 4K60 performance.

A separate configured-geometry pattern test completed 250 submitted/250
completed frames, 250 application-context resolves, and zero worker copies.
Eleven geometry-stable settled screenshots passed all 55 sampled interior color
checks. The three cached generations required no eviction. Aggregate resolve
submission averaged 1.98 ms and fence export 0.64 ms; three allocation/import
calls totaled 22.04 ms. These are CPU-side recorded durations, not GPU execution
time or physical cadence, and the test reuses only three sizes. It does not
characterize allocation churn across hundreds of unique sizes.

The next graphics work should use a consumer that drains resize events and
changes frame identifiers, retaining patterned pixel/lifetime tests. Investigate
real CPU submission and unique-size allocation costs separately. The user
requested connected-session restart packaging before proceeding with that work.
