package com.tvhome;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** adb-only (DUMP) trigger for Wake, to test the wake-up without waiting for Core. */
public class WakeTestReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String music = intent.getStringExtra("music");
        int volume = intent.getIntExtra("volume", 20);
        Context app = context.getApplicationContext();
        new Thread(() -> Wake.run(app, music != null ? music : "", volume, ""), "wake-test").start();
    }
}
