import Foundation
import SwiftUI

/// State for the Hub screen: five independent sections over the dashboard
/// endpoints. Each section loads, fails, and renders on its own — one dead
/// service must never blank the rest of the screen.
@MainActor
final class HubViewModel: ObservableObject {

    /// One section's lifecycle. `serverError` is the endpoint's own `error`
    /// field (service down, request fine); `loadError` means the request to
    /// JARVIS Core itself failed.
    struct Section<Value> {
        var value: Value?
        var serverError: String = ""
        var loadError: String?
        var isLoading = false

        var failureText: String? {
            if let loadError { return loadError }
            return serverError.isEmpty ? nil : serverError
        }
    }

    @Published var services = Section<[ServiceStatus]>()
    @Published var torrents = Section<TorrentsStatus>()
    @Published var media = Section<MediaStatus>()
    @Published var entities = Section<[HomeEntity]>()
    @Published var links = Section<[ServiceLink]>()

    /// entity_id -> in-flight optimistic state, so a toggle feels instant and
    /// reverts visibly if the call fails.
    @Published var pendingEntityStates: [String: Bool] = [:]
    /// Torrent hashes with a control call in flight.
    @Published var busyTorrents: Set<String> = []
    /// Transient, user-visible outcome of the last quick action that failed.
    @Published var actionError: String?

    private let settings: AppSettings
    private let makeClient: () throws -> JarvisAPIClient
    private var pollTask: Task<Void, Never>?

    /// Matches the server's TTL cache — polling faster just returns the same
    /// cached payload.
    private let pollInterval: Duration = .seconds(5)

    init(settings: AppSettings, clientFactory: (() throws -> JarvisAPIClient)? = nil) {
        self.settings = settings
        self.makeClient = clientFactory ?? { try JarvisAPIClient.make(from: settings) }
    }

    private func client() throws -> JarvisAPIClient {
        try makeClient()
    }

    // MARK: - Loading

    /// Full refresh: every section, in parallel. Used on appear and
    /// pull-to-refresh.
    func refreshAll() async {
        async let a: Void = refreshServices()
        async let b: Void = refreshTorrents()
        async let c: Void = refreshMedia()
        async let d: Void = refreshEntities()
        async let e: Void = refreshLinks()
        _ = await (a, b, c, d, e)
    }

    func refreshServices() async {
        await load(\.services) { try await $0.servicesStatus().services }
    }

    func refreshTorrents() async {
        await load(\.torrents, serverError: { $0.error }) { try await $0.torrentsStatus() }
    }

    func refreshMedia() async {
        await load(\.media, serverError: { status in
            // Per-service errors render inside the section; only surface a
            // whole-section error when both halves failed.
            status.radarr.error.isEmpty || status.sonarr.error.isEmpty
                ? "" : status.radarr.error
        }) { try await $0.mediaStatus() }
    }

    func refreshEntities() async {
        await load(\.entities, serverError: { $0.error }, transform: { $0.entities }) {
            try await $0.homeEntities()
        }
    }

    func refreshLinks() async {
        await load(\.links) { try await $0.serviceLinks().links }
    }

    /// Fetch one section, keeping stale data on screen when a refresh fails.
    private func load<Value>(
        _ keyPath: ReferenceWritableKeyPath<HubViewModel, Section<Value>>,
        serverError: (Value) -> String = { _ in "" },
        fetch: (JarvisAPIClient) async throws -> Value
    ) async {
        self[keyPath: keyPath].isLoading = true
        defer { self[keyPath: keyPath].isLoading = false }
        do {
            let value = try await fetch(client())
            guard !Task.isCancelled else { return }
            self[keyPath: keyPath].value = value
            self[keyPath: keyPath].serverError = serverError(value)
            self[keyPath: keyPath].loadError = nil
        } catch {
            guard !Task.isCancelled else { return }
            self[keyPath: keyPath].loadError = error.localizedDescription
        }
    }

    /// Variant for endpoints whose envelope and rendered value differ.
    private func load<Raw, Value>(
        _ keyPath: ReferenceWritableKeyPath<HubViewModel, Section<Value>>,
        serverError: (Raw) -> String,
        transform: (Raw) -> Value,
        fetch: (JarvisAPIClient) async throws -> Raw
    ) async {
        self[keyPath: keyPath].isLoading = true
        defer { self[keyPath: keyPath].isLoading = false }
        do {
            let raw = try await fetch(client())
            guard !Task.isCancelled else { return }
            self[keyPath: keyPath].value = transform(raw)
            self[keyPath: keyPath].serverError = serverError(raw)
            self[keyPath: keyPath].loadError = nil
        } catch {
            guard !Task.isCancelled else { return }
            self[keyPath: keyPath].loadError = error.localizedDescription
        }
    }

    // MARK: - Foreground polling

    /// Auto-refresh the fast-moving sections (torrents, services) while the
    /// Hub is visible and the app is foregrounded. Never faster than the
    /// server's 5s TTL; stops entirely when the view goes away.
    func startPolling() {
        guard pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: self?.pollInterval ?? .seconds(5))
                guard !Task.isCancelled, let self else { return }
                async let torrents: Void = self.refreshTorrents()
                async let services: Void = self.refreshServices()
                async let entities: Void = self.refreshEntities()
                async let media: Void = self.refreshMedia()
                _ = await (torrents, services, entities, media)
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    // MARK: - Quick actions

    /// Optimistically flip an entity, then reconcile with the state the
    /// server reports back. Any failure reverts the toggle and surfaces why.
    func toggle(_ entity: HomeEntity, to turnOn: Bool) {
        guard pendingEntityStates[entity.entityID] == nil else { return }
        pendingEntityStates[entity.entityID] = turnOn
        Task {
            defer { pendingEntityStates[entity.entityID] = nil }
            do {
                let result = try await client().homeControl(
                    entityID: entity.entityID, turnOn: turnOn
                )
                if result.error.isEmpty {
                    applyEntityState(entity.entityID, state: result.state ?? (turnOn ? "on" : "off"))
                } else {
                    actionError = result.error
                }
            } catch {
                actionError = error.localizedDescription
            }
        }
    }

    private func applyEntityState(_ entityID: String, state: String) {
        guard var current = entities.value,
              let index = current.firstIndex(where: { $0.entityID == entityID })
        else { return }
        current[index] = HomeEntity(
            entityID: entityID,
            friendlyName: current[index].friendlyName,
            domain: current[index].domain,
            state: state
        )
        entities.value = current
    }

    func torrentAction(_ torrent: TorrentTransfer, action: String) {
        guard let hash = torrent.hash, !busyTorrents.contains(hash) else { return }
        busyTorrents.insert(hash)
        Task {
            defer { busyTorrents.remove(hash) }
            do {
                let result = try await client().torrentControl(hash: hash, action: action)
                if !result.error.isEmpty {
                    actionError = result.error
                }
            } catch {
                actionError = error.localizedDescription
            }
            await refreshTorrents()
        }
    }
}
