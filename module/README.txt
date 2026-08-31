This directory is an input template.  make zip fills bin/, apk/, vendor/, and
patched-checksums.list using artifacts compiled on root@192.168.104.201.

The module does not use Magic Mount.  post-fs-data.sh verifies the exact
LineageOS build and the original vendor SHA-256 values before three bind mounts.
On any mismatch it mounts nothing and does not start the broker.

