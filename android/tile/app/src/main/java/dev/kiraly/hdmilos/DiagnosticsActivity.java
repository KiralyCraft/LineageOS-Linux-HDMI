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
                    + "Connector / CRTC / plane: " + status.connector() + " / "
                    + status.crtc() + " / " + status.plane() + "\n\n"
                    + status.detail() + "\n\n"
                    + "Runtime log: /data/adb/hdmi-los/logs/broker.log\n"
                    + "Chroot log: /run/hdmi-los/agent.log";
            runOnUiThread(() -> text.setText(report));
        });
    }
}
