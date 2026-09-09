package dev.kiraly.hdmilos;

import org.junit.Test;
import static org.junit.Assert.*;

public final class ControlStateTest {
    private BrokerClient.Status status(int state, int result, int flags) {
        return new BrokerClient.Status(result, state, 0, 0, 0, 0, flags,
                0, 0, 0, 0, 0, 0, "test");
    }

    @Test public void idleCanArmWithOrWithoutAgent() {
        for (int state : new int[] {BrokerClient.STATE_ANDROID, BrokerClient.STATE_AGENT_READY}) {
            ControlState view = ControlState.from(status(state, 0, 0));
            assertTrue(view.canAct());
            assertFalse(view.stop());
            assertTrue(view.canSetMode());
            assertEquals("Arm HDMI Xorg", view.action());
        }
    }

    @Test public void armedAndWaitingDisarmWithoutChangingMode() {
        for (int state : new int[] {BrokerClient.STATE_ARMED, BrokerClient.STATE_WAITING}) {
            ControlState view = ControlState.from(status(state, 0, BrokerClient.FLAG_ARMED));
            assertTrue(view.canAct());
            assertTrue(view.stop());
            assertFalse(view.canSetMode());
            assertEquals("Disarm HDMI Xorg", view.action());
        }
    }

    @Test public void replugHasActionableGuidance() {
        ControlState view = ControlState.from(status(BrokerClient.STATE_WAITING, 0,
                BrokerClient.FLAG_ARMED | BrokerClient.FLAG_REPLUG_REQUIRED));
        assertEquals("Unplug, then reconnect", view.title());
        assertTrue(view.guidance().contains("Unplug HDMI first"));
    }

    @Test public void leasedTakesPriorityOverArmedFlag() {
        ControlState view = ControlState.from(status(BrokerClient.STATE_LEASED, 0,
                BrokerClient.FLAG_ARMED | BrokerClient.FLAG_CONTINUOUS));
        assertEquals("Return to Android", view.action());
        assertTrue(view.stop());
        assertFalse(view.canSetMode());
    }

    @Test public void errorsCannotArmOrChangeMode() {
        for (int state = 0; state <= 10; state++) {
            ControlState view = ControlState.from(status(state, -6, 0));
            assertFalse(view.canAct());
            assertFalse(view.canSetMode());
        }
    }

    @Test public void transitionalUnavailableAndUnknownStatesAreNotActionable() {
        for (int state : new int[] {1, 3, 4, 5, 7, 8, 99}) {
            ControlState view = ControlState.from(status(state, 0, 0));
            assertFalse(view.canAct());
            assertFalse(view.canSetMode());
        }
    }
}
