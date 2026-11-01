import SwiftUI

/// The "Experimenting" theme's lab: visual ideas Nick can try on his own
/// devices before any of them graduates into a real theme. Picking the
/// Experimenting palette (Spider-Man's colours underneath) turns on whichever
/// experiments are switched on in Settings; every other theme ignores them.
///
/// Rules for anything added here (Nick's taste): slow drift, never pulsing or
/// flashing; colour as glow on a dark base, never a red background; Reduce
/// Motion and Low Power Mode get the still frame.
enum Experiment: String, CaseIterable, Identifiable {
    case plasma, orb3D

    var id: String { rawValue }

    var name: String {
        switch self {
        case .plasma: return "Frosted plasma"
        case .orb3D: return "3D orb"
        }
    }

    var blurb: String {
        switch self {
        case .plasma: return "Slow red and blue light drifting behind frosted glass."
        case .orb3D: return "The reactor as a lit sphere with tilted orbiting rings."
        }
    }

    /// Default on; only read while the Experimenting theme is picked
    /// (`JarvisBackdrop`, `JarvisCoreMark`).
    var storageKey: String { "jarvis.experiment.\(rawValue)" }
}

/// Blurred blobs of the palette's red and blue, wandering on slow Lissajous
/// paths (40–90 s loops) over the deep background, with frosted glass on top.
/// Drawn in one `Canvas` from radial gradients (no blur filter), so it stays
/// cheap enough to sit behind every screen.
struct PlasmaBackdrop: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var scheme

    private struct Blob {
        let color: Color
        let radius: Double      // fraction of the longer side
        let periods: (Double, Double)
        let phase: Double
        let opacity: Double
    }

    private var blobs: [Blob] {
        let red = JarvisTheme.cyan, blue = JarvisTheme.blue, ice = JarvisTheme.reactor
        let dark = scheme == .dark
        return [
            Blob(color: red, radius: 0.55, periods: (71, 53), phase: 0.0, opacity: dark ? 0.62 : 0.30),
            Blob(color: blue, radius: 0.65, periods: (89, 61), phase: 1.7, opacity: dark ? 0.70 : 0.34),
            Blob(color: red, radius: 0.38, periods: (47, 79), phase: 3.1, opacity: dark ? 0.48 : 0.22),
            Blob(color: ice, radius: 0.42, periods: (59, 43), phase: 4.4, opacity: dark ? 0.34 : 0.18),
            Blob(color: blue, radius: 0.34, periods: (41, 67), phase: 5.6, opacity: dark ? 0.50 : 0.24),
        ]
    }

    private var moving: Bool { !reduceMotion && !ProcessInfo.processInfo.isLowPowerModeEnabled }

    var body: some View {
        ZStack {
            LinearGradient(colors: [JarvisTheme.background, JarvisTheme.backgroundDeep],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            TimelineView(.animation(minimumInterval: 1.0 / 20, paused: !moving)) { context in
                let t = moving ? context.date.timeIntervalSinceReferenceDate : 0
                Canvas { canvas, size in
                    let side = max(size.width, size.height)
                    for blob in blobs {
                        let x = 0.5 + 0.42 * sin(2 * .pi * t / blob.periods.0 + blob.phase)
                        let y = 0.5 + 0.40 * cos(2 * .pi * t / blob.periods.1 + blob.phase * 1.3)
                        let r = side * blob.radius
                        let center = CGPoint(x: x * size.width, y: y * size.height)
                        let rect = CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)
                        canvas.fill(Path(ellipseIn: rect), with: .radialGradient(
                            Gradient(colors: [blob.color.opacity(blob.opacity), blob.color.opacity(blob.opacity * 0.35), .clear]),
                            center: center, startRadius: 0, endRadius: r))
                    }
                }
            }
            // The frost: a thin material evens the colour out like glass, then a
            // whisper of the background keeps text contrast where it was.
            Rectangle().fill(.ultraThinMaterial).opacity(0.35)
            JarvisTheme.backgroundDeep.opacity(scheme == .dark ? 0.18 : 0.08)
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

/// The reactor as a 3D object: a shaded glass sphere with a hot core and
/// three rings on tilted orbits (each a `rotation3DEffect`, so the near side
/// sweeps past in front and the far side behind). Speeds match the classic
/// mark's ring periods so it feels like the same JARVIS.
struct OrbitOrb: View {
    var size: CGFloat
    var moving: Bool
    var active = false

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !moving)) { context in
            orb(seconds: moving ? context.date.timeIntervalSinceReferenceDate : 0)
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
        // The whole system precesses slowly so the tilts never look frozen.
        let wobble = 8 * sin(2 * .pi * seconds / 23)
        return ZStack {
            Circle().fill(RadialGradient(colors: [JarvisTheme.reactor.opacity(active ? 0.30 : 0.20), .clear],
                                         center: .center, startRadius: core * 0.3, endRadius: size / 2))
            ring(seconds, tilt: (68 + wobble, 12), period: 16, inset: size * 0.06, width: max(1.5, size / 60), opacity: 0.55)
            // The sphere: lit from the upper left, rim-lit, a white-hot centre.
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
    @AppStorage private var on: Bool

    init(experiment: Experiment) {
        self.experiment = experiment
        _on = AppStorage(wrappedValue: true, experiment.storageKey)
    }

    var body: some View {
        Toggle(isOn: $on) {
            VStack(alignment: .leading, spacing: 2) {
                Text(experiment.name).font(.subheadline.weight(.medium))
                Text(experiment.blurb).font(.caption).foregroundStyle(.secondary)
            }
        }
        .tint(JarvisTheme.reactor)
        .padding(.vertical, 4)
    }
}
