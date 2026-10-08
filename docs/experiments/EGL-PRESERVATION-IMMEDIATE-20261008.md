# Immediate EGL preservation checks, 2026-10-08

Mesa `eff5c22036c93e9b0c5fc9a7525c46f636e61937` fixes the remaining startup
gap in R13 (`08a3ec464`). R13 recorded a copy-back source but advanced the
render-buffer ring. `dri3_find_back()` clears that source when selecting an
unallocated or busy slot, so newly selected storage could lose the preceding
frame. After enough partial repaints, every tile had been overwritten and the
older eight settled screenshots passed. Those checks missed the startup gap.

R14 follows the ordinary loader: a preserved swap leaves the current private
render buffer selected. Buffer acquisition waits until its producer read is
complete. The independently owned shared presentation images still rotate and
retain their native-fence and Present COMPLETE/IDLE retirement requirements.
Ordinary swaps and buffer-age clients keep their existing selection policy.
This change adds neither CPU readback nor a copy to the production path.

The new `--preserved --check-next-back` test reads sixteen tile centers after
each swap and compares them to an independent CPU scene. It checks the next
back buffer immediately, including startup. Readback synchronizes this diagnostic
and is therefore a correctness check, not a performance measurement or proof
of cross-process/physical presentation.

| Check | R13 | R14 |
| --- | ---: | ---: |
| Fixed-size burst, immediate tile checks | 223/240 | 240/240 |
| Resize, after establishing a defined scene | 16/24 | 24/24 |
| Resize without buffer-age queries | not used as a control | 24/24 |
| Fixed-size burst without buffer-age queries | not used as a control | 240/240 |
| Preserved burst, settled root images | earlier 8/8 | 8/8 |
| Ordinary buffer-age burst, settled root images | earlier 8/8 | 8/8 |
| Preserved burst without age queries, settled root images | earlier 8/8 | 8/8 |

The resize contract test queries EGL's dimensions, performs an unchecked resize
swap, then repaints the entire new scene before checking subsequent preservation.
This is necessary because buffer contents during a native resize are not a valid
preservation oracle. See EGL 1.5 sections 2.2.2.2 and 3.10.1.1 and the resize
clarification in [EXT_swap_buffers_with_damage](https://registry.khronos.org/EGL/extensions/EXT/EGL_EXT_swap_buffers_with_damage.txt).
The initial immediate-resize variant, which omitted that step, is retained in
RAM as a discarded contract check. It must not be cited as a driver violation.
This exclusion does not establish that transient browser resize clipping is
acceptable or solved; that remains a separate responsiveness investigation.

FD740 regression checks passed 24/24 each for ordinary EGL full repaint,
EGL swap-with-damage resizing, and GLX with four-sample MSAA plus swap-interval
transitions. The matched Mesa frontend, Gallium, EGL, GLX and GBM stack was built
on `root@192.168.104.201`, with every tracked source blob and artifact verified.
The production cache fixture passed with address/undefined/leak sanitizers.
Builder tests requiring KGSL skipped there because no device was available.

All device graphics checks used the existing leased 3840x2160@30 Xorg desktop.
No Magisk or kernel change was made. Raw results and screenshots remain in RAM;
the accompanying JSON retains counts and hashes. Physical 4K60 and general
Firefox/Maps/game pacing are not established by these preservation tests.
