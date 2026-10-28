package com.tvhome;

import android.service.notification.NotificationListenerService;

/**
 * Exists only so the launcher may read media sessions (what Spotify is
 * playing) through MediaSessionManager. Enabled over adb by deploy.sh
 * (cmd notification allow_listener); it ignores the notifications themselves.
 */
public class MediaListener extends NotificationListenerService {
}
