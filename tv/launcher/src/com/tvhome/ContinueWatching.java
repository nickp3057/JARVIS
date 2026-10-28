package com.tvhome;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Plex's Continue Watching, straight from the NAS with the TV's own Plex token
 * (Config, like voice search in RemoteBridgeService): what Nick is part-way
 * through, newest first. Opening one goes to its page in the Plex app (the
 * show's page for an episode), where Resume is the first button.
 */
final class ContinueWatching {
    private static final String TAG = "TvHomeContinue";
    private static final String PLEX = "com.plexapp.android";

    static final class Item {
        String title, detail, type, ratingKey, slug;
    }

    private ContinueWatching() {}

    /** Blocking; empty on any failure (the NAS may be asleep). */
    static List<Item> fetch() {
        List<Item> out = new ArrayList<>();
        try {
            JSONObject box = get("/hubs/continueWatching/items?X-Plex-Container-Size=8")
                    .optJSONObject("MediaContainer");
            JSONArray items = box != null ? box.optJSONArray("Metadata") : null;
            if (items == null) {
                box = get("/library/onDeck").optJSONObject("MediaContainer");
                items = box != null ? box.optJSONArray("Metadata") : null;
            }
            for (int i = 0; items != null && i < items.length() && out.size() < 6; i++) {
                Item item = parse(items.getJSONObject(i));
                if (item != null) out.add(item);
            }
        } catch (Exception e) {
            Log.i(TAG, "continue watching unavailable: " + e);
        }
        return out;
    }

    private static Item parse(JSONObject m) {
        Item item = new Item();
        String type = m.optString("type");
        long left = (m.optLong("duration") - m.optLong("viewOffset")) / 60_000;
        String remaining = m.optLong("viewOffset") <= 0 || left <= 0 ? "Up next"
                : left >= 60 ? (left / 60) + "h " + (left % 60) + "m left" : left + "m left";
        if ("episode".equals(type)) {
            item.title = m.optString("grandparentTitle");
            item.detail = String.format(Locale.US, "S%d E%d  ·  %s",
                    m.optInt("parentIndex"), m.optInt("index"), remaining);
            item.type = "show";
            item.ratingKey = m.optString("grandparentRatingKey");
            item.slug = m.optString("grandparentSlug");
        } else if ("movie".equals(type)) {
            item.title = m.optString("title");
            item.detail = remaining;
            item.type = "movie";
            item.ratingKey = m.optString("ratingKey");
            item.slug = m.optString("slug");
        } else {
            return null;
        }
        return item.title.isEmpty() || item.ratingKey.isEmpty() ? null : item;
    }

    static void open(Context c, Item item) {
        String slug = item.slug != null && !item.slug.isEmpty() ? item.slug
                : item.title.toLowerCase(Locale.US).replaceAll("[^a-z0-9]+", "-").replaceAll("^-|-$", "");
        String url = "https://watch.plex.tv/libraries/" + Config.PLEX_SERVER_ID + "/" + item.type
                + "/" + slug + "/" + item.ratingKey;
        Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
        i.setPackage(PLEX);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        c.startActivity(i);
    }

    private static JSONObject get(String path) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(Config.PLEX_SERVER + path).openConnection();
        c.setConnectTimeout(4000);
        c.setReadTimeout(15000);
        c.setRequestProperty("Accept", "application/json");
        c.setRequestProperty("X-Plex-Token", Config.PLEX_TOKEN);
        try (InputStream in = c.getInputStream()) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
            return new JSONObject(out.toString(StandardCharsets.UTF_8.name()));
        } finally {
            c.disconnect();
        }
    }
}
