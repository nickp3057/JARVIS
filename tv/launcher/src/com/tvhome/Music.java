package com.tvhome;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;

import java.util.List;

/**
 * What's playing on the TV (Spotify first, else any music session) and the
 * remote-friendly controls for it, read through MediaSessionManager (needs
 * MediaListener enabled; deploy.sh does that). The home screen polls it;
 * sessions don't need callbacks for a glance that updates every second.
 */
final class Music {
    static final String SPOTIFY = "com.spotify.tv.android";
    private static final String PLEX = "com.plexapp.android";

    /** A snapshot for the card; title is null when nothing is loaded. */
    static final class Now {
        String title, artist, app;
        Bitmap art;
        boolean playing;
        boolean allowed = true;
    }

    private Music() {}

    static Now now(Context c) {
        Now n = new Now();
        MediaController m = pick(c, n);
        if (m == null) return n;
        MediaMetadata meta = m.getMetadata();
        PlaybackState state = m.getPlaybackState();
        n.app = m.getPackageName();
        n.playing = state != null && state.getState() == PlaybackState.STATE_PLAYING;
        if (meta != null) {
            n.title = meta.getString(MediaMetadata.METADATA_KEY_TITLE);
            n.artist = meta.getString(MediaMetadata.METADATA_KEY_ARTIST);
            if (n.artist == null) n.artist = meta.getString(MediaMetadata.METADATA_KEY_ALBUM_ARTIST);
            n.art = meta.getBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART);
            if (n.art == null) n.art = meta.getBitmap(MediaMetadata.METADATA_KEY_ART);
            if (n.art == null) n.art = meta.getBitmap(MediaMetadata.METADATA_KEY_DISPLAY_ICON);
        }
        return n;
    }

    static void playPause(Context c) {
        MediaController m = pick(c, new Now());
        if (m == null) {
            openSpotify(c);
            return;
        }
        PlaybackState s = m.getPlaybackState();
        if (s != null && s.getState() == PlaybackState.STATE_PLAYING) {
            m.getTransportControls().pause();
        } else {
            m.getTransportControls().play();
        }
    }

    static void next(Context c) {
        MediaController m = pick(c, new Now());
        if (m != null) m.getTransportControls().skipToNext();
    }

    static void previous(Context c) {
        MediaController m = pick(c, new Now());
        if (m != null) m.getTransportControls().skipToPrevious();
    }

    /** Spotify's own session, or null if Spotify hasn't run since boot. */
    static MediaController spotify(Context c) {
        try {
            for (MediaController m : c.getSystemService(MediaSessionManager.class)
                    .getActiveSessions(new ComponentName(c, MediaListener.class))) {
                if (SPOTIFY.equals(m.getPackageName())) return m;
            }
        } catch (SecurityException ignored) {
        }
        return null;
    }

    /**
     * Plays without opening Spotify on screen: a spotify: URI, "search:words"
     * (Spotify plays its top hit), or "" to resume. False if Spotify has no
     * session yet (not run since boot) and has to be opened instead.
     */
    static boolean playInBackground(Context c, String music) {
        MediaController m = spotify(c);
        if (m == null) return false;
        if (music.startsWith("spotify:")) {
            m.getTransportControls().playFromUri(android.net.Uri.parse(music), null);
        } else if (music.startsWith("search:")) {
            m.getTransportControls().playFromSearch(music.substring(7), null);
        } else {
            m.getTransportControls().play();
        }
        return true;
    }

    /** Opens Spotify itself (search, playlists); Back returns home. */
    static void openSpotify(Context c) {
        Intent i = c.getPackageManager().getLeanbackLaunchIntentForPackage(SPOTIFY);
        if (i == null) i = c.getPackageManager().getLaunchIntentForPackage(SPOTIFY);
        if (i == null) return;
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        c.startActivity(i);
    }

    /** Spotify's session if it has one, else any non-Plex session that is playing. */
    private static MediaController pick(Context c, Now n) {
        List<MediaController> sessions;
        try {
            sessions = c.getSystemService(MediaSessionManager.class)
                    .getActiveSessions(new ComponentName(c, MediaListener.class));
        } catch (SecurityException e) {
            n.allowed = false;
            return null;
        }
        MediaController fallback = null;
        for (MediaController m : sessions) {
            if (SPOTIFY.equals(m.getPackageName())) return m;
            PlaybackState s = m.getPlaybackState();
            if (fallback == null && !PLEX.equals(m.getPackageName())
                    && s != null && s.getState() == PlaybackState.STATE_PLAYING) {
                fallback = m;
            }
        }
        return fallback;
    }
}
