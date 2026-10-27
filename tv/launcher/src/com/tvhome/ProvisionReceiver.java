package com.tvhome;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * Receives Core's URL and this TV's scoped token from tv/provision.sh
 * ({@code --ez clear true} unlinks).
 * Guarded by the DUMP permission in the manifest, which adb's shell holds
 * and ordinary apps can't get, so nothing else on the TV can repoint it.
 */
public class ProvisionReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent.getBooleanExtra("clear", false)) {
            Jarvis.provision(context, "", "");
            setResultData("unlinked");
            return;
        }
        String core = intent.getStringExtra("core");
        String token = intent.getStringExtra("token");
        if (core == null || token == null || !token.startsWith("jc_")) {
            setResultData("need --es core http://host:8000 --es token jc_...");
            return;
        }
        Jarvis.provision(context, core, token);
        Log.i("TvHomeJarvis", "provisioned against " + core);
        setResultData("linked to " + core);
        RemoteBridgeService.start(context);
        PendingResult result = goAsync();
        new Thread(() -> {
            try {
                Jarvis.refresh(context);
            } catch (Exception e) {
                Log.w("TvHomeJarvis", "first refresh failed", e);
            } finally {
                result.finish();
            }
        }).start();
    }
}
