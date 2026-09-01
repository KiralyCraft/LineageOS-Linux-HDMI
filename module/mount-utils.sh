#!/system/bin/sh

# Print the per-mount VFS options for an exact mount point.  The fifth and
# sixth fields precede the optional fields in /proc/*/mountinfo.
mountinfo_options_for_target() {
  target=$1
  mountinfo=${2:-/proc/self/mountinfo}
  awk -v target="$target" '
    $5 == target { print $6; found = 1; exit }
    END { if (!found) exit 1 }
  ' "$mountinfo"
}

mount_options_have() {
  options=$1
  option=$2
  case ",$options," in
    *",$option,"*) return 0 ;;
    *) return 1 ;;
  esac
}

mount_options_allow_exec() {
  options=$1
  ! mount_options_have "$options" nosuid &&
    ! mount_options_have "$options" noexec
}

mount_options_are_read_only() {
  mount_options_have "$1" ro
}
