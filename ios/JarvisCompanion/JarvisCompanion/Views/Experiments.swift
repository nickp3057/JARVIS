import SwiftUI
import simd

/// The "Experimenting" theme's lab: visual ideas Nick can try on his own
/// devices before any of them graduates into a real theme. Picking the
/// Experimenting palette (Spider-Man's colours underneath) turns on whichever
/// experiments are switched on in Settings; every other theme ignores them.
/// The switches are shared through Core (`/api/theme` `experiments`), so the
/// phone and the iPad match.
///
/// How it's drawn: no video, no web view. Every frame is computed in Swift
/// (SwiftUI `Canvas` inside a `TimelineView`), from the clock.
///
/// Rules for anything added here (Nick's taste): no flashing the screen or
/// pulsing the whole view; colour as glow on a dark base, never a red
/// background; Reduce Motion and Low Power Mode get the still frame.
enum Experiment: String, CaseIterable, Identifiable {
    case plasma, neural, orb3D

    var id: String { rawValue }

    var name: String {
        switch self {
        case .plasma: return "Flowing plasma"
        case .neural: return "Neural core"
        case .orb3D: return "Orbit orb"
        }
    }

    var blurb: String {
        switch self {
        case .plasma: return "Red and blue light flowing behind frosted glass, speeding up and easing off on its own."
        case .neural: return "The reactor becomes a brain of data nodes, gold impulses firing through it; busier while JARVIS thinks."
        case .orb3D: return "A lit sphere with tilted orbiting rings (Neural core wins if both are on)."
        }
    }

    /// Default when Nick hasn't flipped it on any device.
    var defaultOn: Bool { self != .orb3D }

    var storageKey: String { "jarvis.experiment.\(rawValue)" }

    var isOn: Bool { UserDefaults.standard.object(forKey: storageKey) as? Bool ?? defaultOn }
}

// MARK: - Flowing plasma

/// A smooth, never-repeating "speed" signal: the sum of a few sine waves with
/// unrelated periods. `phase(t)` is its integral, so motion driven by it
/// speeds up and eases off at times that look random but never jumps or
/// runs backwards (the wave amplitudes sum to less than 1).
private struct Gust {
    let rate: Double                       // average cycles per second
    let waves: [(amp: Double, period: Double, offset: Double)]

    init(rate: Double, seed: Double) {
        self.rate = rate
        // Periods ~6–31 s, mutually unrelated; amplitudes sum to 0.88.
        self.waves = [(0.42, 6.3 + seed.truncatingRemainder(dividingBy: 3.1), seed * 1.7),
                      (0.28, 13.7 + seed.truncatingRemainder(dividingBy: 5.3), seed * 2.9),
                      (0.18, 31.1 - seed.truncatingRemainder(dividingBy: 7.7), seed * 0.6)]
    }

    /// Cycles completed by time `t` (seconds): ∫ rate·(1 + Σ a·cos(2πt/p + o)) dt.
    func phase(_ t: Double) -> Double {
        var extra = 0.0
        for w in waves {
            extra += w.amp * w.period / (2 * .pi) * sin(2 * .pi * t / w.period + w.offset)
        }
        return rate * (t + extra)
    }
}

/// Blobs of the palette's red and blue flowing on wandering paths over the
/// deep background, stretched along their travel, with frosted glass on top.
/// One `Canvas` of radial gradients (no blur filter), cheap enough to sit
/// behind every screen.
struct PlasmaBackdrop: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var scheme

    private struct Blob {
        let color: Color
        let radius: Double      // fraction of the longer side
        let x: Gust, y: Gust, spin: Gust
        let phase: Double
        let opacity: (dark: Double, light: Double)
    }

    private var blobs: [Blob] {
        let red = JarvisTheme.cyan, blue = JarvisTheme.blue, ice = JarvisTheme.reactor
        func blob(_ color: Color, _ radius: Double, _ seed: Double, _ dark: Double, _ light: Double) -> Blob {
            Blob(color: color, radius: radius,
                 x: Gust(rate: 1 / (34 + seed * 4), seed: seed),
                 y: Gust(rate: 1 / (27 + seed * 5), seed: seed + 11.3),
                 spin: Gust(rate: 1 / (60 + seed * 7), seed: seed + 5.9),
                 phase: seed * 1.37, opacity: (dark, light))
        }
        return [
            blob(red, 0.55, 0.0, 0.60, 0.30),
            blob(blue, 0.65, 1.0, 0.68, 0.34),
            blob(red, 0.38, 2.0, 0.46, 0.22),
            blob(ice, 0.40, 3.0, 0.32, 0.18),
            blob(blue, 0.34, 4.0, 0.50, 0.24),
            blob(red, 0.28, 5.0, 0.36, 0.18),
            blob(blue, 0.30, 6.0, 0.40, 0.20),
        ]
    }

    private var moving: Bool { !reduceMotion && !ProcessInfo.processInfo.isLowPowerModeEnabled }

    var body: some View {
        let blobs = self.blobs
        let dark = scheme == .dark
        ZStack {
            LinearGradient(colors: [JarvisTheme.background, JarvisTheme.backgroundDeep],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            TimelineView(.animation(minimumInterval: 1.0 / 24, paused: !moving)) { context in
                // Seconds since 2001 is too big for smooth float sines; wrap to a day.
                let t = moving ? context.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 86_400) : 40
                Canvas { canvas, size in
                    let side = max(size.width, size.height)
                    for blob in blobs {
                        let px = 2 * .pi * blob.x.phase(t) + blob.phase
                        let py = 2 * .pi * blob.y.phase(t) + blob.phase * 1.3
                        // A wandering path: two harmonics per axis so it doesn't trace an ellipse.
                        let x = 0.5 + 0.34 * sin(px) + 0.12 * sin(2.3 * px + 1.1)
                        let y = 0.5 + 0.32 * cos(py) + 0.12 * cos(1.7 * py + 0.4)
                        // Stretch along the direction of travel: flow, not floating discs.
                        let dx = 0.34 * cos(px) + 0.276 * cos(2.3 * px + 1.1)
                        let dy = -(0.32 * sin(py) + 0.204 * sin(1.7 * py + 0.4))
                        let angle = atan2(dy, dx) + 0.4 * sin(2 * .pi * blob.spin.phase(t))
                        let r = side * blob.radius
                        let stretch = 1.35
                        let opacity = dark ? blob.opacity.dark : blob.opacity.light
                        var layer = canvas
                        layer.translateBy(x: x * size.width, y: y * size.height)
                        layer.rotate(by: .radians(angle))
                        layer.scaleBy(x: stretch, y: 1 / stretch)
                        layer.fill(Path(ellipseIn: CGRect(x: -r, y: -r, width: r * 2, height: r * 2)), with: .radialGradient(
                            Gradient(colors: [blob.color.opacity(opacity), blob.color.opacity(opacity * 0.35), .clear]),
                            center: .zero, startRadius: 0, endRadius: r))
                    }
                }
            }
            // The frost: a thin material evens the colour out like glass, then a
            // whisper of the background keeps text contrast where it was.
            Rectangle().fill(.ultraThinMaterial).opacity(0.35)
            JarvisTheme.backgroundDeep.opacity(dark ? 0.18 : 0.08)
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

// MARK: - Neural core

/// JARVIS's mind as the films drew it in Age of Ultron: a body of angular,
/// box-like data nodes wired together, warm gold light running through it.
/// Here it's shaped as two hemispheres (a brain), turning slowly in 3D; the
/// structure takes the palette's reactor colour and the impulses are gold.
///
/// Impulses travel along the wires; when one reaches a node the node lights
/// and may pass it on to a neighbour or two, so firing spreads in chains.
/// Spontaneous firing runs ~2.4 per second, about three times that while
/// JARVIS is thinking (`active`).
final class NeuralNetwork {
    struct Node { let p: SIMD3<Double>; var glow: Double = 0 }
    struct Signal { let from: Int, to: Int; var progress: Double; let speed: Double }

    private(set) var nodes: [Node] = []
    private(set) var edges: [(Int, Int)] = []
    private var neighbours: [[Int]] = []
    private(set) var signals: [Signal] = []
    private var lastTime: Double?
    private var rng = SplitMix(seed: 0x0A6E_0F17)

    init(count: Int = 96) {
        build(count: count)
    }

    private func build(count: Int) {
        // Two hemispheres with a gap between them (the fissure); points spread
        // by a Fibonacci spiral, every fifth pulled inward for depth.
        let golden = Double.pi * (3 - 5.0.squareRoot())
        var points: [SIMD3<Double>] = []
        let half = count / 2
        for side in [-1.0, 1.0] {
            for i in 0..<half {
                let y = 1 - (Double(i) + 0.5) / Double(half) * 2
                let ring = (1 - y * y).squareRoot()
                let theta = golden * Double(i)
                let x = abs(cos(theta) * ring) * side
                let z = sin(theta) * ring * 1.08
                let depth = i % 5 == 0 ? 0.72 : 1.0
                // Longer front to back than wide, flatter than tall: a brain, not a ball;
                // the underside tucks in where the temporal lobes would be.
                let tuck = y < -0.55 ? 0.85 : 1.0
                points.append(SIMD3(x * 0.36 * tuck + side * 0.06, y * 0.31, z * 0.52) * depth)
            }
        }
        nodes = points.map { Node(p: $0) }
        func distance(_ a: Int, _ b: Int) -> Double { simd_length(nodes[a].p - nodes[b].p) }
        // Wire each node to its three nearest in its own hemisphere, plus a
        // few bridges across the middle (the corpus callosum).
        var set = Set<[Int]>()
        for i in nodes.indices {
            let near = nodes.indices.filter { $0 != i && (nodes[$0].p.x > 0) == (nodes[i].p.x > 0) }
                .sorted { distance($0, i) < distance($1, i) }
                .prefix(3)
            for j in near { set.insert([min(i, j), max(i, j)]) }
        }
        let left = nodes.indices.filter { nodes[$0].p.x < 0 && abs(nodes[$0].p.x) < 0.16 }
        let right = nodes.indices.filter { nodes[$0].p.x > 0 && abs(nodes[$0].p.x) < 0.16 }
        for (k, i) in left.enumerated() where k % 2 == 0 {
            if let j = right.min(by: { distance($0, i) < distance($1, i) }) {
                set.insert([min(i, j), max(i, j)])
            }
        }
        edges = set.map { ($0[0], $0[1]) }.sorted { $0.0 == $1.0 ? $0.1 < $1.1 : $0.0 < $1.0 }
        neighbours = Array(repeating: [], count: nodes.count)
        for (a, b) in edges { neighbours[a].append(b); neighbours[b].append(a) }
    }

    /// Advance the simulation to `time` (seconds).
    func step(to time: Double, active: Bool) {
        let dt = min(0.1, max(0, time - (lastTime ?? time)))
        lastTime = time
        guard dt > 0 else { return }
        for i in nodes.indices { nodes[i].glow = max(0, nodes[i].glow - dt * 1.6) }
        var arrived: [Int] = []
        signals = signals.compactMap { signal in
            var s = signal
            s.progress += dt * s.speed
            if s.progress >= 1 { arrived.append(s.to); return nil }
            return s
        }
        for node in arrived {
            nodes[node].glow = 1
            // Pass it on: usually one neighbour, sometimes two, sometimes it dies out.
            let roll = rng.next01()
            let fanOut = roll < 0.30 ? 0 : (roll < 0.86 ? 1 : 2)
            for _ in 0..<fanOut { fire(from: node) }
        }
        let rate = active ? 7.0 : 2.4
        if rng.next01() < rate * dt { fire(from: Int(rng.next01() * Double(nodes.count)) % nodes.count) }
    }

    private func fire(from node: Int) {
        guard signals.count < 70, !neighbours[node].isEmpty else { return }
        let next = neighbours[node][Int(rng.next01() * Double(neighbours[node].count)) % neighbours[node].count]
        nodes[node].glow = max(nodes[node].glow, 0.7)
        signals.append(Signal(from: node, to: next, progress: 0, speed: 1.6 + rng.next01() * 1.8))
    }
}

/// Tiny deterministic PRNG so every device grows the same brain.
private struct SplitMix {
    var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
    mutating func next01() -> Double { Double(next() >> 11) / Double(UInt64(1) << 53) }
}

struct NeuralCore: View {
    var size: CGFloat
    var moving: Bool
    var active = false
    @State private var network = NeuralNetwork()

    private static let gold = Color(red: 1.0, green: 0.68, blue: 0.22)
    private static let hot = Color(red: 1.0, green: 0.90, blue: 0.62)

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !moving)) { context in
            let t = context.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 86_400)
            Canvas { canvas, canvasSize in
                if moving { network.step(to: t, active: active) }
                draw(in: &canvas, size: canvasSize, seconds: moving ? t : 12)
            }
        }
    }

    /// Rotate (a slow turn with a gentle nod), then perspective-project.
    private func project(_ p: SIMD3<Double>, seconds: Double, size: CGSize) -> (CGPoint, Double) {
        let yaw = seconds * 2 * .pi / 28
        let pitch = 0.32 + 0.10 * sin(seconds * 2 * .pi / 17)
        let x1 = p.x * cos(yaw) + p.z * sin(yaw)
        let z1 = -p.x * sin(yaw) + p.z * cos(yaw)
        let y1 = p.y * cos(pitch) - z1 * sin(pitch)
        let z2 = p.y * sin(pitch) + z1 * cos(pitch)
        let scale = Double(min(size.width, size.height)) * 1.3 / (1.6 - z2 * 0.6)
        let point = CGPoint(x: size.width / 2 + x1 * scale, y: size.height / 2 + y1 * scale)
        return (point, max(0.15, min(1, z2 + 0.5)))  // depth: ~0 at the back, 1 at the front
    }

    private func draw(in canvas: inout GraphicsContext, size: CGSize, seconds: Double) {
        let nodes = network.nodes
        let projected = nodes.map { project($0.p, seconds: seconds, size: size) }
        let line = max(0.6, size.width / 220)
        let reactor = JarvisTheme.reactor

        // Soft halo so the brain sits in light, not on nothing.
        let center = CGPoint(x: size.width / 2, y: size.height / 2)
        canvas.fill(Path(ellipseIn: CGRect(origin: .zero, size: size)), with: .radialGradient(
            Gradient(colors: [reactor.opacity(active ? 0.22 : 0.15), .clear]),
            center: center, startRadius: 0, endRadius: size.width * 0.48))

        canvas.blendMode = .plusLighter
        for (a, b) in network.edges {
            let (pa, da) = projected[a], (pb, db) = projected[b]
            var path = Path()
            path.move(to: pa)
            path.addLine(to: pb)
            canvas.stroke(path, with: .color(reactor.opacity(0.10 + 0.32 * (da + db) / 2)), lineWidth: line)
            let warm = max(nodes[a].glow, nodes[b].glow)
            if warm > 0.05 {
                canvas.stroke(path, with: .color(Self.gold.opacity(0.55 * warm)), lineWidth: line * 1.4)
            }
        }
        for signal in network.signals {
            let (pa, da) = projected[signal.from], (pb, db) = projected[signal.to]
            let f = signal.progress
            let head = CGPoint(x: pa.x + (pb.x - pa.x) * f, y: pa.y + (pb.y - pa.y) * f)
            let tailF = max(0, f - 0.35)
            let tail = CGPoint(x: pa.x + (pb.x - pa.x) * tailF, y: pa.y + (pb.y - pa.y) * tailF)
            var trail = Path()
            trail.move(to: tail)
            trail.addLine(to: head)
            let depth = da + (db - da) * f
            canvas.stroke(trail, with: .linearGradient(Gradient(colors: [Self.gold.opacity(0), Self.gold.opacity(0.95 * depth)]),
                                                       startPoint: tail, endPoint: head),
                          style: StrokeStyle(lineWidth: line * 2, lineCap: .round))
            let r = line * 2.2
            canvas.fill(Path(ellipseIn: CGRect(x: head.x - r, y: head.y - r, width: r * 2, height: r * 2)),
                        with: .color(Self.hot.opacity(depth)))
        }
        // Nodes: little data boxes (the films' angular JARVIS), gold while lit.
        for (i, node) in nodes.enumerated() {
            let (p, depth) = projected[i]
            let box = max(1.2, size.width / 95) * (0.6 + 0.6 * depth) * (1 + node.glow * 0.8)
            let rect = CGRect(x: p.x - box / 2, y: p.y - box / 2, width: box, height: box)
            canvas.fill(Path(rect), with: .color(reactor.opacity(0.25 + 0.55 * depth)))
            if node.glow > 0.02 {
                canvas.fill(Path(rect), with: .color(Self.hot.opacity(node.glow * depth)))
                let g = box * 3.2
                canvas.fill(Path(ellipseIn: CGRect(x: p.x - g, y: p.y - g, width: g * 2, height: g * 2)),
                            with: .radialGradient(Gradient(colors: [Self.gold.opacity(0.45 * node.glow * depth), .clear]),
                                                  center: p, startRadius: 0, endRadius: g))
            }
        }
    }
}

// MARK: - Orbit orb

/// The reactor as a lit glass sphere with three rings on tilted, slowly
/// precessing orbits (`rotation3DEffect`).
struct OrbitOrb: View {
    var size: CGFloat
    var moving: Bool
    var active = false

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !moving)) { context in
            orb(seconds: moving ? context.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 86_400) : 0)
        }
    }

    private func turn(_ seconds: Double, every period: Double) -> Double {
        (seconds / period).truncatingRemainder(dividingBy: 1) * 360
    }

    private func ring(_ seconds: Double, tilt: (Double, Double), period: Double, inset: CGFloat,
                      width: CGFloat, opacity: Double) -> some View {
        Circle()
            .trim(from: 0.0, to: 0.86)
            .stroke(JarvisTheme.reactor.opacity(opacity),
                    style: StrokeStyle(lineWidth: width, lineCap: .round))
            .padding(inset)
            .rotationEffect(.degrees(turn(seconds, every: period)))
            .rotation3DEffect(.degrees(tilt.0), axis: (x: 1, y: 0, z: 0), perspective: 0.45)
            .rotation3DEffect(.degrees(tilt.1), axis: (x: 0, y: 1, z: 0), perspective: 0.45)
    }

    private func orb(seconds: Double) -> some View {
        let core = size * 0.46
        let wobble = 8 * sin(2 * .pi * seconds / 23)
        return ZStack {
            Circle().fill(RadialGradient(colors: [JarvisTheme.reactor.opacity(active ? 0.30 : 0.20), .clear],
                                         center: .center, startRadius: core * 0.3, endRadius: size / 2))
            ring(seconds, tilt: (68 + wobble, 12), period: 16, inset: size * 0.06, width: max(1.5, size / 60), opacity: 0.55)
            ZStack {
                Circle().fill(RadialGradient(
                    colors: [JarvisTheme.reactor.opacity(0.95), JarvisTheme.blue.opacity(0.85), JarvisTheme.backgroundDeep],
                    center: UnitPoint(x: 0.36, y: 0.32), startRadius: 0, endRadius: core * 0.62))
                Circle().fill(RadialGradient(colors: [.white.opacity(0.85), .white.opacity(0)],
                                             center: .center, startRadius: 0, endRadius: core * 0.20))
                Circle().strokeBorder(LinearGradient(colors: [JarvisTheme.reactor.opacity(0.9), JarvisTheme.cyan.opacity(0.5)],
                                                     startPoint: .bottomTrailing, endPoint: .topLeading),
                                      lineWidth: max(1, size / 90))
                Ellipse().fill(.white.opacity(0.35))
                    .frame(width: core * 0.36, height: core * 0.18)
                    .rotationEffect(.degrees(-35))
                    .offset(x: -core * 0.17, y: -core * 0.22)
                    .blur(radius: core * 0.04)
            }
            .frame(width: core, height: core)
            .shadow(color: JarvisTheme.reactor.opacity(0.45), radius: size * 0.08)
            ring(seconds, tilt: (-62 - wobble, -20), period: 10, inset: size * 0.14, width: 1, opacity: 0.40)
            ring(seconds, tilt: (12, 74 + wobble), period: 22, inset: size * 0.02, width: max(1, size / 110), opacity: 0.30)
        }
        .compositingGroup()
    }
}

// MARK: - Settings

/// Switches for each experiment; shown under the theme list while
/// Experimenting is picked.
struct ExperimentToggles: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Experiments").font(.footnote.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
                .padding(.top, 10)
            ForEach(Experiment.allCases) { ExperimentToggle(experiment: $0) }
        }
    }
}

private struct ExperimentToggle: View {
    let experiment: Experiment
    @EnvironmentObject private var settings: AppSettings
    @AppStorage private var on: Bool

    init(experiment: Experiment) {
        self.experiment = experiment
        _on = AppStorage(wrappedValue: experiment.defaultOn, experiment.storageKey)
    }

    var body: some View {
        Toggle(isOn: Binding(get: { on }, set: { value in
            on = value
            Task { await ThemeSync.pushExperiment(experiment, on: value, settings: settings) }
        })) {
            VStack(alignment: .leading, spacing: 2) {
                Text(experiment.name).font(.subheadline.weight(.medium))
                Text(experiment.blurb).font(.caption).foregroundStyle(.secondary)
            }
        }
        .tint(JarvisTheme.reactor)
        .padding(.vertical, 4)
    }
}
