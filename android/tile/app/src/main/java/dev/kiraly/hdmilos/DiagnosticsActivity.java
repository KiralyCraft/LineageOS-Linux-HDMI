package dev.kiraly.hdmilos;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.graphics.Insets;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.service.quicksettings.TileService;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.widget.Button;
import android.widget.TextView;

import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Supplier;

public final class DiagnosticsActivity extends Activity {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Runnable poll = () -> request(BrokerClient::status, false, null);
    private BrokerClient.Status status;
    private boolean resumed;
    private boolean inFlight;
    private Button action;
    private Button refresh;
    private Button[] modes;
    private TextView notice;
    private TextView diagnostics;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setContentView(R.layout.activity_control);
        View root = findViewById(R.id.root);
        root.setOnApplyWindowInsetsListener((view, windowInsets) -> {
            Insets bars = windowInsets.getInsets(WindowInsets.Type.systemBars()
                    | WindowInsets.Type.displayCutout());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return windowInsets;
        });
        root.requestApplyInsets();
        getWindow().getInsetsController().setSystemBarsAppearance(0,
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
        action = findViewById(R.id.action);
        refresh = findViewById(R.id.refresh);
        notice = findViewById(R.id.notice);
        diagnostics = findViewById(R.id.diagnostics);
        modes = new Button[] {findViewById(R.id.mode_native), findViewById(R.id.mode_1080),
                findViewById(R.id.mode_4k)};
        modes[0].setOnClickListener(v -> setMode(0, 0, 0));
        modes[1].setOnClickListener(v -> setMode(1920, 1080, 60000));
        modes[2].setOnClickListener(v -> setMode(3840, 2160, 60000));
        action.setOnClickListener(v -> act());
        refresh.setOnClickListener(v -> request(BrokerClient::status, false, null));
        Button details = findViewById(R.id.details_toggle);
        details.setOnClickListener(v -> {
            View panel = findViewById(R.id.details_panel);
            boolean show = panel.getVisibility() != View.VISIBLE;
            panel.setVisibility(show ? View.VISIBLE : View.GONE);
            details.setText(show ? "Hide diagnostics" : "Show diagnostics");
        });
        findViewById(R.id.copy).setOnClickListener(v -> {
            ClipboardManager clipboard = getSystemService(ClipboardManager.class);
            clipboard.setPrimaryClip(ClipData.newPlainText("HDMI Xorg diagnostics", report()));
            showNotice("Diagnostics copied.", false);
        });
        if (saved != null && saved.containsKey("notice")) {
            showNotice(saved.getString("notice"), saved.getBoolean("noticeError"));
        }
        render();
    }

    @Override
    protected void onResume() {
        super.onResume();
        resumed = true;
        request(BrokerClient::status, false, null);
    }

    @Override
    protected void onPause() {
        resumed = false;
        main.removeCallbacks(poll);
        super.onPause();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        if (notice.getVisibility() == View.VISIBLE) {
            out.putString("notice", notice.getText().toString());
            out.putBoolean("noticeError", Boolean.TRUE.equals(notice.getTag()));
        }
    }

    @Override
    protected void onDestroy() {
        main.removeCallbacks(poll);
        executor.shutdownNow();
        super.onDestroy();
    }

    private void act() {
        if (status == null || inFlight) return;
        ControlState state = ControlState.from(status);
        if (!state.canAct()) return;
        if (status.state() == BrokerClient.STATE_LEASED) {
            new AlertDialog.Builder(this).setTitle("Return HDMI to Android?")
                    .setMessage("This stops the current Xorg session and disarms HDMI takeover. "
                            + "Save your Linux work first.")
                    .setNegativeButton("Keep Linux", null)
                    .setPositiveButton("Return to Android", (dialog, which) ->
                            request(BrokerClient::disarm, true, "HDMI Xorg stopped and disarmed."))
                    .show();
        } else if (state.stop()) {
            request(BrokerClient::disarm, true, "HDMI Xorg disarmed.");
        } else {
            // Explicit operations prevent a stale screen from toggling a session
            // started from the tile back off, or rearming one just stopped.
            request(BrokerClient::arm, true, "HDMI Xorg armed.");
        }
    }

    private void setMode(int width, int height, int refreshMilliHz) {
        if (status == null || !ControlState.from(status).canSetMode()) return;
        request(() -> BrokerClient.setMode(width, height, refreshMilliHz), true,
                "Mode saved. Tap Arm HDMI Xorg when you are ready.");
    }

    private void request(Supplier<BrokerClient.Status> operation, boolean command,
                         String success) {
        if (inFlight || isDestroyed() || !resumed) return;
        main.removeCallbacks(poll);
        inFlight = true;
        render();
        executor.execute(() -> {
            BrokerClient.Status reply = operation.get();
            main.post(() -> {
                if (isDestroyed()) return;
                inFlight = false;
                status = reply;
                if (command) {
                    showNotice(reply.result() == 0 ? "Last action: " + success
                            : "Request failed (" + reply.result() + "): " + reply.detail(),
                            reply.result() != 0);
                    TileService.requestListeningState(this,
                            new ComponentName(this, HdmiTileService.class));
                }
                render();
                if (resumed) main.postDelayed(poll, 2000);
            });
        });
    }

    private void render() {
        ControlState state = status == null ? new ControlState("Connecting to broker",
                "CONNECTING", "Arm HDMI Xorg", false, false, false,
                "Reading the module's live display state.") : ControlState.from(status);
        text(R.id.state_title, state.title());
        text(R.id.state_badge, state.badge());
        text(R.id.guidance, state.guidance());
        action.setText(state.action());
        action.setEnabled(state.canAct() && !inFlight);
        refresh.setEnabled(!inFlight);
        findViewById(R.id.progress).setVisibility(inFlight ? View.VISIBLE : View.INVISIBLE);
        if (status != null) {
            text(R.id.connection, status.result() != 0 ? "HDMI connection unknown"
                    : (status.flags() & BrokerClient.FLAG_CONNECTED) != 0
                    ? "HDMI connected" : "HDMI not connected");
            text(R.id.selected_mode, "Selected: " + formatMode(status.requestedWidth(),
                    status.requestedHeight(), status.requestedRefreshMilliHz()));
            text(R.id.active_mode, "Android output: " + formatMode(status.activeWidth(),
                    status.activeHeight(), status.activeRefreshMilliHz(), false));
            text(R.id.broker_detail, status.detail());
        }
        for (int i = 0; i < modes.length; i++) {
            boolean selected = status != null && switch (i) {
                case 0 -> status.requestedWidth() == 0 && status.requestedHeight() == 0
                        && status.requestedRefreshMilliHz() == 0;
                case 1 -> status.requestedWidth() == 1920 && status.requestedHeight() == 1080
                        && status.requestedRefreshMilliHz() == 60000;
                default -> status.requestedWidth() == 3840 && status.requestedHeight() == 2160
                        && status.requestedRefreshMilliHz() == 60000;
            };
            modes[i].setSelected(selected);
            modes[i].setStateDescription(selected ? "Selected" : "Not selected");
            modes[i].setEnabled(state.canSetMode() && !inFlight);
        }
        diagnostics.setText(report());
    }

    private void showNotice(String message, boolean error) {
        notice.setVisibility(View.VISIBLE);
        notice.setTag(error);
        notice.setText(message);
        notice.setTextColor(getColor(error ? R.color.warning : R.color.accent));
    }

    private void text(int id, String value) {
        TextView view = findViewById(id);
        if (!value.contentEquals(view.getText())) view.setText(value);
    }

    private String report() {
        String header = "HDMI Xorg " + BuildConfig.VERSION_NAME + "\nProfile: "
                + BuildConfig.HDMI_PROFILE + "\nLineageOS: " + BuildConfig.HDMI_LINEAGE;
        if (status == null) return header + "\nWaiting for broker.";
        return header + "\nState: " + status.state() + "  Result: " + status.result()
                + "\nFlags: 0x" + Integer.toHexString(status.flags())
                + "\nRemaining: " + status.remaining() + " seconds"
                + "\nConfigured: " + formatMode(status.requestedWidth(), status.requestedHeight(),
                        status.requestedRefreshMilliHz())
                + "\nAndroid output: " + formatMode(status.activeWidth(), status.activeHeight(),
                        status.activeRefreshMilliHz(), false)
                + "\nConnector / CRTC / plane: " + status.connector() + " / " + status.crtc()
                + " / " + status.plane() + "\n" + status.detail()
                + "\n\nBroker log: /data/adb/hdmi-los/logs/broker.log"
                + "\nChroot log: /run/hdmi-los/agent.log";
    }

    private static String formatMode(int width, int height, int refresh) {
        return formatMode(width, height, refresh, true);
    }

    private static String formatMode(int width, int height, int refresh, boolean preference) {
        if (width == 0 && height == 0 && refresh == 0) {
            return preference ? "Native / automatic" : "Not available";
        }
        if (width == 0 || height == 0) return "Not available";
        return String.format(Locale.getDefault(), "%d × %d · %.2f Hz", width, height, refresh / 1000.0);
    }
}
