import SwiftUI

/// One signal between two parts of the house (`GET /api/traffic/stream`,
/// Core's brain/traffic.py): a request in, a push out, a call to the NAS.
struct TrafficSignal: Decodable, Identifiable {
    let seq: Int
    let at: Double
    let from: String
    let to: String
    let label: String
    var id: Int { seq }
}

/// The live traffic, shared: the Link map draws it, and while the Room's
/// neural core is on screen every signal fires one of its thoughts, so the
/// brain's timing is the house's real timing. Held open by whoever needs it
/// (`hold`/`release`); reconnects on its own.
@MainActor
final class TrafficFeed: ObservableObject {
    static let shared = TrafficFeed()

    struct Arrival: Identifiable {
        let signal: TrafficSignal
        /// When to draw it leaving (local clock): a batch is spread back out over its real timing.
        let start: Date
        var id: Int { signal.seq }
    }

    @Published private(set) var arrivals: [Arrival] = []
    @Published private(set) var connected = false
    private var holders = 0
    private var task: Task<Void, Never>?

    func hold(settings: AppSettings) {
        holders += 1
        guard task == nil else { return }
        task = Task { [weak self] in
            var delay: UInt64 = 2
            while !Task.isCancelled {
                do {
                    let client = try JarvisAPIClient.make(from: settings)
                    try await client.streamTraffic { batch in self?.receive(batch) }
                    delay = 2
                } catch {
                    delay = min(delay * 2, 60)
                }
                self?.connected = false
                try? await Task.sleep(nanoseconds: delay * 1_000_000_000)
            }
        }
    }

    func release() {
        holders = max(0, holders - 1)
        guard holders == 0 else { return }
        task?.cancel()
        task = nil
        connected = false
    }

    private func receive(_ batch: [TrafficSignal]) {
        connected = true
        let now = Date()
        let first = batch.first?.at ?? 0
        for signal in batch {
            let lag = min(0.4, max(0, signal.at - first))
            arrivals.append(Arrival(signal: signal, start: now.addingTimeInterval(lag)))
            NeuralNetwork.shared.fire(from: signal.from, to: signal.to)
        }
        arrivals.removeAll { now.timeIntervalSince($0.start) > 8 }
        if arrivals.count > 160 { arrivals.removeFirst(arrivals.count - 160) }
    }
}

/// Every endpoint in the house and the links between them, lighting up as
/// real signals cross: a packet runs along the link, the far end flashes
/// softly, and the last few signals are listed underneath. Something to
/// watch for a minute, then close.
struct LinkMapView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @ObservedObject private var feed = TrafficFeed.shared

    private struct Endpoint {
        let id: String, name: String, symbol: String, spot: CGPoint
    }

    /// Core in the middle; everything else around it, roughly where it lives.
    private static let endpoints: [Endpoint] = [
        .init(id: "core", name: "Core · Optiplex", symbol: "cpu", spot: CGPoint(x: 0.5, y: 0.5)),
        .init(id: "nas", name: "NAS · HA, Plex, arr", symbol: "externaldrive.connected.to.line.below", spot: CGPoint(x: 0.14, y: 0.5)),
        .init(id: "tv", name: "TV", symbol: "tv", spot: CGPoint(x: 0.5, y: 0.12)),
        .init(id: "ipad", name: "iPad", symbol: "ipad.landscape", spot: CGPoint(x: 0.86, y: 0.5)),
        .init(id: "iphone", name: "iPhone", symbol: "iphone", spot: CGPoint(x: 0.7, y: 0.88)),
        .init(id: "mac", name: "Mac", symbol: "laptopcomputer", spot: CGPoint(x: 0.3, y: 0.88)),
        .init(id: "internet", name: "Internet · Claude, APNs", symbol: "globe", spot: CGPoint(x: 0.82, y: 0.14)),
    ]

    private static let travel = 0.7   // seconds a packet takes along a link

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                GeometryReader { geo in
                    TimelineView(.animation(minimumInterval: 1.0 / 30)) { context in
                        ZStack {
                            Canvas { canvas, size in draw(&canvas, size: size, now: context.date) }
                            ForEach(Self.endpoints, id: \.id) { endpoint in
                                badge(endpoint, now: context.date)
                                    .position(x: endpoint.spot.x * geo.size.width, y: endpoint.spot.y * geo.size.height)
                            }
                        }
                    }
                }
                .frame(minHeight: 320)
                log
            }
            .padding(20)
            .background { JarvisBackdrop() }
            .navigationTitle("Link map")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Label(feed.connected ? "Live" : "Connecting…", systemImage: feed.connected ? "dot.radiowaves.left.and.right" : "wifi.exclamationmark")
                        .labelStyle(.titleAndIcon)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(feed.connected ? JarvisTheme.success : .secondary)
                }
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .onAppear { feed.hold(settings: settings) }
        .onDisappear { feed.release() }
    }

    private func spot(_ id: String, _ size: CGSize) -> CGPoint? {
        Self.endpoints.first { $0.id == id }.map { CGPoint(x: $0.spot.x * size.width, y: $0.spot.y * size.height) }
    }

    /// How lit a link or endpoint is: 1 the moment a packet lands, fading over ~2 s.
    private func heat(_ match: (TrafficSignal) -> Bool, now: Date) -> Double {
        var best = 0.0
        for arrival in feed.arrivals where match(arrival.signal) {
            let age = now.timeIntervalSince(arrival.start)
            guard age >= 0 else { continue }
            best = max(best, age < Self.travel ? 0.6 : max(0, 1 - (age - Self.travel) / 2))
        }
        return best
    }

    private func draw(_ canvas: inout GraphicsContext, size: CGSize, now: Date) {
        guard let core = spot("core", size) else { return }
        // The links: always faintly there, brighter while in use.
        for endpoint in Self.endpoints where endpoint.id != "core" {
            guard let end = spot(endpoint.id, size) else { continue }
            let lit = heat({ $0.from == endpoint.id || $0.to == endpoint.id }, now: now)
            var line = Path()
            line.move(to: core)
            line.addLine(to: end)
            canvas.stroke(line, with: .color(JarvisTheme.reactor.opacity(0.16 + 0.5 * lit)),
                          style: StrokeStyle(lineWidth: 1.5 + 2 * lit, lineCap: .round, dash: lit > 0.05 ? [] : [4, 6]))
        }
        // The packets: a bright head with a short tail, from sender to receiver.
        canvas.blendMode = .plusLighter
        for arrival in feed.arrivals {
            let f = now.timeIntervalSince(arrival.start) / Self.travel
            guard f >= 0, f <= 1, let a = spot(arrival.signal.from, size), let b = spot(arrival.signal.to, size) else { continue }
            let eased = 1 - pow(1 - f, 2)
            let head = CGPoint(x: a.x + (b.x - a.x) * eased, y: a.y + (b.y - a.y) * eased)
            let tailF = max(0, eased - 0.18)
            let tail = CGPoint(x: a.x + (b.x - a.x) * tailF, y: a.y + (b.y - a.y) * tailF)
            var trail = Path()
            trail.move(to: tail)
            trail.addLine(to: head)
            let color = arrival.signal.from == "core" ? JarvisTheme.blue : JarvisTheme.cyan
            canvas.stroke(trail, with: .linearGradient(Gradient(colors: [color.opacity(0), color]), startPoint: tail, endPoint: head),
                          style: StrokeStyle(lineWidth: 4, lineCap: .round))
            canvas.fill(Path(ellipseIn: CGRect(x: head.x - 5, y: head.y - 5, width: 10, height: 10)), with: .color(.white.opacity(0.9)))
            canvas.fill(Path(ellipseIn: CGRect(x: head.x - 16, y: head.y - 16, width: 32, height: 32)),
                        with: .radialGradient(Gradient(colors: [color.opacity(0.6), .clear]), center: head, startRadius: 0, endRadius: 16))
        }
    }

    private func badge(_ endpoint: Endpoint, now: Date) -> some View {
        // Lit when a packet lands on it (or leaves it).
        let lit = heat({ ($0.to == endpoint.id || $0.from == endpoint.id) }, now: now)
        let core = endpoint.id == "core"
        return VStack(spacing: 6) {
            ZStack {
                Circle().fill(JarvisTheme.cyan.opacity(0.10 + 0.35 * lit))
                Circle().strokeBorder(JarvisTheme.cyan.opacity(0.35 + 0.65 * lit), lineWidth: core ? 2 : 1.5)
                Image(systemName: endpoint.symbol)
                    .font(.system(size: core ? 28 : 22, weight: .medium))
                    .foregroundStyle(lit > 0.3 ? .white : JarvisTheme.cyan)
            }
            .frame(width: core ? 76 : 58, height: core ? 76 : 58)
            .shadow(color: JarvisTheme.cyan.opacity(0.7 * lit), radius: 14 * lit)
            Text(endpoint.name).font(.caption.weight(.semibold)).foregroundStyle(.secondary).fixedSize()
        }
    }

    /// The last few signals, newest first: who, to whom, what.
    private var log: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Just now").font(.footnote.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
            if feed.arrivals.isEmpty {
                Text(feed.connected ? "Quiet. Ask JARVIS something, or open another tab." : "Waiting for Core…")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(feed.arrivals.suffix(6).reversed()) { arrival in
                HStack(spacing: 8) {
                    Text("\(arrival.signal.from) → \(arrival.signal.to)").font(.footnote.monospaced().weight(.semibold))
                    Text(arrival.signal.label).font(.footnote.monospaced()).foregroundStyle(.secondary).lineLimit(1)
                    Spacer(minLength: 0)
                }
            }
        }
        .frame(maxWidth: .infinity, minHeight: 150, alignment: .topLeading)
        .padding(16)
        .jarvisSurface(in: .rect(cornerRadius: 20))
    }
}
