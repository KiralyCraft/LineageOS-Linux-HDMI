package dev.kiraly.hdmilos;

import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class HdmiTileService extends TileService {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    @Override
    public void onStartListening() {
        super.onStartListening();
        refresh();
    }

    @Override
    public void onClick() {
        super.onClick();
        Runnable action = () -> executor.execute(() -> apply(BrokerClient.toggle()));
        if (isLocked()) unlockAndRun(action); else action.run();
    }

    @Override
    public void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }

    private void refresh() {
        executor.execute(() -> apply(BrokerClient.status()));
    }

    private void apply(BrokerClient.Status status) {
        Tile tile = getQsTile();
        if (tile == null) return;
        String subtitle;
        switch (status.state()) {
            case BrokerClient.STATE_LEASED -> {
                tile.setState(Tile.STATE_ACTIVE);
                subtitle = "Xorg · " + status.remaining() + "s";
            }
            case BrokerClient.STATE_DRAINING, BrokerClient.STATE_STARTING_X -> {
                tile.setState(Tile.STATE_ACTIVE);
                subtitle = "Starting Xorg";
            }
            case BrokerClient.STATE_RESTORING -> {
                tile.setState(Tile.STATE_INACTIVE);
                subtitle = "Restoring Android";
            }
            case BrokerClient.STATE_AGENT_READY -> {
                tile.setState(Tile.STATE_INACTIVE);
                subtitle = "Ready";
            }
            case BrokerClient.STATE_ANDROID -> {
                tile.setState(Tile.STATE_INACTIVE);
                subtitle = "Android";
            }
            default -> {
                tile.setState(Tile.STATE_UNAVAILABLE);
                subtitle = "Unavailable";
            }
        }
        tile.setLabel("HDMI Xorg");
        tile.setSubtitle(subtitle);
        tile.setContentDescription(status.detail());
        tile.updateTile();
    }
}

