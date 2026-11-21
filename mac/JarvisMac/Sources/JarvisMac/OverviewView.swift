import SwiftUI

/// The front page: the reactor and a greeting, what needs you, what's next,
/// the room, downloads and the stack's health, all at a glance.
struct OverviewView: View {
    @ObservedObject var model: OverviewModel
    @ObservedObject var alerts: AlertsModel
    @ObservedObject var home: HomeModel
    @ObservedObject var skills: SkillsModel
    let go: (AppSection) -> Void

    private let columns = [GridItem(.adaptive(minimum: 320), spacing: 16, alignment: .top)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                hero
                LazyVGrid(columns: columns, alignment: .leading, spacing: 16) {
                    attention
                    nextUp
                    scenes
                    downloads
                    systems
                    if skills.pendingCount > 0 { approvals }
                }
            }
            .padding(24)
        }
        .task {
            while !Task.isCancelled {
                await model.load()
                await skills.load()
                if home.scenes.isEmpty { await home.load() }
                try? await Task.sleep(for: .seconds(20))
            }
        }
    }

    // MARK: Hero

    private var hero: some View {
        HStack(spacing: 20) {
            ReactorEmblem(size: 88)
            VStack(alignment: .leading, spacing: 6) {
                TimelineView(.everyMinute) { context in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(greeting(context.date)).font(.system(size: 28, weight: .semibold))
                        Text(context.date.formatted(.dateTime.weekday(.wide).month(.wide).day().hour().minute()))
                            .foregroundStyle(.white.opacity(0.55))
                    }
                }
                HStack(spacing: 8) {
                    switch model.online {
                    case .some(true): Pill(text: "Core online", color: JarvisTheme.success)
                    case .some(false): Pill(text: "Core offline", color: JarvisTheme.critical)
                    case .none: Pill(text: "Connecting…", color: .gray)
                    }
                    if let location = model.presence?.location {
                        Pill(text: location == "home" ? "You're home" : "You're out", color: JarvisTheme.accent)
                    }
                    if let autopilot = alerts.autopilot {
                        Pill(text: autopilot.enabled ? "Autopilot on" : "Autopilot off",
                             color: autopilot.enabled ? JarvisTheme.gold : .gray)
                    }
                }
            }
            Spacer()
            Button { go(.chat) } label: {
                Label("Ask JARVIS", systemImage: "bubble.left.and.text.bubble.right")
                    .padding(.horizontal, 6)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .keyboardShortcut("k")
            .help("Ask JARVIS (⌘K)")
        }
        .padding(20)
        .background(JarvisTheme.panel, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(JarvisTheme.border))
    }

    private func greeting(_ date: Date) -> String {
        let hour = Calendar.current.component(.hour, from: date)
        let part = hour < 5 ? "Working late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"
        return "\(part), sir."
    }

    // MARK: Cards

    private var attention: some View {
        let items = alerts.alerts.filter { $0.severity != "info" }
        return Panel("Needs attention", symbol: "exclamationmark.triangle", trailing: {
            if !items.isEmpty { Pill(text: "\(items.count)", color: JarvisTheme.warning) }
        }) {
            if items.isEmpty {
                quiet("Nothing needs you.", symbol: "checkmark.seal")
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(items.prefix(3)) { alert in
                        HStack(alignment: .top, spacing: 10) {
                            Capsule().fill(alert.severity == "critical" ? JarvisTheme.critical : JarvisTheme.warning)
                                .frame(width: 3, height: 32)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(alert.title).font(.callout.weight(.medium)).lineLimit(1)
                                Text(alert.message).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            }
                            Spacer(minLength: 0)
                            if alert.repair?.isWorking == true { ProgressView().controlSize(.small) }
                        }
                    }
                    Button("Open Alerts") { go(.alerts) }.buttonStyle(.link)
                }
            }
        }
    }

    private var nextUp: some View {
        Panel("Next up", symbol: "calendar") {
            VStack(alignment: .leading, spacing: 10) {
                if let event = model.now?.nextEvent {
                    row(symbol: "calendar", title: event.title,
                        detail: event.startAt.map { Date(timeIntervalSince1970: $0).formatted(.relative(presentation: .named)) }
                            ?? (event.allDay ? "All day" : ""))
                }
                ForEach(model.now?.timers ?? []) { timer in
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        row(symbol: "timer", title: timer.label,
                            detail: Format.eta(max(0, Int(timer.expiresAt - context.date.timeIntervalSince1970))))
                    }
                }
                ForEach((model.now?.reminders ?? []).prefix(4)) { reminder in
                    row(symbol: reminder.isDue ? "bell.badge" : "checklist", title: reminder.text,
                        detail: reminder.dueAt.map { Date(timeIntervalSince1970: $0).formatted(.relative(presentation: .named)) } ?? "")
                }
                if model.now?.nextEvent == nil && (model.now?.timers.isEmpty ?? true) && (model.now?.reminders.isEmpty ?? true) {
                    quiet("A clear schedule.", symbol: "sun.max")
                }
            }
        }
    }

    private var scenes: some View {
        Panel("Room", symbol: "lightbulb.2", trailing: {
            Button("Open Home") { go(.home) }.buttonStyle(.link)
        }) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 130), spacing: 8)], spacing: 8) {
                ForEach(home.scenes.prefix(8)) { scene in
                    let current = home.currentScene == scene.entityID
                    Button { Task { await home.activate(scene) } } label: {
                        GlowTile(active: current) {
                            HStack(spacing: 8) {
                                Image(systemName: scene.symbol).foregroundStyle(current ? JarvisTheme.accent : .secondary)
                                Text(scene.name).lineLimit(1)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
            if home.scenes.isEmpty { quiet("Scenes load from Home Assistant.", symbol: "house") }
        }
    }

    private var downloads: some View {
        let active = model.activeDownloads
        return Panel("Downloads", symbol: "arrow.down.circle", trailing: {
            if let list = model.downloads { Pill(text: "↓ " + Format.speed(list.dlSpeed)) }
        }) {
            if active.isEmpty {
                quiet("Nothing downloading.", symbol: "tray")
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(active.prefix(4)) { torrent in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(torrent.name).font(.callout).lineLimit(1)
                                Spacer()
                                Text("\(Int(torrent.progress * 100))%").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            }
                            ProgressView(value: torrent.progress).tint(torrent.isPaused ? .gray : JarvisTheme.accent)
                        }
                    }
                    Button("Open Media") { go(.media) }.buttonStyle(.link)
                }
            }
        }
    }

    private var systems: some View {
        let down = model.servicesDown
        return Panel("Systems", symbol: "dot.radiowaves.left.and.right", trailing: {
            if !model.services.isEmpty {
                Pill(text: down.isEmpty ? "Nominal" : "\(down.count) down", color: down.isEmpty ? JarvisTheme.success : JarvisTheme.critical)
            }
        }) {
            VStack(alignment: .leading, spacing: 10) {
                if model.services.isEmpty && model.storage == nil {
                    quiet(model.online == false ? "Can't reach Core." : "Checking the stack…", symbol: "antenna.radiowaves.left.and.right")
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 6)], alignment: .leading, spacing: 6) {
                    ForEach(model.services) { service in
                        HStack(spacing: 6) {
                            Circle().fill(service.up ? JarvisTheme.success : JarvisTheme.critical).frame(width: 7, height: 7)
                            Text(service.name).font(.caption).lineLimit(1)
                        }
                    }
                }
                if let storage = model.storage, storage.error.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text("Media drive").font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            Text("\(Format.bytes(storage.freeBytes)) free").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        }
                        ProgressView(value: min(max(storage.usedFraction, 0), 1))
                            .tint(storage.usedFraction > 0.9 ? JarvisTheme.warning : JarvisTheme.accent)
                    }
                }
            }
        }
    }

    private var approvals: some View {
        Panel("Waiting for you", symbol: "checkmark.shield", trailing: {
            Pill(text: "\(skills.pendingCount)", color: JarvisTheme.gold)
        }) {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(skills.skills.filter(\.isDraft)) { skill in
                    row(symbol: "wand.and.stars", title: "New skill: \(skill.title)", detail: "")
                }
                ForEach(skills.repairs.filter(\.isPending)) { repair in
                    row(symbol: "wrench.and.screwdriver", title: repair.label, detail: "")
                }
                Button("Review in Skills & Fixes") { go(.skills) }.buttonStyle(.link)
            }
        }
    }

    // MARK: Bits

    private func row(symbol: String, title: String, detail: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: symbol).foregroundStyle(JarvisTheme.accent).frame(width: 18)
            Text(title).font(.callout).lineLimit(1)
            Spacer(minLength: 8)
            if !detail.isEmpty { Text(detail).font(.caption.monospacedDigit()).foregroundStyle(.secondary) }
        }
    }

    private func quiet(_ text: String, symbol: String) -> some View {
        Label(text, systemImage: symbol).font(.callout).foregroundStyle(.secondary)
    }
}
