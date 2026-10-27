package com.tvhome;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

/**
 * Catches the remote's app-shortcut buttons once TCL's handler is gone:
 * opens the requested app if it is installed, otherwise Plex.
 */
public class ShortcutActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Uri uri = getIntent().getData();
        String wanted = uri != null ? uri.getQueryParameter("packagename") : null;
        Intent launch = null;
        if (wanted != null) launch = getPackageManager().getLeanbackLaunchIntentForPackage(wanted);
        if (launch == null) {
            launch = getPackageManager().getLeanbackLaunchIntentForPackage("com.plexapp.android");
        }
        if (launch != null) {
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(launch);
        }
        finish();
    }
}
