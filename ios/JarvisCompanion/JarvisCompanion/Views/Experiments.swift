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
    case plasma, neural, orb3D, glass

    var id: String { rawValue }

    var name: String {
        switch self {
        case .plasma: return "Flowing plasma"
        case .neural: return "Neural core"
        case .orb3D: return "Orbit orb"
        case .glass: return "Liquid glass"
        }
    }

    var blurb: String {
        switch self {
        case .plasma: return "Red and blue light flowing behind frosted glass, speeding up and easing off on its own."
        case .neural: return "The reactor becomes a layered mind of data nodes with thoughts cascading out from its core. It opens into a green eye while the camera watches and bristles red for Barn Door."
        case .orb3D: return "A lit sphere with tilted orbiting rings (Neural core wins if both are on)."
        case .glass: return "Every card turns to Liquid Glass (as clear or tinted as your Liquid Glass setting), and the background loses its frost and turns vivid."
        }
    }

    /// Default when Nick hasn't flipped it on any device (Core's
    /// `brain/theme._LAB_DEFAULTS` mirrors these for the TV).
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

    /// `swing` scales the speed changes: 1 is the original (0.12×–1.88× the
    /// average speed), the backdrop uses 0.5 so a burst never reads as
    /// something popping up in the corner of your eye.
    init(rate: Double, seed: Double, swing: Double = 1) {
        self.rate = rate
        // Periods ~6–31 s, mutually unrelated; amplitudes sum to 0.88 × swing.
        self.waves = [(0.42 * swing, 6.3 + seed.truncatingRemainder(dividingBy: 3.1), seed * 1.7),
                      (0.28 * swing, 13.7 + seed.truncatingRemainder(dividingBy: 5.3), seed * 2.9),
                      (0.18 * swing, 31.1 - seed.truncatingRemainder(dividingBy: 7.7), seed * 0.6)]
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

/// Where JARVIS's core sits on screen (window coordinates), so the backdrop
/// can keep a warm pool of red under him. Set by the Room's core while it's
/// on screen; nil everywhere else.
enum PlasmaAnchor {
    static var center: CGPoint?
    static var radius: CGFloat = 0
}

/// Blobs of the palette's red and blue flowing on wandering paths over the
/// deep background, stretched along their travel, with frosted glass on top.
/// One `Canvas` of radial gradients (no blur filter), cheap enough to sit
/// behind every screen.
///
/// Calm on purpose (Nick, 2026-11-01): speed bursts are half what they were,
/// and a blob only stretches while it's actually moving, so a slow blob
/// never swings round in a blink. Under JARVIS's core (`PlasmaAnchor`) the
/// passing colour thins and a soft dark pool holds instead, so full red and
/// full blue can't stack there and wash him out.
struct PlasmaBackdrop: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var scheme
    /// With Liquid glass on the frost moves to the cards: the backdrop goes clear and vivid.
    @AppStorage(Experiment.glass.storageKey) private var glassOn = Experiment.glass.defaultOn
    /// This backdrop's own top-left in the window, to place the anchor (sheets sit inset).
    @State private var origin = CGPoint.zero

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
                 x: Gust(rate: 1 / (40 + seed * 4), seed: seed, swing: 0.5),
                 y: Gust(rate: 1 / (33 + seed * 5), seed: seed + 11.3, swing: 0.5),
                 spin: Gust(rate: 1 / (70 + seed * 7), seed: seed + 5.9, swing: 0.5),
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

    /// A wandering path: two harmonics per axis so it doesn't trace an ellipse.
    private func position(_ blob: Blob, _ t: Double) -> (Double, Double) {
        let px = 2 * .pi * blob.x.phase(t) + blob.phase
        let py = 2 * .pi * blob.y.phase(t) + blob.phase * 1.3
        return (0.5 + 0.34 * sin(px) + 0.12 * sin(2.3 * px + 1.1),
                0.5 + 0.32 * cos(py) + 0.12 * cos(1.7 * py + 0.4))
    }

    /// Soft and even under frost; with Liquid glass (the cards are the glass)
    /// vivid: stronger colour, a bright centre that holds, then a quicker fade.
    private func gradient(_ color: Color, _ opacity: Double) -> Gradient {
        guard glassOn else {
            return Gradient(colors: [color.opacity(opacity), color.opacity(opacity * 0.35), .clear])
        }
        let o = min(0.95, opacity * 1.4)
        return Gradient(stops: [
            .init(color: color.opacity(o), location: 0),
            .init(color: color.opacity(o * 0.8), location: 0.3),
            .init(color: color.opacity(o * 0.25), location: 0.65),
            .init(color: .clear, location: 1),
        ])
    }

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
                    let origin = self.origin
                    let anchor = PlasmaAnchor.center.map { CGPoint(x: $0.x - origin.x, y: $0.y - origin.y) }
                    // The pool under JARVIS: a soft shade of the deep background (Nick
                    // picked dark over red, 2026-11-01), drifting a little.
                    if let anchor {
                        let r = max(PlasmaAnchor.radius, side * 0.12) * 2.15
                        let drift = CGPoint(x: anchor.x + r * 0.08 * sin(2 * .pi * t / 47),
                                            y: anchor.y + r * 0.06 * cos(2 * .pi * t / 39))
                        var pool = canvas
                        pool.translateBy(x: drift.x, y: drift.y)
                        pool.rotate(by: .radians(0.3 * sin(2 * .pi * t / 83)))
                        // Its shape slowly morphs (a minute-long ease, no brightness change).
                        let squash = 1.18 + 0.08 * sin(2 * .pi * t / 61)
                        pool.scaleBy(x: squash, y: 1 / squash)
                        pool.fill(Path(ellipseIn: CGRect(x: -r, y: -r, width: r * 2, height: r * 2)), with: .radialGradient(
                            .init(colors: [JarvisTheme.backgroundDeep.opacity(dark ? 0.86 : 0.45),
                                           JarvisTheme.backgroundDeep.opacity(dark ? 0.58 : 0.28), .clear]),
                            center: .zero, startRadius: 0, endRadius: r))
                    }
                    canvas.drawLayer { layer in
                        for blob in blobs {
                            let (x, y) = position(blob, t)
                            // Direction and stretch from where it's actually heading in the next
                            // half second: round when slow, so it never visibly spins.
                            let (ax, ay) = position(blob, t + 0.5)
                            let vx = (ax - x) * size.width, vy = (ay - y) * size.height
                            let speed = (vx * vx + vy * vy).squareRoot() / side
                            let stretch = 1 + 0.35 * min(1, speed / 0.015)
                            let angle = atan2(vy, vx) + 0.4 * sin(2 * .pi * blob.spin.phase(t))
                            let r = side * blob.radius
                            var blobLayer = layer
                            blobLayer.translateBy(x: x * size.width, y: y * size.height)
                            blobLayer.rotate(by: .radians(angle))
                            blobLayer.scaleBy(x: stretch, y: 1 / stretch)
                            blobLayer.fill(Path(ellipseIn: CGRect(x: -r, y: -r, width: r * 2, height: r * 2)), with: .radialGradient(
                                gradient(blob.color, dark ? blob.opacity.dark : blob.opacity.light),
                                center: .zero, startRadius: 0, endRadius: r))
                        }
                        // Thin the passing colour right under JARVIS (the pool shows through).
                        if let anchor {
                            let r = max(PlasmaAnchor.radius, side * 0.12) * 1.8
                            layer.blendMode = .destinationOut
                            layer.fill(Path(ellipseIn: CGRect(x: anchor.x - r, y: anchor.y - r, width: r * 2, height: r * 2)),
                                       with: .radialGradient(Gradient(colors: [.black.opacity(0.62), .black.opacity(0.4), .clear]),
                                                             center: anchor, startRadius: 0, endRadius: r))
                        }
                    }
                }
            }
            // The frost: a thin material evens the colour out like glass, then a
            // whisper of the background keeps text contrast where it was. With
            // Liquid glass the cards are the glass: the backdrop is left clear.
            if !glassOn {
                Rectangle().fill(.ultraThinMaterial).opacity(0.35)
                JarvisTheme.backgroundDeep.opacity(dark ? 0.18 : 0.08)
            }
        }
        .ignoresSafeArea()
        .onGeometryChange(for: CGPoint.self) { $0.frame(in: .global).origin } action: { origin = $0 }
        .accessibilityHidden(true)
    }
}

// MARK: - Neural core

/// What the neural core is doing; it morphs between these instead of cutting.
enum NeuralMood {
    case thinking   // the brain: layered shells, thoughts cascading outward
    case watching   // the room camera is on: it opens into a green eye
    case alert      // Barn Door: it bristles into red spikes

    var target: SIMD3<Double> {
        switch self {
        case .thinking: return [1, 0, 0]
        case .watching: return [0, 1, 0]
        case .alert: return [0, 0, 1]
        }
    }
}

/// JARVIS's mind as the films drew it in Age of Ultron: angular, box-like
/// data nodes wired together, warm gold light running through them. Here
/// it's round and layered: a core, a middle and an outer shell (each
/// breathing a little against the others) inside two orbit rings, all
/// turning slowly in 3D.
///
/// Thinking looks like thinking, and it's the house's real thinking: every
/// signal to or from Core (`TrafficFeed`) fires a thought that cascades
/// through the shells, and the traffic's recent pace (`activity`) sets how
/// fast it turns, how many sparks wander and how quick the ring packets run.
/// With nothing going on it settles: a slow turn and a rare idle thought.
/// Busy while JARVIS is working (`active`).
///
/// Every node also has an eye position and a spike length, so the same
/// network opens into an eye (iris fibres, a pupil that glances around)
/// when the camera watches, and bristles into spikes for Barn Door. The
/// change runs core first, outer rings last, over a second or two.
final class NeuralNetwork {
    /// One brain for every core on screen. A theme change rebuilds the views
    /// (Barn Door swaps the palette), so the network lives outside them and
    /// keeps morphing instead of starting over.
    static let shared = NeuralNetwork()

    struct Node {
        let base: SIMD3<Double>     // position before rotation (unit circle for ring nodes)
        let layer: Int              // 0 core, 1 middle, 2 outer shell; 3 and 4 the orbit rings
        let eye: SIMD3<Double>      // where it sits in the eye, in view space
        let spike: Double           // radial stretch in the alert pose
        let seed: Double
        var glow: Double = 0
    }

    enum EdgeKind: CaseIterable { case inner, outer, axon, orbit }

    struct Signal {
        enum Kind { case spark, thought, orbit }
        let from: Int, to: Int
        var progress: Double
        let speed: Double
        let kind: Kind
        var direction = 1           // thoughts: +1 outward, -1 inward
    }

    static let shellRadii = [0.15, 0.29, 0.42]
    static let shellCounts = [16, 40, 72]
    static let ringCount = 30
    static let ringRadius = 0.52

    private(set) var nodes: [Node] = []
    private(set) var edges: [(a: Int, b: Int, kind: EdgeKind)] = []
    private var neighbours: [[Int]] = []
    private(set) var signals: [Signal] = []
    /// Mood mix (thinking, watching, alert) per layer; inner layers change first.
    private(set) var mix = [SIMD3<Double>](repeating: [1, 0, 0], count: 5)
    private(set) var look = SIMD2<Double>(0, 0)
    private var lookTarget = SIMD2<Double>(0, 0)
    private var nextLook = 0.0
    private var nextThought = 1.5
    private var clock = 20.0
    private var yaw = 0.6, ringSpin = 0.0
    private var lastTime: Double?
    private var rng = SplitMix(seed: 0x0A6E_0F17)
    /// Real signals waiting to fire (see `fire(from:to:)`).
    private var pending: [(start: Int, inward: Bool)] = []
    /// The traffic's recent pace, 0 (quiet) to 1 (busy): each real signal
    /// lifts it, and it eases back over ten seconds or so.
    private(set) var activity = 0.0

    /// A real signal between Core and a device (`TrafficFeed`): one thought.
    /// Each device has its own neuron, so the same link always lights the
    /// same spot: Core sending fires from the core outward; something
    /// arriving at Core comes in from the outer shell, from that device's
    /// side. Bursts are capped so a busy poll doesn't turn it into static.
    func fire(from source: String, to target: String) {
        guard pending.count < 4 else { return }
        let inward = target == "core"
        let device = inward ? source : target
        let layer = inward ? 2 : 0
        var hash: UInt64 = 5381
        for byte in device.utf8 { hash = (hash &* 33) &+ UInt64(byte) }
        let members = nodes.indices.filter { nodes[$0].layer == layer }
        pending.append((members[Int(hash % UInt64(members.count))], inward))
        activity = min(1, activity + 0.18)
    }

    private init() {
        build()
    }

    // MARK: Building

    private func build() {
        let golden = Double.pi * (3 - 5.0.squareRoot())
        var built: [Node] = []
        for layer in 0..<3 {
            let count = Self.shellCounts[layer], radius = Self.shellRadii[layer]
            // A Fibonacci sphere per shell, each a touch ragged.
            let points: [SIMD3<Double>] = (0..<count).map { i in
                let y = 1 - (Double(i) + 0.5) / Double(count) * 2
                let ring = (1 - y * y).squareRoot()
                let theta = golden * Double(i) + Double(layer)
                return SIMD3(cos(theta) * ring, y, sin(theta) * ring) * radius * (0.94 + 0.12 * rng.next01())
            }
            // The eye: shells become the pupil rim, the inner iris and the outer
            // iris, in the order their points sit around the view axis, so the
            // wires between shells become iris fibres.
            let order = points.indices.sorted { atan2(points[$0].y, points[$0].x) < atan2(points[$1].y, points[$1].x) }
            var eye = [SIMD3<Double>](repeating: .zero, count: count)
            for (rank, index) in order.enumerated() {
                let angle = -Double.pi + 2 * .pi * (Double(rank) + 0.5) / Double(count)
                let r = [0.085, 0.18, 0.29][layer] * (layer == 0 ? 1 : 0.9 + 0.16 * rng.next01())
                eye[index] = SIMD3(cos(angle) * r, sin(angle) * r, [0.10, 0.06, 0.02][layer])
            }
            for i in 0..<count {
                let roll = rng.next01()
                let spike: Double
                switch layer {
                case 0: spike = 0.72
                case 1: spike = 0.9 + 0.1 * roll
                default: spike = roll < 0.34 ? 1.42 + 0.18 * rng.next01() : (roll < 0.62 ? 1.08 : 0.84)
                }
                built.append(Node(base: points[i], layer: layer, eye: eye[i], spike: spike, seed: rng.next01()))
            }
        }
        for ring in 0..<2 {
            for i in 0..<Self.ringCount {
                let angle = 2 * .pi * Double(i) / Double(Self.ringCount)
                // Ring 0 becomes the eyelids (an almond, pointed at the corners);
                // ring 1 a circle around the eye, placed live in pose().
                let lid = SIMD3(0.56 * cos(angle), 0.33 * sin(angle) * pow(1 - abs(cos(angle)), 0.35), 0)
                built.append(Node(base: SIMD3(cos(angle), 0, sin(angle)), layer: 3 + ring, eye: ring == 0 ? lid : .zero,
                                  spike: i % 2 == 0 ? 1.24 : 1.0, seed: rng.next01()))
            }
        }
        nodes = built

        var kinds: [[Int]: EdgeKind] = [:]
        func link(_ i: Int, _ j: Int, _ kind: EdgeKind) {
            let key = [min(i, j), max(i, j)]
            if kinds[key] == nil { kinds[key] = kind }
        }
        func distance(_ a: Int, _ b: Int) -> Double { simd_length(built[a].base - built[b].base) }
        let shells = (0..<3).map { layer in built.indices.filter { built[$0].layer == layer } }
        for (layer, members) in shells.enumerated() {
            // Each node to its three nearest on the same shell...
            for i in members {
                for j in members.filter({ $0 != i }).sorted(by: { distance($0, i) < distance($1, i) }).prefix(3) {
                    link(i, j, layer == 2 ? .outer : .inner)
                }
            }
            // ...and axons both ways to the next shell out, so every node can pass a thought on.
            guard layer < 2 else { continue }
            let next = shells[layer + 1]
            for i in members { if let j = next.min(by: { distance($0, i) < distance($1, i) }) { link(i, j, .axon) } }
            for j in next { if let i = members.min(by: { distance($0, j) < distance($1, j) }) { link(i, j, .axon) } }
        }
        for ring in 0..<2 {
            let first = built.count - (2 - ring) * Self.ringCount
            for i in 0..<Self.ringCount { link(first + i, first + (i + 1) % Self.ringCount, .orbit) }
        }
        edges = kinds.map { (a: $0.key[0], b: $0.key[1], kind: $0.value) }
            .sorted { $0.a == $1.a ? $0.b < $1.b : $0.a < $1.a }
        neighbours = Array(repeating: [], count: built.count)
        for edge in edges {
            neighbours[edge.a].append(edge.b)
            neighbours[edge.b].append(edge.a)
        }
        // Data packets that circle the rings forever, three per ring, opposite ways.
        for ring in 0..<2 {
            let first = built.count - (2 - ring) * Self.ringCount
            for k in 0..<3 {
                let from = first + k * 10
                let to = first + (k * 10 + (ring == 0 ? 1 : Self.ringCount - 1)) % Self.ringCount
                signals.append(Signal(from: from, to: to, progress: rng.next01(), speed: 1.7, kind: .orbit))
            }
        }
    }

    // MARK: Pose

    private static func rotateY(_ p: SIMD3<Double>, _ a: Double) -> SIMD3<Double> {
        SIMD3(p.x * cos(a) + p.z * sin(a), p.y, -p.x * sin(a) + p.z * cos(a))
    }

    private static func rotateX(_ p: SIMD3<Double>, _ a: Double) -> SIMD3<Double> {
        SIMD3(p.x, p.y * cos(a) - p.z * sin(a), p.y * sin(a) + p.z * cos(a))
    }

    private static func rotateZ(_ p: SIMD3<Double>, _ a: Double) -> SIMD3<Double> {
        SIMD3(p.x * cos(a) - p.y * sin(a), p.x * sin(a) + p.y * cos(a), p.z)
    }

    /// Every node in view space (x right, y down, z toward the viewer), each
    /// layer blended between its brain, eye and spike positions by its mix.
    func pose() -> [SIMD3<Double>] {
        let pitch = 0.30 + 0.10 * sin(clock * 2 * .pi / 17)
        func view(_ p: SIMD3<Double>) -> SIMD3<Double> { Self.rotateX(Self.rotateY(p, yaw), pitch) }
        var out: [SIMD3<Double>] = []
        out.reserveCapacity(nodes.count)
        for node in nodes {
            let w = mix[node.layer]
            var local: SIMD3<Double>
            var eye = node.eye
            if node.layer < 3 {
                // The shells breathe against each other: a slow sway each, out of step.
                let k = Double(node.layer)
                local = Self.rotateY(node.base, 0.32 * sin(clock * 2 * .pi / (8 + 5 * k) + k * 2.1))
                eye.x += look.x
                eye.y += look.y
            } else {
                let ring = node.layer - 3
                let start = atan2(node.base.z, node.base.x)
                let a = start + (ring == 0 ? ringSpin : 1.3 - ringSpin * 0.8)
                local = SIMD3(cos(a), 0, sin(a)) * Self.ringRadius
                local = Self.rotateX(local, ring == 0 ? 1.2 : -1.05)
                local = Self.rotateZ(local, ring == 0 ? 0.35 : -0.55 + 0.2 * sin(clock * 2 * .pi / 23))
                if ring == 1 {
                    let e = start + ringSpin * 0.15
                    eye = SIMD3(cos(e), sin(e), -0.02) * 0.62
                }
            }
            let tremble = node.spike + 0.045 * sin(clock * 13 + node.seed * 40)
            let brain = view(local)
            var spiked = view(local * tremble)
            if node.layer >= 3 {
                // Alert rings: jagged crowns facing you, spinning opposite ways.
                let ring = node.layer - 3
                let a = atan2(node.base.z, node.base.x) + (ring == 0 ? ringSpin : -ringSpin) * 0.6
                let r = (ring == 0 ? 0.50 : 0.60) * (node.spike > 1.1 ? 1.12 : 0.9) * (1 + 0.02 * sin(clock * 11 + node.seed * 30))
                spiked = SIMD3(cos(a) * r, sin(a) * r, -0.05)
            }
            out.append(brain * w.x + eye * w.y + spiked * w.z)
        }
        return out
    }

    // MARK: Simulation

    /// Jump straight to a mood (Reduce Motion, Low Power, the still frame).
    func settle(_ mood: NeuralMood) {
        mix = Array(repeating: mood.target, count: mix.count)
        look = .zero
    }

    /// Advance to `time` (seconds, any steadily increasing clock). Several
    /// views can call this each frame; only the first moves it on.
    func step(to time: Double, mood: NeuralMood, active: Bool) {
        guard let last = lastTime else { lastTime = time; return }
        guard time > last else { return }
        lastTime = time
        let dt = min(0.1, time - last)
        clock += dt

        // Morph: the core leads, the outer ring follows about a second later.
        for (layer, rate) in [3.2, 2.5, 1.9, 1.5, 1.3].enumerated() {
            mix[layer] += (mood.target - mix[layer]) * min(1, dt * rate)
        }
        let w = mix[2]
        activity = max(0, activity - dt * activity * 0.12 - dt * 0.004)
        // How lively the brain is: calm when the house is quiet, quicker with
        // traffic, quickest while JARVIS is working.
        let pace = active ? 1.4 : 0.35 + 0.95 * activity
        // Still in the eye (it faces you), restless in the alert.
        yaw += dt * 2 * .pi / 32 * (w.x * pace + 0.1 * w.y + 2.6 * w.z)
        ringSpin += dt * (0.45 * w.x * pace + 0.15 * w.y + 1.6 * w.z)

        // The eye glances around: a quick move, then a hold of 0.8–3.4 s.
        if clock >= nextLook {
            lookTarget = SIMD2((rng.next01() - 0.5) * 0.12, (rng.next01() - 0.5) * 0.07)
            nextLook = clock + 0.8 + rng.next01() * 2.6
        }
        look += (lookTarget - look) * min(1, dt * 9)

        for i in nodes.indices { nodes[i].glow = max(0, nodes[i].glow - dt * 1.5) }
        var arrived: [Signal] = []
        signals = signals.compactMap { signal in
            var s = signal
            s.progress += dt * s.speed * (s.kind == .orbit && mood == .thinking ? pace : 1)
            if s.progress >= 1 { arrived.append(s); return nil }
            return s
        }
        let alert = mood == .alert
        for s in arrived {
            nodes[s.to].glow = s.kind == .orbit ? max(nodes[s.to].glow, 0.55) : 1
            switch s.kind {
            case .orbit:
                if let next = neighbours[s.to].first(where: { $0 != s.from }) {
                    signals.append(Signal(from: s.to, to: next, progress: s.progress - 1, speed: s.speed, kind: .orbit))
                }
            case .thought:
                let layer = nodes[s.to].layer
                let onward = neighbours[s.to].filter { nodes[$0].layer == layer + s.direction }
                if !onward.isEmpty {
                    for j in pick(onward, 2) { send(s.to, j, .thought, direction: s.direction, alert: alert) }
                } else if !alert {
                    // Reached the last shell: it spreads across it a little and fades
                    // (in the alert it stops at the spike's tip).
                    let across = neighbours[s.to].filter { nodes[$0].layer == layer }
                    for j in pick(across, 2) { send(s.to, j, .spark, alert: alert) }
                }
            case .spark:
                let roll = rng.next01()
                let fanOut = roll < 0.35 ? 0 : (roll < 0.88 ? 1 : 2)
                let onShells = neighbours[s.to].filter { sparkable(s.to, $0, alert: alert) }
                for j in pick(onShells, fanOut) { send(s.to, j, .spark, alert: alert) }
            }
        }

        // Real signals first: one per frame, so a batch reads as a ripple.
        if !pending.isEmpty, mood == .thinking {
            let (start, inward) = pending.removeFirst()
            nodes[start].glow = 1
            let onward = neighbours[start].filter { nodes[$0].layer == nodes[start].layer + (inward ? -1 : 1) }
            for j in pick(onward, 2) { send(start, j, .thought, direction: inward ? -1 : 1, alert: false) }
        } else if mood != .thinking {
            pending.removeAll()
        }

        // A new thought: from the core outward (inward from the iris to the
        // pupil while watching).
        if clock >= nextThought {
            let inward = mood == .watching
            let layer = inward ? 2 : 0
            let starts = nodes.indices.filter { nodes[$0].layer == layer }
            let start = starts[Int(rng.next01() * Double(starts.count)) % starts.count]
            nodes[start].glow = 1
            let onward = neighbours[start].filter { nodes[$0].layer == layer + (inward ? -1 : 1) }
            for j in pick(onward, alert ? 3 : 2) { send(start, j, .thought, direction: inward ? -1 : 1, alert: alert) }
            let (low, spread): (Double, Double)
            switch mood {
            case .alert: (low, spread) = (0.45, 0.45)
            case .watching: (low, spread) = (1.2, 1.2)
            // Quiet: a rare thought of its own; real traffic does the rest.
            case .thinking: (low, spread) = active ? (0.5, 0.6) : (7, 7)
            }
            nextThought = clock + low + rng.next01() * spread
        }
        // Stray sparks between thoughts.
        let sparkRate: Double
        switch mood {
        case .alert: sparkRate = 7
        case .watching: sparkRate = 1.2
        case .thinking: sparkRate = active ? 5 : 0.25 + 2.5 * activity
        }
        if rng.next01() < sparkRate * dt {
            let shellCount = Self.shellCounts.reduce(0, +)
            let from = Int(rng.next01() * Double(shellCount)) % shellCount
            if let to = pick(neighbours[from].filter { sparkable(from, $0, alert: alert) }, 1).first {
                nodes[from].glow = max(nodes[from].glow, 0.7)
                send(from, to, .spark, alert: alert)
            }
        }
    }

    /// Sparks stay on the shells; in the alert they keep off the stretched outer mesh.
    private func sparkable(_ from: Int, _ to: Int, alert: Bool) -> Bool {
        nodes[to].layer < 3 && !(alert && nodes[from].layer == 2 && nodes[to].layer == 2)
    }

    private func send(_ from: Int, _ to: Int, _ kind: Signal.Kind, direction: Int = 1, alert: Bool) {
        guard signals.count < 130 else { return }
        let base = kind == .thought ? 2.4 + rng.next01() : 1.6 + rng.next01() * 1.8
        signals.append(Signal(from: from, to: to, progress: 0, speed: base * (alert ? 1.7 : 1),
                              kind: kind, direction: direction))
    }

    /// Up to `count` distinct picks from `options`.
    private func pick(_ options: [Int], _ count: Int) -> [Int] {
        var pool = options, out: [Int] = []
        while out.count < count, !pool.isEmpty {
            out.append(pool.remove(at: Int(rng.next01() * Double(pool.count)) % pool.count))
        }
        return out
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
    var mood: NeuralMood = .thinking
    private let network = NeuralNetwork.shared

    // Thinking: the palette's reactor wiring, gold impulses (the films' JARVIS).
    private static let gold = Color(red: 1.0, green: 0.68, blue: 0.22)
    private static let hot = Color(red: 1.0, green: 0.90, blue: 0.62)
    // Watching: the camera orb's greens.
    private static let watchLine = Color(red: 0.20, green: 0.85, blue: 0.45)
    private static let watchImpulse = Color(red: 0.55, green: 1.0, blue: 0.70)
    private static let watchHot = Color(red: 0.85, green: 1.0, blue: 0.90)
    // Alert: the TV's red-alert reds.
    private static let alertLine = Color(red: 1.0, green: 0.18, blue: 0.16)
    private static let alertImpulse = Color(red: 1.0, green: 0.42, blue: 0.28)
    private static let alertHot = Color(red: 1.0, green: 0.80, blue: 0.70)

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !moving)) { context in
            let now = context.date.timeIntervalSinceReferenceDate
            Canvas { canvas, canvasSize in
                if moving { network.step(to: now, mood: mood, active: active) } else { network.settle(mood) }
                draw(in: &canvas, size: canvasSize)
            }
        }
    }

    private func blend(_ colors: (Color, Color, Color), _ w: SIMD3<Double>, in env: EnvironmentValues) -> Color {
        let a = colors.0.resolve(in: env), b = colors.1.resolve(in: env), c = colors.2.resolve(in: env)
        let x = Float(w.x), y = Float(w.y), z = Float(w.z)
        return Color(Color.Resolved(red: a.red * x + b.red * y + c.red * z,
                                    green: a.green * x + b.green * y + c.green * z,
                                    blue: a.blue * x + b.blue * y + c.blue * z))
    }

    private func project(_ p: SIMD3<Double>, size: CGSize, lift: Double) -> (CGPoint, Double) {
        let scale = Double(min(size.width, size.height)) * 1.15 / (1.6 - p.z * 0.6)
        let point = CGPoint(x: size.width / 2 + p.x * scale, y: size.height / 2 + p.y * scale)
        return (point, max(0.15, min(1, p.z + 0.55 + lift)))  // depth: ~0 at the back, 1 at the front
    }

    private func draw(in canvas: inout GraphicsContext, size: CGSize) {
        let env = canvas.environment
        let mix = network.mix
        let w = mix[2]
        let line = blend((JarvisTheme.reactor, Self.watchLine, Self.alertLine), w, in: env)
        let impulse = blend((Self.gold, Self.watchImpulse, Self.alertImpulse), w, in: env)
        let hot = blend((Self.hot, Self.watchHot, Self.alertHot), w, in: env)
        let nodes = network.nodes
        let lift = 0.3 * w.y   // the eye faces you, so it's lit like the front
        let projected = network.pose().map { project($0, size: size, lift: lift) }
        // A hair heavier than it was (/240): reads over the vivid backdrop.
        let stroke = max(0.7, size.width / 185)
        let center = CGPoint(x: size.width / 2, y: size.height / 2)
        let unit = Double(min(size.width, size.height)) * 1.15 / 1.6

        // Soft halo so it sits in light, not on nothing.
        canvas.fill(Path(ellipseIn: CGRect(origin: .zero, size: size)), with: .radialGradient(
            Gradient(colors: [line.opacity((active ? 0.22 : 0.15) + 0.12 * w.z), .clear]),
            center: center, startRadius: 0, endRadius: size.width * 0.48))

        // The eye's pupil: open (no dark fill, Nick's call), light around its
        // rim of nodes, following the glance.
        let seeing = mix[0].y
        if seeing > 0.01 {
            let look = network.look
            let pupil = CGPoint(x: center.x + look.x * unit, y: center.y + look.y * unit)
            let r = 0.075 * unit
            canvas.fill(Path(ellipseIn: CGRect(x: pupil.x - r * 4, y: pupil.y - r * 4, width: r * 8, height: r * 8)),
                        with: .radialGradient(Gradient(colors: [impulse.opacity(0.22 * seeing), .clear]),
                                              center: pupil, startRadius: r, endRadius: r * 4))
        }

        canvas.blendMode = .plusLighter
        // The layers as glass: a faint outline per shell and light in the core,
        // so it reads as spheres inside spheres, not one tangle.
        let layered = mix[1].x
        if layered > 0.01 {
            canvas.fill(Path(ellipseIn: CGRect(x: center.x - 0.2 * unit, y: center.y - 0.2 * unit, width: 0.4 * unit, height: 0.4 * unit)),
                        with: .radialGradient(Gradient(colors: [impulse.opacity((active ? 0.30 : 0.20) * layered), .clear]),
                                              center: center, startRadius: 0, endRadius: 0.2 * unit))
            for (k, radius) in NeuralNetwork.shellRadii.enumerated() {
                let r = radius * unit
                canvas.stroke(Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)),
                              with: .color(line.opacity([0.20, 0.14, 0.10][k] * layered)), lineWidth: stroke)
            }
        }
        // Barn Door: the long nodes become spikes out from the middle.
        if w.z > 0.01 {
            for (i, node) in nodes.enumerated() where node.layer == 2 && node.spike > 1.3 {
                let (tip, depth) = projected[i]
                let root = CGPoint(x: center.x + (tip.x - center.x) * 0.2, y: center.y + (tip.y - center.y) * 0.2)
                var spike = Path()
                spike.move(to: root)
                spike.addLine(to: tip)
                canvas.stroke(spike, with: .linearGradient(Gradient(colors: [line.opacity(0), line.opacity((0.5 + 0.5 * depth) * w.z)]),
                                                           startPoint: root, endPoint: tip),
                              style: StrokeStyle(lineWidth: stroke * 1.6, lineCap: .round))
            }
        }
        // Wires, batched by kind and depth: a dozen strokes, not four hundred.
        func bucket(_ depth: Double) -> Int { depth < 0.45 ? 0 : (depth < 0.75 ? 1 : 2) }
        var paths = [NeuralNetwork.EdgeKind: [Path]]()
        for kind in NeuralNetwork.EdgeKind.allCases { paths[kind] = [Path(), Path(), Path()] }
        func weight(_ kind: NeuralNetwork.EdgeKind) -> Double {
            switch kind {
            case .inner: return 1.25 - 0.85 * w.z           // the inner shells carry the light
            case .outer: return 0.7 - 0.7 * w.z             // spikes read clearer without the outer mesh
            case .axon: return 0.8 - 0.3 * w.z + 0.5 * w.y   // iris fibres
            case .orbit: return 0.9
            }
        }
        var warm: [(Path, Double)] = []
        for edge in network.edges {
            let (pa, da) = projected[edge.a], (pb, db) = projected[edge.b]
            let b = bucket((da + db) / 2)
            paths[edge.kind]![b].move(to: pa)
            paths[edge.kind]![b].addLine(to: pb)
            let heat = max(nodes[edge.a].glow, nodes[edge.b].glow)
            if heat > 0.05 && edge.kind != .orbit {
                var path = Path()
                path.move(to: pa)
                path.addLine(to: pb)
                warm.append((path, heat * min(1, weight(edge.kind))))
            }
        }
        for (kind, buckets) in paths {
            let weight = weight(kind)
            for (b, path) in buckets.enumerated() {
                let depth = [0.3, 0.6, 0.88][b]
                canvas.stroke(path, with: .color(line.opacity((0.10 + 0.32 * depth) * weight)), lineWidth: stroke)
            }
        }
        for (path, heat) in warm {
            canvas.stroke(path, with: .color(impulse.opacity(0.5 * heat)), lineWidth: stroke * 1.4)
        }

        for signal in network.signals {
            let (pa, da) = projected[signal.from], (pb, db) = projected[signal.to]
            let f = min(1, max(0, signal.progress))
            let head = CGPoint(x: pa.x + (pb.x - pa.x) * f, y: pa.y + (pb.y - pa.y) * f)
            let tailF = max(0, f - 0.4)
            let tail = CGPoint(x: pa.x + (pb.x - pa.x) * tailF, y: pa.y + (pb.y - pa.y) * tailF)
            var trail = Path()
            trail.move(to: tail)
            trail.addLine(to: head)
            let depth = da + (db - da) * f
            let strength = signal.kind == .orbit ? 0.7 : 1
            canvas.stroke(trail, with: .linearGradient(Gradient(colors: [impulse.opacity(0), impulse.opacity(0.95 * depth * strength)]),
                                                       startPoint: tail, endPoint: head),
                          style: StrokeStyle(lineWidth: stroke * 2, lineCap: .round))
            let r = stroke * (signal.kind == .thought ? 2.6 : 2.0)
            canvas.fill(Path(ellipseIn: CGRect(x: head.x - r, y: head.y - r, width: r * 2, height: r * 2)),
                        with: .color(hot.opacity(depth * strength)))
        }

        // Nodes: little data boxes, turning into diamonds as it bristles; batched by depth.
        var boxes = [Path(), Path(), Path()]
        var lit: [(CGPoint, Double, Double, Double)] = []
        for (i, node) in nodes.enumerated() {
            let (p, depth) = projected[i]
            let ring = node.layer >= 3
            let box = max(1.0, size.width / 125)   // small nodes, heavier wires * (0.6 + 0.6 * depth) * (ring ? 0.7 : 1) * (1 + node.glow * 0.8)
            let turn = Double.pi / 4 * mix[node.layer].z
            var shape = Path()
            for corner in 0..<4 {
                let a = turn + Double.pi / 4 + Double(corner) * .pi / 2
                let q = CGPoint(x: p.x + cos(a) * box * 0.707, y: p.y + sin(a) * box * 0.707)
                if corner == 0 { shape.move(to: q) } else { shape.addLine(to: q) }
            }
            shape.closeSubpath()
            boxes[bucket(depth)].addPath(shape)
            if node.glow > 0.02 { lit.append((p, box, node.glow, depth)) }
        }
        for (b, path) in boxes.enumerated() {
            canvas.fill(path, with: .color(line.opacity(0.25 + 0.55 * [0.3, 0.6, 0.88][b])))
        }
        for (p, box, glow, depth) in lit {
            let r = box / 2
            canvas.fill(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: box, height: box)),
                        with: .color(hot.opacity(glow * depth)))
            let g = box * 3.2
            canvas.fill(Path(ellipseIn: CGRect(x: p.x - g, y: p.y - g, width: g * 2, height: g * 2)),
                        with: .radialGradient(Gradient(colors: [impulse.opacity(0.45 * glow * depth), .clear]),
                                              center: p, startRadius: 0, endRadius: g))
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

// MARK: - Liquid glass

extension View {
    /// The background every card and panel uses: the theme's panel gradient,
    /// or frosted Liquid Glass while Experimenting has its glass switch on.
    func jarvisSurface<S: Shape>(in shape: S) -> some View {
        modifier(JarvisSurface(shape: shape))
    }
}

private struct JarvisSurface<S: Shape>: ViewModifier {
    let shape: S
    @AppStorage(JarvisPalette.storageKey) private var palette = JarvisPalette.arc.rawValue
    @AppStorage(Experiment.glass.storageKey) private var glassOn = Experiment.glass.defaultOn

    func body(content: Content) -> some View {
        if palette == JarvisPalette.experimenting.rawValue && glassOn {
            content.background { FrostedGlass(shape: shape) }
        } else {
            // Not jarvisSurface(in:) itself: that recursed forever (5.49's
            // launch crash with glass switched off).
            content.background(JarvisTheme.panelGradient, in: shape)
        }
    }
}

/// The card glass. On iOS 26+ it's Apple's Liquid Glass and nothing else, so
/// it follows the phone's own setting (Settings > Display & Brightness >
/// Liquid Glass: Clear or Tinted) and Reduce Transparency, like the system's
/// own glass. Older iOS gets a frosted stand-in: a blur of the plasma, a
/// whisper of the panel colour, a sheen and a bright rim.
private struct FrostedGlass<S: Shape>: View {
    let shape: S
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        if #available(iOS 26, macCatalyst 26, *) {
            Color.clear.glassEffect(.regular, in: shape)
        } else {
            let dark = scheme == .dark
            ZStack {
                shape.fill(.regularMaterial)
                shape.fill(JarvisTheme.panel.opacity(dark ? 0.30 : 0.18))
                shape.fill(LinearGradient(colors: [.white.opacity(dark ? 0.10 : 0.30), .white.opacity(0)],
                                          startPoint: .topLeading, endPoint: .center))
                shape.stroke(LinearGradient(colors: [.white.opacity(0.45), .white.opacity(0.06), .white.opacity(0.18)],
                                            startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 1)
            }
        }
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
