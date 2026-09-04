# Qualcomm display patch series

`series` is the default Qualcomm composer patch set. The build and port tools
apply only the files named there, in order.

`optional/0015-composer-lease-an-overlay-plane-for-the-xorg-cursor.patch`
must be paired with the optional Xorg `OverlayCursor` patch. It successfully
placed the cursor on a distinct leased plane, but did not fix cursor-motion
cadence: 120 Hz motion reduced rendering to 27-31 FPS and produced synchronous
plane-update stalls lasting seconds. It is excluded from `series` and retained
only as a diagnostic experiment.
