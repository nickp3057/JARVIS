import Foundation
import Network

/// Which address reaches Core right now: home (the Surface on the LAN) or
/// away (`jarvis.nickolaspearson.com` through Lightsail, device certificate
/// required), chosen on its own (REMOTE_AND_MONEY_PLAN.md, Part 1, item 5).
///
/// - On Wi-Fi or Ethernet it asks the home address first (1.5 s); any HTTP
///   answer, even a 401, means home. Otherwise away.
/// - Cellular only: away straight away.
/// - A request that can't connect retries once on the other address and
///   flips the choice (`JarvisAPIClient`), and streams on the old address are
///   cancelled so they reconnect on the new one.
/// - The optional Tailscale address is a last resort when away access isn't
///   set up yet (no certificate) or doesn't answer.
@MainActor
final class ServerRoute: ObservableObject {
    static let shared = ServerRoute()

    enum Kind: String, CaseIterable {
        case home, away, fallback

        var label: String {
            switch self {
            case .home: return "at home"
            case .away: return "away (nickolaspearson.com)"
            case .fallback: return "over Tailscale"
            }
        }
    }

    enum Mode: String, CaseIterable, Identifiable {
        case auto, home, away
        var id: String { rawValue }
        var title: String {
            switch self {
            case .auto: return "Automatic"
            case .home: return "Home"
            case .away: return "Away"
            }
        }
    }

    private static let lastRouteKey = "jarvis.route.last"

    @Published private(set) var current: Kind
    @Published private(set) var checking = false

    private weak var settings: AppSettings?
    private let monitor = NWPathMonitor()
    private var started = false
    private var path: NWPath?
    private var evaluation: Task<Void, Never>?

    private init() {
        current = Kind(rawValue: UserDefaults.standard.string(forKey: Self.lastRouteKey) ?? "") ?? .home
    }

    /// Begin following the network. Safe to call more than once.
    func start(_ settings: AppSettings) {
        self.settings = settings
        guard !started else { reevaluate(); return }
        started = true
        monitor.pathUpdateHandler = { [weak self] path in
            Task { @MainActor in
                self?.path = path
                self?.reevaluate()
            }
        }
        monitor.start(queue: DispatchQueue(label: "jarvis.route.path"))
        reevaluate()
    }


    /// Pick again: on a network change, app foregrounding, or an address edit.
    func reevaluate() {
        evaluation?.cancel()
        evaluation = Task { [weak self] in await self?.evaluate() }
    }

    private func evaluate() async {
        guard let settings else { return }
        switch settings.routeMode {
        case .home: set(settings.url(for: .home) != nil ? .home : bestAway(settings))
        case .away: set(bestAway(settings))
        case .auto:
            let cellularOnly = path.map { $0.usesInterfaceType(.cellular)
                && !$0.usesInterfaceType(.wifi) && !$0.usesInterfaceType(.wiredEthernet) } ?? false
            guard !cellularOnly, let home = settings.url(for: .home) else {
                set(bestAway(settings))
                return
            }
            checking = true
            let atHome = await Self.answers(home)
            checking = false
            guard !Task.isCancelled else { return }
            set(atHome ? .home : bestAway(settings))
        }
    }

    /// Away with the device certificate; Tailscale when away isn't set up.
    private func bestAway(_ settings: AppSettings) -> Kind {
        if settings.url(for: .away) != nil, DeviceCertificate.shared.isInstalled { return .away }
        if settings.url(for: .fallback) != nil { return .fallback }
        return settings.url(for: .away) != nil ? .away : .home
    }

    private func set(_ kind: Kind) {
        guard kind != current else { return }
        let old = settings?.url(for: current)
        current = kind
        UserDefaults.standard.set(kind.rawValue, forKey: Self.lastRouteKey)
        if let old { JarvisAPIClient.cancelRequests(to: old) }  // streams reconnect on the new route
    }

    /// The other address to try once when `failed` couldn't be reached
    /// (Automatic only). Switches to it, so later requests go there first.
    func failover(from failed: URL) -> URL? {
        guard let settings, settings.routeMode == .auto else { return nil }
        let failedKind = Kind.allCases.first { settings.url(for: $0).map { Self.sameServer($0, failed) } ?? false }
        let order: [Kind]
        switch failedKind {
        case .home: order = [bestAway(settings)]
        case .away: order = [.home, .fallback]
        case .fallback: order = [.home, .away]
        case nil: return nil
        }
        for kind in order where kind != failedKind {
            if kind == .away, !DeviceCertificate.shared.isInstalled { continue }
            if let url = settings.url(for: kind) {
                set(kind)
                reevaluate()  // and confirm with a proper check shortly
                return url
            }
        }
        return nil
    }

    nonisolated static func sameServer(_ a: URL, _ b: URL) -> Bool {
        a.scheme == b.scheme && a.host == b.host && a.port == b.port
    }

    /// Any HTTP answer from Core's /health within 1.5 s.
    nonisolated static func answers(_ base: URL) async -> Bool {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 1.5
        config.waitsForConnectivity = false
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        guard let (_, response) = try? await session.data(from: base.appending(path: "health")) else { return false }
        return response is HTTPURLResponse
    }
}
