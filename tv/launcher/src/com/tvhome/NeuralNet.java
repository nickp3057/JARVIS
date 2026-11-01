package com.tvhome;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The phone's neural core (ios/.../Views/Experiments.swift NeuralNetwork),
 * ported line for line so the TV grows the same mind from the same seed: a
 * core, middle and outer shell of data nodes inside two orbit rings, thoughts
 * cascading from the core outward, sparks between them, packets circling the
 * rings. Each node also has an eye position and a spike length, and the
 * whole thing morphs between moods (core first, rings last).
 *
 * One per process ({@link #SHARED}): Barn Door recreates the home screen, so
 * the mind lives outside the views and keeps morphing instead of starting over.
 */
final class NeuralNet {
    static final int THINKING = 0, WATCHING = 1, ALERT = 2;
    static final int INNER = 0, OUTER = 1, AXON = 2, ORBIT = 3;
    static final int SPARK = 0, THOUGHT = 1, PACKET = 2;

    static final double[] SHELL_RADII = {0.15, 0.29, 0.42};
    static final int[] SHELL_COUNTS = {16, 40, 72};
    static final int RING_COUNT = 30;
    static final double RING_RADIUS = 0.52;
    /** Built after the tables above: Java runs static initialisers in order. */
    static final NeuralNet SHARED = new NeuralNet();

    // Nodes (parallel arrays).
    final int count;
    final double[][] base, eye;
    final int[] layer;
    final double[] spike, seed, glow;
    // Edges.
    final int[] edgeA, edgeB, edgeKind;
    private final int[][] neighbours;

    static final class Signal {
        final int from, to, kind, direction;
        double progress;
        final double speed;

        Signal(int from, int to, double progress, double speed, int kind, int direction) {
            this.from = from;
            this.to = to;
            this.progress = progress;
            this.speed = speed;
            this.kind = kind;
            this.direction = direction;
        }
    }

    final List<Signal> signals = new ArrayList<>();
    /** Mood mix (thinking, watching, alert) per layer. */
    final double[][] mix = new double[5][];
    final double[] look = {0, 0};
    private final double[] lookTarget = {0, 0};
    private double nextLook = 0, nextThought = 1.5, clock = 20, yaw = 0.6, ringSpin = 0;
    private double lastTime = -1;
    private long rng = 0x0A6E0F17L;

    private NeuralNet() {
        for (int i = 0; i < 5; i++) mix[i] = new double[] {1, 0, 0};
        int total = RING_COUNT * 2;
        for (int c : SHELL_COUNTS) total += c;
        count = total;
        base = new double[total][];
        eye = new double[total][];
        layer = new int[total];
        spike = new double[total];
        seed = new double[total];
        glow = new double[total];

        double golden = Math.PI * (3 - Math.sqrt(5));
        int n = 0;
        for (int l = 0; l < 3; l++) {
            int c = SHELL_COUNTS[l];
            double radius = SHELL_RADII[l];
            double[][] points = new double[c][];
            for (int i = 0; i < c; i++) {
                double y = 1 - (i + 0.5) / c * 2;
                double ring = Math.sqrt(1 - y * y);
                double theta = golden * i + l;
                double r = radius * (0.94 + 0.12 * next01());
                points[i] = new double[] {Math.cos(theta) * ring * r, y * r, Math.sin(theta) * ring * r};
            }
            Integer[] order = new Integer[c];
            for (int i = 0; i < c; i++) order[i] = i;
            final double[][] pts = points;
            Arrays.sort(order, (a, b) -> Double.compare(Math.atan2(pts[a][1], pts[a][0]), Math.atan2(pts[b][1], pts[b][0])));
            double[][] eyes = new double[c][];
            for (int rank = 0; rank < c; rank++) {
                double angle = -Math.PI + 2 * Math.PI * (rank + 0.5) / c;
                double r = new double[] {0.085, 0.18, 0.29}[l] * (l == 0 ? 1 : 0.9 + 0.16 * next01());
                eyes[order[rank]] = new double[] {Math.cos(angle) * r, Math.sin(angle) * r, new double[] {0.10, 0.06, 0.02}[l]};
            }
            for (int i = 0; i < c; i++) {
                double roll = next01();
                double s;
                if (l == 0) s = 0.72;
                else if (l == 1) s = 0.9 + 0.1 * roll;
                else s = roll < 0.34 ? 1.42 + 0.18 * next01() : (roll < 0.62 ? 1.08 : 0.84);
                base[n] = points[i];
                layer[n] = l;
                eye[n] = eyes[i];
                spike[n] = s;
                seed[n] = next01();
                n++;
            }
        }
        for (int ring = 0; ring < 2; ring++) {
            for (int i = 0; i < RING_COUNT; i++) {
                double angle = 2 * Math.PI * i / RING_COUNT;
                base[n] = new double[] {Math.cos(angle), 0, Math.sin(angle)};
                eye[n] = ring == 0
                        ? new double[] {0.56 * Math.cos(angle), 0.33 * Math.sin(angle) * Math.pow(1 - Math.abs(Math.cos(angle)), 0.35), 0}
                        : new double[] {0, 0, 0};
                layer[n] = 3 + ring;
                spike[n] = i % 2 == 0 ? 1.24 : 1.0;
                seed[n] = next01();
                n++;
            }
        }

        Map<Long, Integer> kinds = new HashMap<>();
        List<long[]> order = new ArrayList<>();
        List<List<Integer>> shells = new ArrayList<>();
        for (int l = 0; l < 3; l++) {
            List<Integer> members = new ArrayList<>();
            for (int i = 0; i < count; i++) if (layer[i] == l) members.add(i);
            shells.add(members);
        }
        for (int l = 0; l < 3; l++) {
            List<Integer> members = shells.get(l);
            for (int i : members) {
                List<Integer> others = new ArrayList<>(members);
                others.remove(Integer.valueOf(i));
                final int me = i;
                others.sort((a, b) -> Double.compare(dist(a, me), dist(b, me)));
                for (int k = 0; k < 3 && k < others.size(); k++) link(kinds, order, i, others.get(k), l == 2 ? OUTER : INNER);
            }
            if (l == 2) continue;
            List<Integer> next = shells.get(l + 1);
            for (int i : members) link(kinds, order, i, nearest(next, i), AXON);
            for (int j : next) link(kinds, order, nearest(members, j), j, AXON);
        }
        for (int ring = 0; ring < 2; ring++) {
            int first = count - (2 - ring) * RING_COUNT;
            for (int i = 0; i < RING_COUNT; i++) link(kinds, order, first + i, first + (i + 1) % RING_COUNT, ORBIT);
        }
        order.sort((a, b) -> a[0] == b[0] ? Long.compare(a[1], b[1]) : Long.compare(a[0], b[0]));
        edgeA = new int[order.size()];
        edgeB = new int[order.size()];
        edgeKind = new int[order.size()];
        List<List<Integer>> nb = new ArrayList<>();
        for (int i = 0; i < count; i++) nb.add(new ArrayList<>());
        for (int e = 0; e < order.size(); e++) {
            long[] pair = order.get(e);
            edgeA[e] = (int) pair[0];
            edgeB[e] = (int) pair[1];
            edgeKind[e] = kinds.get(pair[0] * 100_000 + pair[1]);
            nb.get(edgeA[e]).add(edgeB[e]);
            nb.get(edgeB[e]).add(edgeA[e]);
        }
        neighbours = new int[count][];
        for (int i = 0; i < count; i++) {
            neighbours[i] = new int[nb.get(i).size()];
            for (int k = 0; k < neighbours[i].length; k++) neighbours[i][k] = nb.get(i).get(k);
        }
        for (int ring = 0; ring < 2; ring++) {
            int first = count - (2 - ring) * RING_COUNT;
            for (int k = 0; k < 3; k++) {
                int from = first + k * 10;
                int to = first + (k * 10 + (ring == 0 ? 1 : RING_COUNT - 1)) % RING_COUNT;
                signals.add(new Signal(from, to, next01(), 1.7, PACKET, 1));
            }
        }
    }

    private void link(Map<Long, Integer> kinds, List<long[]> order, int i, int j, int kind) {
        long a = Math.min(i, j), b = Math.max(i, j);
        long key = a * 100_000 + b;
        if (kinds.containsKey(key)) return;
        kinds.put(key, kind);
        order.add(new long[] {a, b});
    }

    private double dist(int a, int b) {
        double dx = base[a][0] - base[b][0], dy = base[a][1] - base[b][1], dz = base[a][2] - base[b][2];
        return Math.sqrt(dx * dx + dy * dy + dz * dz);
    }

    private int nearest(List<Integer> among, int to) {
        int best = among.get(0);
        for (int j : among) if (dist(j, to) < dist(best, to)) best = j;
        return best;
    }

    // --- Pose ---------------------------------------------------------------

    private static void rotY(double[] p, double a) {
        double x = p[0] * Math.cos(a) + p[2] * Math.sin(a), z = -p[0] * Math.sin(a) + p[2] * Math.cos(a);
        p[0] = x;
        p[2] = z;
    }

    private static void rotX(double[] p, double a) {
        double y = p[1] * Math.cos(a) - p[2] * Math.sin(a), z = p[1] * Math.sin(a) + p[2] * Math.cos(a);
        p[1] = y;
        p[2] = z;
    }

    private static void rotZ(double[] p, double a) {
        double x = p[0] * Math.cos(a) - p[1] * Math.sin(a), y = p[0] * Math.sin(a) + p[1] * Math.cos(a);
        p[0] = x;
        p[1] = y;
    }

    /** Every node in view space (x right, y down, z toward the viewer) into {@code out[i]}. */
    void pose(double[][] out) {
        double pitch = 0.30 + 0.10 * Math.sin(clock * 2 * Math.PI / 17);
        double[] local = new double[3], brain = new double[3], spiked = new double[3], e = new double[3];
        for (int i = 0; i < count; i++) {
            double[] w = mix[layer[i]];
            e[0] = eye[i][0];
            e[1] = eye[i][1];
            e[2] = eye[i][2];
            double start = 0;
            if (layer[i] < 3) {
                double k = layer[i];
                local[0] = base[i][0];
                local[1] = base[i][1];
                local[2] = base[i][2];
                rotY(local, 0.32 * Math.sin(clock * 2 * Math.PI / (8 + 5 * k) + k * 2.1));
                e[0] += look[0];
                e[1] += look[1];
            } else {
                int ring = layer[i] - 3;
                start = Math.atan2(base[i][2], base[i][0]);
                double a = start + (ring == 0 ? ringSpin : 1.3 - ringSpin * 0.8);
                local[0] = Math.cos(a) * RING_RADIUS;
                local[1] = 0;
                local[2] = Math.sin(a) * RING_RADIUS;
                rotX(local, ring == 0 ? 1.2 : -1.05);
                rotZ(local, ring == 0 ? 0.35 : -0.55 + 0.2 * Math.sin(clock * 2 * Math.PI / 23));
                if (ring == 1) {
                    double ea = start + ringSpin * 0.15;
                    e[0] = Math.cos(ea) * 0.62;
                    e[1] = Math.sin(ea) * 0.62;
                    e[2] = -0.0124;
                }
            }
            double tremble = spike[i] + 0.045 * Math.sin(clock * 13 + seed[i] * 40);
            for (int d = 0; d < 3; d++) {
                brain[d] = local[d];
                spiked[d] = local[d] * tremble;
            }
            view(brain, pitch);
            view(spiked, pitch);
            if (layer[i] >= 3) {
                // Alert rings: jagged crowns facing you, spinning opposite ways.
                int ring = layer[i] - 3;
                double a = start + (ring == 0 ? ringSpin : -ringSpin) * 0.6;
                double r = (ring == 0 ? 0.50 : 0.60) * (spike[i] > 1.1 ? 1.12 : 0.9) * (1 + 0.02 * Math.sin(clock * 11 + seed[i] * 30));
                spiked[0] = Math.cos(a) * r;
                spiked[1] = Math.sin(a) * r;
                spiked[2] = -0.05;
            }
            for (int d = 0; d < 3; d++) out[i][d] = brain[d] * w[0] + e[d] * w[1] + spiked[d] * w[2];
        }
    }

    private void view(double[] p, double pitch) {
        rotY(p, yaw);
        rotX(p, pitch);
    }

    // --- Simulation ---------------------------------------------------------

    /** Jump straight to a mood (no animation). */
    void settle(int mood) {
        for (int i = 0; i < mix.length; i++) mix[i] = target(mood);
        look[0] = look[1] = 0;
    }

    private static double[] target(int mood) {
        return new double[] {mood == THINKING ? 1 : 0, mood == WATCHING ? 1 : 0, mood == ALERT ? 1 : 0};
    }

    /** Advance to {@code time} seconds; several views may call this per frame. */
    void step(double time, int mood, boolean active) {
        if (lastTime < 0) {
            lastTime = time;
            return;
        }
        if (time <= lastTime) return;
        double dt = Math.min(0.1, time - lastTime);
        lastTime = time;
        clock += dt;

        double[] goal = target(mood);
        double[] rates = {3.2, 2.5, 1.9, 1.5, 1.3};
        for (int l = 0; l < 5; l++) {
            double f = Math.min(1, dt * rates[l]);
            for (int d = 0; d < 3; d++) mix[l][d] += (goal[d] - mix[l][d]) * f;
        }
        double[] w = mix[2];
        yaw += dt * 2 * Math.PI / 32 * (w[0] + 0.1 * w[1] + 2.6 * w[2]);
        ringSpin += dt * (0.45 * w[0] + 0.15 * w[1] + 1.6 * w[2]);

        if (clock >= nextLook) {
            lookTarget[0] = (next01() - 0.5) * 0.12;
            lookTarget[1] = (next01() - 0.5) * 0.07;
            nextLook = clock + 0.8 + next01() * 2.6;
        }
        double lf = Math.min(1, dt * 9);
        look[0] += (lookTarget[0] - look[0]) * lf;
        look[1] += (lookTarget[1] - look[1]) * lf;

        for (int i = 0; i < count; i++) glow[i] = Math.max(0, glow[i] - dt * 1.5);
        List<Signal> arrived = new ArrayList<>();
        for (int k = signals.size() - 1; k >= 0; k--) {
            Signal s = signals.get(k);
            s.progress += dt * s.speed;
            if (s.progress >= 1) {
                arrived.add(s);
                signals.remove(k);
            }
        }
        boolean alert = mood == ALERT;
        for (Signal s : arrived) {
            glow[s.to] = s.kind == PACKET ? Math.max(glow[s.to], 0.55) : 1;
            if (s.kind == PACKET) {
                for (int j : neighbours[s.to]) {
                    if (j != s.from) {
                        signals.add(new Signal(s.to, j, s.progress - 1, s.speed, PACKET, 1));
                        break;
                    }
                }
            } else if (s.kind == THOUGHT) {
                int l = layer[s.to];
                List<Integer> onward = filter(s.to, l + s.direction);
                if (!onward.isEmpty()) {
                    for (int j : pick(onward, 2)) send(s.to, j, THOUGHT, s.direction, alert);
                } else if (!alert) {
                    for (int j : pick(filter(s.to, l), 2)) send(s.to, j, SPARK, 1, alert);
                }
            } else {
                double roll = next01();
                int fan = roll < 0.35 ? 0 : (roll < 0.88 ? 1 : 2);
                for (int j : pick(sparkable(s.to, alert), fan)) send(s.to, j, SPARK, 1, alert);
            }
        }

        if (clock >= nextThought) {
            boolean inward = mood == WATCHING;
            int l = inward ? 2 : 0;
            List<Integer> starts = new ArrayList<>();
            for (int i = 0; i < count; i++) if (layer[i] == l) starts.add(i);
            int start = starts.get((int) (next01() * starts.size()) % starts.size());
            glow[start] = 1;
            for (int j : pick(filter(start, l + (inward ? -1 : 1)), alert ? 3 : 2)) {
                send(start, j, THOUGHT, inward ? -1 : 1, alert);
            }
            double low, spread;
            if (mood == ALERT) { low = 0.45; spread = 0.45; }
            else if (mood == WATCHING) { low = 1.2; spread = 1.2; }
            else if (active) { low = 0.5; spread = 0.6; }
            else { low = 1.8; spread = 1.8; }
            nextThought = clock + low + next01() * spread;
        }
        double sparkRate = mood == ALERT ? 7 : (mood == WATCHING ? 1.2 : (active ? 5 : 1.6));
        if (next01() < sparkRate * dt) {
            int shellCount = SHELL_COUNTS[0] + SHELL_COUNTS[1] + SHELL_COUNTS[2];
            int from = (int) (next01() * shellCount) % shellCount;
            List<Integer> to = pick(sparkable(from, alert), 1);
            if (!to.isEmpty()) {
                glow[from] = Math.max(glow[from], 0.7);
                send(from, to.get(0), SPARK, 1, alert);
            }
        }
    }

    private List<Integer> filter(int node, int wantLayer) {
        List<Integer> out = new ArrayList<>();
        for (int j : neighbours[node]) if (layer[j] == wantLayer) out.add(j);
        return out;
    }

    /** Sparks stay on the shells; in the alert they keep off the stretched outer mesh. */
    private List<Integer> sparkable(int from, boolean alert) {
        List<Integer> out = new ArrayList<>();
        for (int j : neighbours[from]) {
            if (layer[j] < 3 && !(alert && layer[from] == 2 && layer[j] == 2)) out.add(j);
        }
        return out;
    }

    private void send(int from, int to, int kind, int direction, boolean alert) {
        if (signals.size() >= 130) return;
        double speed = kind == THOUGHT ? 2.4 + next01() : 1.6 + next01() * 1.8;
        signals.add(new Signal(from, to, 0, speed * (alert ? 1.7 : 1), kind, direction));
    }

    private List<Integer> pick(List<Integer> options, int n) {
        List<Integer> pool = new ArrayList<>(options), out = new ArrayList<>();
        while (out.size() < n && !pool.isEmpty()) out.add(pool.remove((int) (next01() * pool.size()) % pool.size()));
        return out;
    }

    /** SplitMix64, the same stream as the phone's. */
    private double next01() {
        rng += 0x9E3779B97F4A7C15L;
        long z = rng;
        z = (z ^ (z >>> 30)) * 0xBF58476D1CE4E5B9L;
        z = (z ^ (z >>> 27)) * 0x94D049BB133111EBL;
        z = z ^ (z >>> 31);
        return (z >>> 11) / (double) (1L << 53);
    }
}
