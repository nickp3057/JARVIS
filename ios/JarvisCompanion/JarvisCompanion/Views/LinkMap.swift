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
    /// The last few hundred signals with when they arrived, for tapping an
    /// endpoint and reading back what it's been doing.
    @Published private(set) var history: [Arrival] = []
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
            let arrival = Arrival(signal: signal, start: now.addingTimeInterval(lag))
            arrivals.append(arrival)
            history.append(arrival)
            NeuralNetwork.shared.fire(from: signal.from, to: signal.to)
        }
        arrivals.removeAll { now.timeIntervalSince($0.start) > 8 }
        if arrivals.count > 160 { arrivals.removeFirst(arrivals.count - 160) }
        if history.count > 400 { history.removeFirst(history.count - 400) }
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
    /// The endpoint tapped: its recent traffic is listed underneath.
    @State private var selected: String?

    private struct Endpoint {
        let id: String, name: String, symbol: String, spot: CGPoint
    }

    /// Core in the middle; everything else around it, roughly where it lives.
    private static let endpoints: [Endpoint] = [
        .init(id: "core", name: "JARVIS", symbol: "cpu", spot: CGPoint(x: 0.5, y: 0.5)),
        .init(id: "nas", name: "NAS · HA, Plex, arr", symbol: "externaldrive.connected.to.line.below", spot: CGPoint(x: 0.14, y: 0.5)),
        .init(id: "tv", name: "TV", symbol: "tv", spot: CGPoint(x: 0.5, y: 0.12)),
        .init(id: "ipad", name: "Door iPad", symbol: "ipad.landscape", spot: CGPoint(x: 0.86, y: 0.36)),
        .init(id: "nightstand", name: "Nightstand mini", symbol: "ipad", spot: CGPoint(x: 0.86, y: 0.64)),
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
                                    .contentShape(Rectangle())
                                    .onTapGesture {
                                        withAnimation(.easeInOut(duration: 0.2)) {
                                            selected = selected == endpoint.id ? nil : endpoint.id
                                        }
                                    }
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
        // The tapped one stays softly lit, so you can see which you're reading.
        let lit = max(heat({ ($0.to == endpoint.id || $0.from == endpoint.id) }, now: now),
                      selected == endpoint.id ? 0.55 : 0)
        let core = endpoint.id == "core"
        return Group {
            if core { coreBadge(lit: lit) } else { deviceBadge(endpoint, lit: lit) }
        }
    }

    /// Core is JARVIS himself: his orb (neural core on Experimenting, the
    /// live traffic firing it), his name, and the machine underneath.
    private func coreBadge(lit: Double) -> some View {
        VStack(spacing: 2) {
            JarvisCoreMark(size: 128, active: lit > 0.3)
                .shadow(color: JarvisTheme.cyan.opacity(0.5 * lit), radius: 18 * lit)
            Text("JARVIS").font(.headline.weight(.semibold)).tracking(3)
            Text("Core · Optiplex").font(.caption2).foregroundStyle(.secondary)
        }
        .fixedSize()
    }

    private func deviceBadge(_ endpoint: Endpoint, lit: Double) -> some View {
        let core = false
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
    @ViewBuilder private var log: some View {
        if let selected, let endpoint = Self.endpoints.first(where: { $0.id == selected }) {
            detail(endpoint)
        } else {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text("Just now").font(.footnote.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
                    Spacer()
                    Text("Tap anything to see its traffic").font(.caption).foregroundStyle(.secondary)
                }
                if feed.arrivals.isEmpty {
                    Text(feed.connected ? "Quiet. Ask JARVIS something, or open another tab." : "Waiting for Core…")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                ForEach(feed.arrivals.suffix(6).reversed()) { arrival in
                    HStack(spacing: 8) {
                        Text("\(arrival.signal.from) → \(arrival.signal.to)").font(.footnote.monospaced().weight(.semibold))
                        Text(TrafficWords.describe(arrival.signal)).font(.footnote).foregroundStyle(.secondary).lineLimit(1)
                        Spacer(minLength: 0)
                    }
                }
            }
            .frame(maxWidth: .infinity, minHeight: 150, alignment: .topLeading)
            .padding(16)
            .jarvisSurface(in: .rect(cornerRadius: 20))
        }
    }

    /// One endpoint's recent traffic, newest first: which way, what it was
    /// in plain words, the raw label, and how long ago. Holds still (it's a
    /// history, not the live flash), so a quick NAS blip can be read.
    private func detail(_ endpoint: Endpoint) -> some View {
        let items = feed.history.filter { $0.signal.from == endpoint.id || $0.signal.to == endpoint.id }.suffix(60).reversed()
        let title = endpoint.id == "core" ? "JARVIS · everything" : endpoint.name
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(title, systemImage: endpoint.symbol).font(.footnote.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
                Spacer()
                Text("last \(items.count)").font(.caption).foregroundStyle(.secondary)
                Button { withAnimation { selected = nil } } label: { Image(systemName: "xmark.circle.fill") }
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Close")
            }
            if items.isEmpty {
                Text("Nothing yet. It'll fill in as signals cross.").font(.footnote).foregroundStyle(.secondary)
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(Array(items)) { item in
                        let outgoing = item.signal.from == endpoint.id
                        let other = outgoing ? item.signal.to : item.signal.from
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Image(systemName: outgoing ? "arrow.up.right" : "arrow.down.left")
                                .font(.caption.weight(.bold))
                                .foregroundStyle(outgoing ? JarvisTheme.blue : JarvisTheme.cyan)
                                .frame(width: 14)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(TrafficWords.describe(item.signal)).font(.subheadline)
                                Text("\(outgoing ? "to" : "from") \(TrafficWords.name(other)) · \(item.signal.label)")
                                    .font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(1)
                            }
                            Spacer(minLength: 0)
                            Text(item.start, format: .relative(presentation: .numeric, unitsStyle: .narrow))
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .frame(maxHeight: 240)
        }
        .frame(maxWidth: .infinity, minHeight: 150, alignment: .topLeading)
        .padding(16)
        .jarvisSurface(in: .rect(cornerRadius: 20))
    }
}

/// Plain words for a traffic signal: what Core's raw label (a request path,
/// or a service and path it called) actually means.
enum TrafficWords {
    static func name(_ node: String) -> String {
        switch node {
        case "core": return "JARVIS"
        case "nas": return "the NAS"
        case "tv": return "the TV"
        case "iphone": return "your iPhone"
        case "ipad": return "the door iPad"
        case "nightstand": return "the nightstand iPad"
        case "mac": return "the Mac"
        case "internet": return "the internet"
        default: return node
        }
    }

    static func describe(_ signal: TrafficSignal) -> String {
        let label = signal.label.lowercased()
        let reply = signal.from == "core" && ["iphone", "ipad", "nightstand", "mac", "tv"].contains(signal.to)
        // Core calling out: the NAS's services and the internet.
        if signal.from == "core" && (signal.to == "nas" || signal.to == "internet") {
            return outbound(label)
        }
        if signal.from == "nas" && label.contains("home assistant") { return "Home Assistant: something in the house changed" }
        if label.hasPrefix("push") { return label.contains("ha") ? "Notification (through Home Assistant)" : "Notification pushed" }
        let what = request(label)
        return reply ? "Reply: \(what.prefix(1).lowercased() + what.dropFirst())" : what
    }

    /// A companion / TV request, by path.
    private static func request(_ path: String) -> String {
        let table: [(String, String)] = [
            ("camera/frame", "Camera frame"), ("camera/view", "Watching the camera"), ("camera", "Camera control"),
            ("chat", "Talking to JARVIS"), ("voice/stream", "Room voice, live"), ("voice", "Room voice"),
            ("home/stream", "Live home updates"), ("home/entities", "Lights & switches"), ("home/scenes", "Scenes"),
            ("home/light", "Changing a light"), ("home/fan", "Changing the fan"), ("home/control", "Switching something"),
            ("clients/events", "Alert stream"), ("alerts", "Alerts"), ("glance", "Glance screen (time, weather, next up)"),
            ("now", "What's happening now"), ("theme", "Theme sync"), ("knowledge/graph", "Memory map"),
            ("knowledge/import", "Importing memory"), ("knowledge", "Memory notes"), ("wake", "Wake-up alarm"),
            ("status/media", "Media library status"), ("status/torrents", "Download status"), ("status/storage", "Storage space"),
            ("status/services", "Service health"), ("status", "Status"), ("torrents", "Download control"),
            ("media", "Movies & shows"), ("tv/remote", "TV remote"), ("tv/power", "TV power"), ("room/volume", "Room volume"),
            ("todos", "To-dos"), ("study", "Study brief"), ("portfolio", "Portfolio"), ("maintenance", "Maintenance"),
            ("repairs", "Repairs"), ("push/register", "Registering for notifications"), ("device/battery", "Battery level"),
            ("protocols/barn-door", "Barn Door Protocol"), ("links", "Service links"), ("capabilities", "Checking what Core can do"),
            ("health", "Is Core up?"), ("sessions", "Chat history"), ("debug-reports", "Debug report"),
        ]
        return table.first { path.hasPrefix($0.0) }?.1 ?? path
    }

    /// Core calling a service: "home assistant /api/services/light/turn_on".
    private static func outbound(_ label: String) -> String {
        let parts = label.split(separator: " ", maxSplits: 1).map(String.init)
        let service = parts.first ?? label
        let path = parts.count > 1 ? parts[1] : ""
        if label.hasPrefix("home assistant") {
            if path.contains("/services/") {
                let bits = path.split(separator: "/").suffix(2).joined(separator: ".")
                return "Home Assistant: \(bits.replacingOccurrences(of: "_", with: " "))"
            }
            if path.contains("/states") { return "Home Assistant: reading device states" }
            if path.contains("/camera") { return "Home Assistant: camera" }
            return "Home Assistant"
        }
        switch service {
        case "plex": return "Plex: \(path.contains("sessions") ? "what's playing" : "the library")"
        case "radarr": return "Radarr: movies"
        case "sonarr": return "Sonarr: TV shows"
        case "qbittorrent": return "qBittorrent: downloads"
        case "prowlarr": return "Prowlarr: indexers"
        default: break
        }
        if label.contains("anthropic") { return "Asking Claude (JARVIS thinking)" }
        if label.contains("push.apple.com") { return "Apple push: a notification to a device" }
        if label.contains("open-meteo") { return "Weather forecast" }
        if label.contains("schwab") { return "Schwab: portfolio" }
        if label.contains("elevenlabs") { return "ElevenLabs: JARVIS's voice" }
        if label.contains("spotify") { return "Spotify" }
        if label.contains("google") { return "Google (calendar)" }
        if label.contains("hc-ping") || label.contains("healthchecks") { return "Health check ping" }
        return "Calling \(service)"
    }
}
