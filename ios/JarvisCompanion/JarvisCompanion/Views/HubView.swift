import SwiftUI

/// The status hub: five sections over the dashboard endpoints, each rendering
/// (or failing) independently. Torrents and services auto-refresh while the
/// view is on screen; everything refreshes on pull.
struct HubView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var model: HubViewModel
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            List {
                servicesSection
                torrentsSection
                mediaSection
                quickActionsSection
                linksSection
            }
            .navigationTitle("Hub")
            .navigationBarTitleDisplayMode(.inline)
            .refreshable { await model.refreshAll() }
            .overlay {
                if !settings.isConfigured {
                    ContentUnavailableView(
                        "Not configured",
                        systemImage: "gearshape",
                        description: Text("Add your JARVIS URL and API token in Settings on the Chat tab.")
                    )
                }
            }
            .alert(
                "Action failed",
                isPresented: Binding(
                    get: { model.actionError != nil },
                    set: { if !$0 { model.actionError = nil } }
                )
            ) {
                Button("OK") { model.actionError = nil }
            } message: {
                Text(model.actionError ?? "")
            }
        }
        .task {
            await model.refreshAll()
            model.startPolling()
        }
        .onDisappear { model.stopPolling() }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active: model.startPolling()
            case .inactive, .background: model.stopPolling()
            @unknown default: break
            }
        }
    }

    // MARK: - Services

    private var servicesSection: some View {
        Section("Services") {
            if let error = model.services.failureText {
                errorRow(error)
            }
            if let services = model.services.value {
                ForEach(services) { service in
                    HStack {
                        Circle()
                            .fill(service.up ? .green : .red)
                            .frame(width: 9, height: 9)
                        Text(service.name)
                        Spacer()
                        if let latency = service.latencyMS {
                            Text("\(latency) ms")
                                .font(.footnote.monospacedDigit())
                                .foregroundStyle(.secondary)
                        } else if !service.error.isEmpty {
                            Text(service.error.contains("not configured") ? "Not configured" : "Down")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityLabel("\(service.name), \(service.up ? "up" : "down")")
                }
            } else if model.services.isLoading {
                loadingRow
            }
        }
    }

    // MARK: - Torrents

    private var torrentsSection: some View {
        Section {
            if let error = model.torrents.failureText {
                errorRow(error)
            }
            if let status = model.torrents.value {
                if status.torrents.isEmpty && status.error.isEmpty {
                    Text("Nothing downloading.")
                        .foregroundStyle(.secondary)
                }
                ForEach(status.torrents) { torrent in
                    torrentRow(torrent)
                }
            } else if model.torrents.isLoading {
                loadingRow
            }
        } header: {
            HStack {
                Text("Torrents")
                Spacer()
                if let status = model.torrents.value, status.error.isEmpty {
                    Text("↓ \(Self.rate(status.dlSpeed))  ↑ \(Self.rate(status.upSpeed))")
                        .font(.caption.monospacedDigit())
                        .textCase(nil)
                }
            }
        }
    }

    private func torrentRow(_ torrent: TorrentTransfer) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(torrent.name)
                    .font(.subheadline)
                    .lineLimit(1)
                Spacer()
                if let hash = torrent.hash {
                    let paused = torrent.state.lowercased().hasPrefix("paused")
                    Button {
                        model.torrentAction(torrent, action: paused ? "resume" : "pause")
                    } label: {
                        Image(systemName: paused ? "play.circle" : "pause.circle")
                            .imageScale(.large)
                    }
                    .buttonStyle(.borderless)
                    .disabled(model.busyTorrents.contains(hash))
                    .accessibilityLabel(paused ? "Resume \(torrent.name)" : "Pause \(torrent.name)")
                }
            }
            ProgressView(value: min(max(torrent.progress, 0), 1))
            HStack {
                Text("\(Int(torrent.progress * 100))% · \(Self.rate(torrent.dlSpeed))")
                Spacer()
                Text(Self.eta(torrent.eta))
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
    }

    // MARK: - Media

    private var mediaSection: some View {
        Section("Media") {
            if let error = model.media.failureText {
                errorRow(error)
            }
            if let media = model.media.value {
                mediaService("Radarr", section: media.radarr)
                mediaService("Sonarr", section: media.sonarr)
            } else if model.media.isLoading {
                loadingRow
            }
        }
    }

    @ViewBuilder
    private func mediaService(_ name: String, section: ServarrSection) -> some View {
        if !section.error.isEmpty {
            errorRow("\(name): \(section.error)")
        } else {
            ForEach(section.queue) { item in
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.title)
                        .font(.subheadline)
                        .lineLimit(1)
                    ProgressView(value: min(max(item.progress, 0), 1))
                    HStack {
                        Text("\(name) · \(item.status)")
                        Spacer()
                        if !item.timeLeft.isEmpty { Text(item.timeLeft) }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    if !item.errorMessage.isEmpty {
                        Text(item.errorMessage)
                            .font(.caption)
                            .foregroundStyle(.orange)
                    }
                }
                .padding(.vertical, 2)
            }
            DisclosureGroup("\(name) — recent additions") {
                if section.recent.isEmpty {
                    Text("Nothing recent.")
                        .foregroundStyle(.secondary)
                }
                ForEach(section.recent) { item in
                    HStack {
                        Text(item.title)
                            .lineLimit(1)
                        Spacer()
                        if item.year > 0 {
                            Text(String(item.year))
                                .foregroundStyle(.secondary)
                        }
                    }
                    .font(.subheadline)
                }
            }
            .font(.subheadline)
        }
    }

    // MARK: - Quick actions

    private var quickActionsSection: some View {
        Section("Quick actions") {
            if let error = model.entities.failureText {
                errorRow(error)
            }
            if let entities = model.entities.value {
                if entities.isEmpty && model.entities.serverError.isEmpty {
                    Text("No entities allowlisted. Set HOME_DASHBOARD_ENTITIES on the server.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                ForEach(entities) { entity in
                    Toggle(isOn: Binding(
                        get: { model.pendingEntityStates[entity.entityID] ?? entity.isOn },
                        set: { model.toggle(entity, to: $0) }
                    )) {
                        VStack(alignment: .leading) {
                            Text(entity.friendlyName)
                            Text(entity.domain)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .disabled(
                        !entity.isToggleable
                            || model.pendingEntityStates[entity.entityID] != nil
                    )
                }
            } else if model.entities.isLoading {
                loadingRow
            }
        }
    }

    // MARK: - Links

    private var linksSection: some View {
        Section("Open in browser") {
            if let error = model.links.failureText {
                errorRow(error)
            }
            if let links = model.links.value {
                ForEach(links) { link in
                    if let url = URL(string: link.url) {
                        Link(destination: url) {
                            HStack {
                                Image(systemName: Self.icon(for: link.iconHint))
                                    .frame(width: 24)
                                Text(link.name)
                                Spacer()
                                Image(systemName: "arrow.up.right.square")
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            } else if model.links.isLoading {
                loadingRow
            }
        }
    }

    // MARK: - Shared bits

    private var loadingRow: some View {
        HStack {
            Spacer()
            ProgressView()
            Spacer()
        }
    }

    private func errorRow(_ text: String) -> some View {
        Label(text, systemImage: "exclamationmark.triangle")
            .font(.footnote)
            .foregroundStyle(.orange)
    }

    private static func icon(for hint: String) -> String {
        switch hint {
        case "home": return "house"
        case "play": return "play.rectangle"
        case "tv": return "tv"
        case "movie": return "film"
        case "download": return "arrow.down.circle"
        default: return "link"
        }
    }

    private static func rate(_ bytesPerSecond: Int) -> String {
        guard bytesPerSecond > 0 else { return "0 KB/s" }
        let formatted = ByteCountFormatter.string(
            fromByteCount: Int64(bytesPerSecond), countStyle: .binary
        )
        return "\(formatted)/s"
    }

    private static func eta(_ seconds: Int) -> String {
        // qBittorrent reports 8640000 (100 days) for "unknown".
        guard seconds > 0, seconds < 8_640_000 else { return "" }
        if seconds < 60 { return "under a minute" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(minutes)m left" }
        let hours = minutes / 60
        if hours < 48 { return "\(hours)h \(minutes % 60)m left" }
        return "\(hours / 24)d left"
    }
}
