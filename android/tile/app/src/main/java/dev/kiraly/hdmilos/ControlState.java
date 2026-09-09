package dev.kiraly.hdmilos;

/** Pure presentation policy shared by the screen and its state tests. */
record ControlState(String title, String badge, String action, boolean canAct,
                    boolean stop, boolean canSetMode, String guidance) {
    static ControlState from(BrokerClient.Status status) {
        boolean armed = (status.flags() & BrokerClient.FLAG_ARMED) != 0
                || status.state() == BrokerClient.STATE_ARMED
                || status.state() == BrokerClient.STATE_WAITING;
        if (status.result() != 0 || status.state() == BrokerClient.STATE_UNAVAILABLE
                || status.state() == BrokerClient.STATE_ERROR) {
            return new ControlState("Connection needs attention", "CHECK CONNECTION",
                    "Arm HDMI Xorg", false, false, false,
                    "Check the module and broker. Refresh to retry; details are below.");
        }
        if (status.state() == BrokerClient.STATE_LEASED) {
            return new ControlState("Linux is on HDMI", "LIVE", "Return to Android",
                    true, true, false, "Your Linux desktop owns the external display. "
                    + "Returning to Android stops this Xorg session.");
        }
        if (armed) {
            boolean replug = (status.flags() & BrokerClient.FLAG_REPLUG_REQUIRED) != 0;
            return new ControlState(replug ? "Unplug, then reconnect" : "Ready for HDMI",
                    "ARMED", "Disarm HDMI Xorg", true, true, false,
                    replug ? "Unplug HDMI first so the selected mode can be applied safely. "
                    + "Reconnect and accept Android's Mirror prompt."
                    : "Start run-agent.sh in the chroot, connect HDMI, then accept "
                    + "Android's Mirror prompt. Xorg starts when the display is ready.");
        }
        if (status.state() == BrokerClient.STATE_ANDROID
                || status.state() == BrokerClient.STATE_AGENT_READY) {
            return new ControlState("Android is in control", "STANDBY", "Arm HDMI Xorg",
                    true, false, true,
                    status.state() == BrokerClient.STATE_AGENT_READY
                    ? "The chroot agent is ready. Arm HDMI Xorg before connecting your display."
                    : "Choose a mode and arm HDMI Xorg. You can start run-agent.sh "
                    + "in the chroot before or after arming.");
        }
        return new ControlState("Display handoff in progress", "PLEASE WAIT", "Please wait",
                false, false, false, "The broker is preparing or restoring the display. "
                + "Status updates automatically.");
    }
}
