package dev.kiraly.hdmilos;

import android.app.Activity;
import android.os.Bundle;
import android.text.method.ScrollingMovementMethod;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class DiagnosticsActivity extends Activity {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private TextView text;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(padding, padding, padding, padding);
        layout.setGravity(Gravity.TOP);

        text = new TextView(this);
        text.setTextSize(17);
        text.setMovementMethod(new ScrollingMovementMethod());
        layout.addView(text, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        LinearLayout presets = new LinearLayout(this);
        presets.setOrientation(LinearLayout.HORIZONTAL);
        addPresetButton(presets, "1080p60 (default)", 1920, 1080, 60000);
        addPresetButton(presets, "Native (experimental)", 0, 0, 0);
        addPresetButton(presets, "4K60 (experimental)", 3840, 2160, 60000);
        layout.addView(presets, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        Button refresh = new Button(this);
        refresh.setText("Refresh");
        refresh.setOnClickListener(view -> refresh());
        layout.addView(refresh, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(layout);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }

    private void refresh() {
        text.setText("Reading broker state…");
        executor.execute(() -> {
            BrokerClient.Status status = BrokerClient.status();
            String report = "Profile: " + BuildConfig.HDMI_PROFILE + "\n"
                    + "LineageOS: " + BuildConfig.HDMI_LINEAGE + "\n"
                    + "State: " + status.state() + "\n"
                    + "Result: " + status.result() + "\n"
                    + "Remaining: " + status.remaining() + " seconds\n"
                    + "Flags: 0x" + Integer.toHexString(status.flags()) + "\n"
                    + "Configured mode: " + formatMode(status.requestedWidth(),
                    status.requestedHeight(), status.requestedRefreshMilliHz()) + "\n"
                    + "Android active mode: " + formatMode(status.activeWidth(),
                    status.activeHeight(), status.activeRefreshMilliHz()) + "\n"
                    + "Connector / CRTC / plane: " + status.connector() + " / "
                    + status.crtc() + " / " + status.plane() + "\n\n"
                    + status.detail() + "\n\n"
                    + "Runtime log: /data/adb/hdmi-los/logs/broker.log\n"
                    + "Chroot log: /run/hdmi-los/agent.log";
            runOnUiThread(() -> text.setText(report));
        });
    }

    private void addPresetButton(LinearLayout parent, String label, int width, int height,
                                 int refreshMilliHz) {
        Button button = new Button(this);
        button.setText(label);
        button.setOnClickListener(view -> executor.execute(() -> {
            BrokerClient.Status status = BrokerClient.setMode(width, height, refreshMilliHz);
            runOnUiThread(() -> text.setText(status.detail()));
            refresh();
        }));
        parent.addView(button, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1));
    }

    private static String formatMode(int width, int height, int refreshMilliHz) {
        if (width == 0 && height == 0 && refreshMilliHz == 0) return "native/automatic";
        if (width == 0 || height == 0) return "unavailable";
        return width + "×" + height + " @ " + (refreshMilliHz / 1000.0) + " Hz";
    }
}
