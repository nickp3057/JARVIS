package com.tvhome;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * adb-only (DUMP): show the TV in another theme for a few seconds without
 * touching Core's shared theme, e.g. to check Barn Door's screen:
 *   adb shell am broadcast -n com.tvhome/.PreviewReceiver --es theme barnDoor [--ei seconds 20]
 */
public class PreviewReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        long seconds = intent.getIntExtra("seconds", 20);
        Jarvis.prefs(context).edit().putString("preview_theme", intent.getStringExtra("theme"))
                .putLong("preview_until", System.currentTimeMillis() + seconds * 1000).apply();
        Jarvis.notifyUpdated(context);
    }
}
