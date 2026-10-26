import SwiftUI

/// A glanceable mobile counterpart to the desktop cockpit. Each card still
/// loads independently; the presentation no longer inherits Form/List chrome.
struct HubView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var model: HubViewModel
    var onAsk: ((String) -> Void)? = nil
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var isVisible = false
    @State private var showingSettings = false
    @State private var showingMediaRequest = false
    @State private var focus = HubFocus.today

    enum HubFocus: String, CaseIterable { case today = "Today", media = "Media", home = "Home" }

    init(model: HubViewModel, onAsk: ((String) -> Void)? = nil, initialFocus: HubFocus = .today) {
        self.model = model
        self.onAsk = onAsk
        _focus = State(initialValue: initialFocus)
    }

    private var columns: [GridItem] {
        Array(repeating: GridItem(.flexible(), spacing: 10), count: typeSize.isAccessibilitySize ? 1 : 2)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 14) {
                    header
                    overview
                    Picker("Hub focus", selection: $focus) {
                        ForEach(HubFocus.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                    }.pickerStyle(.segmented).padding(.vertical, 3)
                    switch focus {
                    case .today:
                        if !attentionAlerts.isEmpty { needsAttentionCard }
                        nowCard
                        storageCard
                        if onAsk != nil { briefingShortcut }
                        servicesCard
                    case .media:
                        requestMediaButton
                        storageCard
                        torrentsCard
                        mediaCard
                        linksCard
                    case .home:
                        quickActionsCard
                        servicesCard
                        linksCard
                    }
                }
                .frame(maxWidth: 760)
                .padding(.horizontal, 16)
                .padding(.bottom, 28)
                .frame(maxWidth: .infinity)
            }
            .refreshable { await model.refreshAll() }
            .background { JarvisBackdrop() }
            .navigationTitle("Control Room")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(JarvisTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Settings", systemImage: "slider.horizontal.3") { showingSettings = true }
                }
            }
            .sheet(isPresented: $showingSettings) { SettingsView() }
            .sheet(isPresented: $showingMediaRequest) {
                MediaRequestView(makeClient: { try JarvisAPIClient.make(from: settings) })
            }
            .overlay {
                if !settings.isConfigured {
                    ContentUnavailableView {
                        Label("Your command center awaits", systemImage: "network")
                    } description: {
                        Text("Connect to JARVIS to see your day, home, and media in one place.")
                    } actions: {
                        Button("Connect JARVIS") { showingSettings = true }
                            .buttonStyle(.borderedProminent)
                    }
                    .padding(24)
                    .background(.ultraThinMaterial, in: .rect(cornerRadius: 24))
                    .padding(24)
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
            isVisible = true
            guard !Task.isCancelled, isVisible, scenePhase == .active else { return }
            model.startPolling()
        }
        .onChange(of: settings.isConfigured) { _, configured in
            if configured, isVisible { model.startPolling() }
            else { model.stopPolling() }
        }
        .onDisappear { isVisible = false; model.stopPolling() }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                if isVisible { model.startPolling() }
            case .inactive, .background: model.stopPolling()
            @unknown default: break
            }
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Text(Date.now, format: .dateTime.weekday(.wide).month(.abbreviated).day())
                    .font(.caption2.monospaced().weight(.semibold))
                    .tracking(1.8)
                    .foregroundStyle(JarvisTheme.cyan)
                Text("A little ahead of everything.")
                    .font(.title.weight(.semibold)).tracking(-0.6)
                Text("Your day. Your home. Your JARVIS.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            if !typeSize.isAccessibilitySize { JarvisCoreMark(size: 66) }
        }
        .padding(.top, 18)
        .padding(.bottom, 2)
    }

    private var overview: some View {
        LazyVGrid(columns: columns, spacing: 10) {
            metric("Systems", value: serviceSummary, symbol: "server.rack",
                   tint: offlineServiceCount == 0 ? JarvisTheme.success : JarvisTheme.amber)
            metric("Media free", value: storageFreeSummary,
                   symbol: "externaldrive", tint: JarvisTheme.cyan)
        }
    }

    private func metric(_ title: String, value: String, symbol: String, tint: Color) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(tint)
                .frame(width: 36, height: 36)
                .background(tint.opacity(0.11), in: .circle)
            VStack(alignment: .leading, spacing: 2) {
                Text(value).font(.title3.monospacedDigit().weight(.semibold)).lineLimit(1).minimumScaleFactor(0.7)
                Text(title.uppercased())
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(typeSize.isAccessibilitySize ? nil : 1)
            }
            Spacer(minLength: 0)
        }
        .padding(13)
        .frame(maxWidth: .infinity, minHeight: 72)
        .background(JarvisTheme.panelGradient, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(JarvisTheme.border))
    }

    private var serviceSummary: String {
        guard let services = model.services.value, !services.isEmpty else { return "—" }
        return "\(services.filter(\.up).count)/\(services.count)"
    }

    private var offlineServiceCount: Int {
        model.services.value?.filter { !$0.up }.count ?? 0
    }

    private var storageFreeSummary: String {
        guard let storage = model.storage.value, storage.error.isEmpty else { return "—" }
        return Self.bytes(storage.freeBytes)
    }

    private var briefingShortcut: some View {
        Button { onAsk?("Give me a briefing on my day and anything that needs my attention.") } label: {
            HStack(spacing: 14) {
                Image(systemName: "waveform").font(.title2).foregroundStyle(JarvisTheme.cyan)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Bring me up to speed").font(.subheadline.weight(.semibold))
                    Text("Start a briefing with JARVIS").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "arrow.up.right").foregroundStyle(JarvisTheme.cyan)
            }.padding(18).background(JarvisTheme.panelGradient, in: .rect(cornerRadius: 20))
                .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(JarvisTheme.border))
        }.buttonStyle(CockpitButtonStyle())
    }

    private var nowCard: some View {
        CockpitPanel("Now", symbol: "clock.badge", trailing: {
            if let snapshot = model.now.value {
                StatusPill(
                    text: model.nowSnapshotIsStale ? "Stale" : "Live",
                    color: model.nowSnapshotIsStale ? JarvisTheme.amber : JarvisTheme.success
                )
                .accessibilityLabel(
                    model.nowSnapshotIsStale
                        ? "Last updated \(snapshot.generatedDate.formatted())"
                        : "Live snapshot"
                )
            }
        }) {
            if let error = model.now.failureText {
                if model.now.value != nil {
                    Label("Showing the last snapshot · \(error)", systemImage: "clock.arrow.circlepath")
                        .font(.caption)
                        .foregroundStyle(JarvisTheme.amber)
                } else {
                    errorRow(error)
                }
            }
            if let snapshot = model.now.value {
                VStack(spacing: 0) {
                    if let event = snapshot.nextEvent {
                        nowRow(symbol: "calendar", tint: JarvisTheme.blue) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(event.title).font(.subheadline.weight(.medium)).lineLimit(1)
                                HStack(spacing: 5) {
                                    if event.allDay {
                                        Text("All day")
                                    } else if let startAt = event.startAt {
                                        Text(Date(timeIntervalSince1970: startAt), format: .dateTime.weekday(.abbreviated).hour().minute())
                                    }
                                    if !event.location.isEmpty { Text("· \(event.location)").lineLimit(1) }
                                }
                                .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        divider
                    } else if !snapshot.calendarError.isEmpty {
                        Label(snapshot.calendarError, systemImage: "calendar.badge.exclamationmark")
                            .font(.caption).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.vertical, 8)
                        if !snapshot.academicEvents.isEmpty || !snapshot.timers.isEmpty || !snapshot.reminders.isEmpty { divider }
                    }

                    if !snapshot.academicEvents.isEmpty {
                        HStack {
                            Text("UPCOMING ASSESSMENTS")
                                .font(.caption2.monospaced().weight(.semibold))
                                .foregroundStyle(.secondary)
                            Spacer()
                            if !typeSize.isAccessibilitySize {
                                Text(settings.studyAlertsEnabled ? "COUNTDOWN" : "ALERTS OFF")
                                    .font(.caption2.monospaced())
                                    .foregroundStyle(JarvisTheme.cyan)
                            }
                        }
                        .padding(.top, 9)
                        ForEach(Array(snapshot.academicEvents.enumerated()), id: \.element.id) { index, event in
                            nowRow(symbol: "graduationcap.fill", tint: JarvisTheme.blue) {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(event.title).font(.subheadline.weight(.medium)).lineLimit(2)
                                    Text(Self.academicCountdown(event.daysLeft))
                                        .font(.caption.monospacedDigit().weight(.semibold))
                                        .foregroundStyle(event.daysLeft <= 1 ? JarvisTheme.amber : .secondary)
                                }
                            }
                            if index < snapshot.academicEvents.count - 1 { divider }
                        }
                        if !snapshot.timers.isEmpty || !snapshot.reminders.isEmpty { divider }
                    }

                    ForEach(Array(snapshot.timers.enumerated()), id: \.element.id) { index, timer in
                        nowRow(
                            symbol: timer.state == "alarming" ? "alarm.waves.left.and.right.fill" : "timer",
                            tint: timer.state == "alarming" ? .red : JarvisTheme.cyan
                        ) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(timer.label).font(.subheadline.weight(.medium)).lineLimit(1)
                                TimelineView(.periodic(from: .now, by: 1)) { context in
                                    Text(timer.state == "alarming" ? "TIME’S UP" : Self.duration(
                                        timer.state == "running" && !model.nowSnapshotIsStale
                                            ? max(0, Int(timer.expiresAt - context.date.timeIntervalSince1970))
                                            : timer.remainingSeconds, state: timer.state))
                                    .font(.caption.monospacedDigit().weight(.medium))
                                    .foregroundStyle(timer.state == "alarming" ? .red : .secondary)
                                }
                            }
                        }
                        if index < snapshot.timers.count - 1 || !snapshot.reminders.isEmpty { divider }
                    }

                    ForEach(Array(snapshot.reminders.enumerated()), id: \.element.id) { index, reminder in
                        nowRow(symbol: reminder.isDue ? "exclamationmark.circle.fill" : "checklist",
                               tint: reminder.isDue ? JarvisTheme.amber : JarvisTheme.success) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(reminder.text).font(.subheadline.weight(.medium)).lineLimit(2)
                                if let dueAt = reminder.dueAt {
                                    Text(Date(timeIntervalSince1970: dueAt), format: .dateTime.weekday(.abbreviated).hour().minute())
                                        .font(.caption).foregroundStyle(reminder.isDue ? JarvisTheme.amber : .secondary)
                                } else {
                                    Text("Open reminder").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        if index < snapshot.reminders.count - 1 { divider }
                    }

                    if snapshot.nextEvent == nil && snapshot.academicEvents.isEmpty && snapshot.timers.isEmpty && snapshot.reminders.isEmpty
                        && snapshot.calendarError.isEmpty {
                        quietState("Nothing needs your attention.", symbol: "checkmark.circle")
                    }
                }
                Text("Updated \(snapshot.generatedDate, style: .relative)")
                    .font(.caption2.monospaced())
                    .foregroundStyle(.tertiary)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            } else if model.now.isLoading {
                loadingRow
            }
        }
    }

    private func nowRow<Content: View>(
        symbol: String,
        tint: Color,
        @ViewBuilder content: () -> Content
    ) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(tint)
                .frame(width: 34, height: 34)
                .background(tint.opacity(0.11), in: .circle)
            content()
            Spacer(minLength: 0)
        }
        .padding(.vertical, 9)
    }

    private var storageCard: some View {
        CockpitPanel("Media drive", symbol: "externaldrive.fill", trailing: {
            if let storage = model.storage.value, storage.error.isEmpty {
                StatusPill(text: "\(Int((1 - storage.usedFraction) * 100))% free", color: JarvisTheme.cyan)
            }
        }) {
            if let error = model.storage.failureText { errorRow(error) }
            if let storage = model.storage.value, storage.error.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(Self.bytes(storage.freeBytes))
                            .font(.title2.monospacedDigit().weight(.semibold))
                        Text("available").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Text(storage.label.uppercased())
                            .font(.caption2.monospaced())
                            .foregroundStyle(.secondary)
                    }
                    ProgressView(value: min(max(storage.usedFraction, 0), 1))
                        .tint(storage.usedFraction > 0.9 ? JarvisTheme.amber : JarvisTheme.cyan)
                    HStack {
                        Text("\(Self.bytes(storage.usedBytes)) used")
                        Spacer()
                        Text("\(Self.bytes(storage.totalBytes)) total")
                    }
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            } else if model.storage.isLoading {
                loadingRow
            }
        }
    }

    /// Warnings and criticals from the last day. An older server without
    /// /api/alerts/recent simply never shows this card.
    private var attentionAlerts: [JarvisAlert] {
        Array((model.alerts.value ?? []).filter { $0.severity != "info" }.prefix(5))
    }

    private var needsAttentionCard: some View {
        CockpitPanel("Needs attention", symbol: "exclamationmark.triangle", trailing: {
            StatusPill(text: "\(attentionAlerts.count)", color: JarvisTheme.amber)
        }) {
            VStack(spacing: 0) {
                ForEach(Array(attentionAlerts.enumerated()), id: \.element.id) { index, alert in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Circle()
                                .fill(alert.severity == "critical" ? Color.red : JarvisTheme.amber)
                                .frame(width: 8, height: 8)
                            Text(alert.title).font(.subheadline.weight(.medium))
                            Spacer()
                            Text(alert.date, style: .relative)
                                .font(.caption2.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                        if !alert.message.isEmpty {
                            Text(alert.message).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                        }
                    }
                    .padding(.vertical, 8)
                    if index < attentionAlerts.count - 1 { divider }
                }
            }
        }
    }

    private var servicesCard: some View {
        CockpitPanel("Services", symbol: "dot.radiowaves.left.and.right", trailing: {
            if let services = model.services.value, !services.isEmpty {
                StatusPill(text: offlineServiceCount == 0 ? "Nominal" : "\(offlineServiceCount) down",
                           color: offlineServiceCount == 0 ? JarvisTheme.success : JarvisTheme.amber)
            }
        }) {
            if let error = model.services.failureText { errorRow(error) }
            if let services = model.services.value {
                VStack(spacing: 0) {
                    ForEach(Array(services.enumerated()), id: \.element.id) { index, service in
                        HStack(spacing: 11) {
                            Circle()
                                .fill(service.up ? JarvisTheme.success : .red)
                                .frame(width: 8, height: 8)
                                .shadow(color: (service.up ? JarvisTheme.success : .red).opacity(0.55), radius: 4)
                            Text(service.name).font(.subheadline.weight(.medium))
                            Spacer()
                            Text(serviceDetail(service))
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 10)
                        .accessibilityLabel("\(service.name), \(service.up ? "up" : "down")")
                        if index < services.count - 1 { divider }
                    }
                }
            } else if model.services.isLoading { loadingRow }
        }
    }

    private func serviceDetail(_ service: ServiceStatus) -> String {
        if let latency = service.latencyMS { return "\(latency) ms" }
        return service.error.contains("not configured") ? "Not set" : "Down"
    }

    private var torrentsCard: some View {
        CockpitPanel("Transfers", symbol: "arrow.down.circle", trailing: {
            if let status = model.torrents.value, status.error.isEmpty {
                Text("↓ \(Self.rate(status.dlSpeed))")
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(JarvisTheme.cyan)
            }
        }) {
            if let error = model.torrents.failureText { errorRow(error) }
            if let status = model.torrents.value {
                if status.torrents.isEmpty && status.error.isEmpty {
                    quietState("No active or paused downloads.", symbol: "checkmark.circle")
                }
                VStack(spacing: 0) {
                    ForEach(Array(status.torrents.enumerated()), id: \.element.id) { index, torrent in
                        torrentRow(torrent)
                        if index < status.torrents.count - 1 { divider }
                    }
                }
            } else if model.torrents.isLoading { loadingRow }
        }
    }

    private func torrentRow(_ torrent: TorrentTransfer) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(torrent.name).font(.subheadline.weight(.medium)).lineLimit(1)
                    Text(torrent.isPaused ? "PAUSED" : torrent.state.uppercased())
                        .font(.caption2.monospaced().weight(.semibold))
                        .foregroundStyle(torrent.isPaused ? JarvisTheme.amber : JarvisTheme.cyan)
                }
                Spacer()
                if let hash = torrent.hash {
                    Button {
                        model.torrentAction(torrent, action: torrent.isPaused ? "resume" : "pause")
                    } label: {
                        Image(systemName: torrent.isPaused ? "play.fill" : "pause.fill")
                            .font(.caption.weight(.bold))
                            .frame(width: 44, height: 44)
                            .background(JarvisTheme.cyan.opacity(0.11), in: .circle)
                    }
                    .disabled(model.busyTorrents.contains(hash) || model.torrents.failureText != nil)
                    .accessibilityLabel(torrent.isPaused ? "Resume \(torrent.name)" : "Pause \(torrent.name)")
                }
            }
            ProgressView(value: min(max(torrent.progress, 0), 1))
                .tint(torrent.isPaused ? JarvisTheme.amber : JarvisTheme.cyan)
            HStack {
                Text("\(Int(min(max(torrent.progress, 0), 1) * 100))%")
                // qBittorrent reports 0 until the torrent's metadata arrives.
                if torrent.size > 0 { Text("of \(Self.bytes(Int64(torrent.size)))") }
                Text("· \(torrent.isPaused ? "Waiting" : Self.rate(torrent.dlSpeed))")
                Spacer()
                Text(Self.eta(torrent.eta))
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 11)
    }

    /// Opens the chat-free search -> pick -> confirm request flow.
    private var requestMediaButton: some View {
        Button { showingMediaRequest = true } label: {
            Label("Request a movie or show", systemImage: "plus.magnifyingglass")
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
        }
        .buttonStyle(.borderedProminent)
        .tint(JarvisTheme.blue)
        .disabled(!settings.isConfigured)
    }

    private var mediaCard: some View {
        CockpitPanel("Media", symbol: "play.rectangle.on.rectangle") {
            if let error = model.media.failureText { errorRow(error) }
            if let media = model.media.value {
                mediaService("Radarr", symbol: "film", section: media.radarr)
                divider
                mediaService("Sonarr", symbol: "tv", section: media.sonarr)
            } else if model.media.isLoading { loadingRow }
        }
    }

    @ViewBuilder
    private func mediaService(_ name: String, symbol: String, section: ServarrSection) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(name, systemImage: symbol).font(.subheadline.weight(.semibold))
                Spacer()
                Text("\(section.queue.count) QUEUED")
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
            }
            if !section.error.isEmpty {
                errorRow(section.error)
            } else {
                ForEach(section.queue) { item in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(item.title).font(.subheadline).lineLimit(1)
                        ProgressView(value: min(max(item.progress, 0), 1)).tint(JarvisTheme.blue)
                        HStack {
                            Text(item.status)
                            if item.size > 0 { Text("· \(Self.bytes(Int64(item.size)))") }
                            Spacer()
                            Text(item.timeLeft)
                        }
                        .font(.caption).foregroundStyle(.secondary)
                    }
                }
                DisclosureGroup("Recent additions (\(section.recent.count))") {
                    if section.recent.isEmpty { Text("Nothing recent.").foregroundStyle(.secondary) }
                    ForEach(section.recent) { item in
                        HStack {
                            Text(item.title).lineLimit(1)
                            Spacer()
                            if item.year > 0 { Text(String(item.year)).foregroundStyle(.secondary) }
                        }
                        .padding(.vertical, 3)
                    }
                }
                .font(.footnote)
                .tint(JarvisTheme.cyan)
            }
        }
        .padding(.vertical, 4)
    }

    private var quickActionsCard: some View {
        CockpitPanel("Home", symbol: "house.lodge") {
            if let error = model.entities.failureText { errorRow(error) }
            if let entities = model.entities.value {
                if entities.isEmpty && model.entities.serverError.isEmpty {
                    quietState("No quick actions configured.", symbol: "slider.horizontal.3")
                }
                VStack(spacing: 0) {
                    ForEach(Array(entities.enumerated()), id: \.element.id) { index, entity in
                        Toggle(isOn: Binding(
                            get: { model.pendingEntityStates[entity.entityID] ?? entity.isOn },
                            set: { model.toggle(entity, to: $0) }
                        )) {
                            HStack(spacing: 11) {
                                Image(systemName: Self.homeIcon(for: entity.domain))
                                    .foregroundStyle(entity.isOn ? JarvisTheme.amber : .secondary)
                                    .frame(width: 24)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(entity.friendlyName).font(.subheadline.weight(.medium))
                                    Text(entity.state.uppercased())
                                        .font(.caption2.monospaced()).foregroundStyle(.secondary)
                                }
                            }
                        }
                        .tint(JarvisTheme.cyan)
                        .padding(.vertical, 9)
                        .disabled(!entity.isToggleable || model.pendingEntityStates[entity.entityID] != nil || model.entities.failureText != nil)
                        if index < entities.count - 1 { divider }
                    }
                }
            } else if model.entities.isLoading { loadingRow }
        }
    }

    private var linksCard: some View {
        CockpitPanel("Launch", symbol: "arrow.up.right.square") {
            if let error = model.links.failureText { errorRow(error) }
            if let links = model.links.value {
                if links.isEmpty { quietState("No service links configured.", symbol: "link") }
                LazyVGrid(columns: columns, spacing: 10) {
                    ForEach(links) { link in
                        if let url = URL(string: link.url) {
                            Link(destination: url) {
                                HStack(spacing: 9) {
                                    Image(systemName: Self.icon(for: link.iconHint))
                                        .foregroundStyle(JarvisTheme.cyan)
                                    Text(link.name).font(.footnote.weight(.medium)).lineLimit(1)
                                    Spacer(minLength: 0)
                                }
                                .padding(11)
                                .background(JarvisTheme.cyan.opacity(0.07), in: .rect(cornerRadius: 12))
                                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(JarvisTheme.border))
                            }
                        }
                    }
                }
            } else if model.links.isLoading { loadingRow }
        }
    }

    private var divider: some View {
        Divider().overlay(JarvisTheme.border.opacity(0.65))
    }

    private var loadingRow: some View {
        HStack(spacing: 10) {
            ProgressView().tint(JarvisTheme.cyan)
            Text("UPDATING TELEMETRY").font(.caption2.monospaced()).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 16)
    }

    private func errorRow(_ text: String) -> some View {
        Label(text, systemImage: "exclamationmark.triangle.fill")
            .font(.footnote)
            .foregroundStyle(JarvisTheme.amber)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func quietState(_ text: String, symbol: String) -> some View {
        Label(text, systemImage: symbol)
            .font(.footnote)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 8)
    }

    private static func homeIcon(for domain: String) -> String {
        switch domain {
        case "light": return "lightbulb.fill"
        case "switch": return "switch.2"
        case "fan": return "fan.fill"
        default: return "power"
        }
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
        return "\(ByteCountFormatter.string(fromByteCount: Int64(bytesPerSecond), countStyle: .binary))/s"
    }

    private static func bytes(_ value: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: value, countStyle: .binary)
    }

    private static func academicCountdown(_ days: Int) -> String {
        switch days {
        case ...0: return "TODAY"
        case 1: return "TOMORROW"
        default: return "IN \(days) DAYS"
        }
    }

    private static func eta(_ seconds: Int) -> String {
        guard seconds > 0, seconds < 8_640_000 else { return "" }
        if seconds < 60 { return "<1m left" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(minutes)m left" }
        let hours = minutes / 60
        if hours < 48 { return "\(hours)h \(minutes % 60)m left" }
        return "\(hours / 24)d left"
    }

    private static func duration(_ seconds: Int, state: String) -> String {
        let prefix = state == "paused" ? "Paused · " : ""
        if seconds < 60 { return "\(prefix)\(seconds)s remaining" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(prefix)\(minutes)m remaining" }
        let hours = minutes / 60
        return "\(prefix)\(hours)h \(minutes % 60)m remaining"
    }
}
