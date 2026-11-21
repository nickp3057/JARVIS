import AppKit
import SwiftUI

enum AppSection: String, CaseIterable, Identifiable {
    case overview, chat, home, media, alerts, skills, donna
    var id: String { rawValue }

    var title: String {
        switch self {
        case .overview: return "Overview"
        case .chat: return "Chat"
        case .home: return "Home"
        case .media: return "Media"
        case .alerts: return "Alerts"
        case .skills: return "Skills & Fixes"
        case .donna: return "Donna"
        }
    }

    var symbol: String {
        switch self {
        case .overview: return "circle.hexagongrid"
        case .chat: return "bubble.left.and.text.bubble.right"
        case .home: return "lightbulb.2"
        case .media: return "film.stack"
        case .alerts: return "bell.badge"
        case .skills: return "wand.and.stars"
        case .donna: return "heart.text.square"
        }
    }

    var shortcut: KeyEquivalent {
        KeyEquivalent(Character(String((AppSection.allCases.firstIndex(of: self) ?? 0) + 1)))
    }
}

extension Notification.Name {
    static let jarvisNewChat = Notification.Name("jarvisNewChat")
}

/// Every page's model, made once and shared by the window and the menu bar.
@MainActor
final class AppModels: ObservableObject {
    let chat: ChatModel
    let home: HomeModel
    let alerts: AlertsModel
    let donna: DonnaModel
    let overview: OverviewModel
    let media: MediaModel
    let skills: SkillsModel

    init(settings: MacSettings) {
        chat = ChatModel(settings: settings)
        home = HomeModel(settings: settings)
        alerts = AlertsModel(settings: settings)
        donna = DonnaModel(settings: settings)
        overview = OverviewModel(settings: settings)
        media = MediaModel(settings: settings)
        skills = SkillsModel(settings: settings)
    }
}

@main
struct JarvisMacApp: App {
    @StateObject private var settings: MacSettings
    @StateObject private var models: AppModels
    @AppStorage("section") private var section: AppSection = .overview

    init() {
        // Run as a bare binary (swift run) there's no bundle to make it a
        // regular app; this gives it a Dock icon and keyboard focus.
        NSApplication.shared.setActivationPolicy(.regular)
        let settings = MacSettings()
        _settings = StateObject(wrappedValue: settings)
        _models = StateObject(wrappedValue: AppModels(settings: settings))
        Snapshot.runIfAsked()
    }

    var body: some Scene {
        Window("JARVIS", id: "main") {
            RootView(models: models)
                .environmentObject(settings)
                .frame(minWidth: 900, minHeight: 600)
                .preferredColorScheme(.dark)
        }
        .defaultSize(width: 1240, height: 820)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Chat") {
                    section = .chat
                    NotificationCenter.default.post(name: .jarvisNewChat, object: nil)
                }
                .keyboardShortcut("n")
            }
            CommandGroup(before: .sidebar) {
                ForEach(AppSection.allCases) { item in
                    Button(item.title) { section = item }
                        .keyboardShortcut(item.shortcut)
                }
                Divider()
            }
        }

        MenuBarExtra {
            MenuBarPanel(models: models, open: { item in
                section = item
                NSApp.activate()
                NSApp.windows.first { $0.identifier?.rawValue == "main" || $0.title == "JARVIS" }?.makeKeyAndOrderFront(nil)
            })
            .environmentObject(settings)
            .preferredColorScheme(.dark)
        } label: {
            Image(systemName: models.alerts.attentionCount > 0 ? "circle.hexagongrid.fill" : "circle.hexagongrid")
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView()
                .environmentObject(settings)
                .preferredColorScheme(.dark)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var settings: MacSettings
    @AppStorage("section") private var section: AppSection = .overview
    @ObservedObject var models: AppModels
    @ObservedObject private var alerts: AlertsModel
    @ObservedObject private var skills: SkillsModel

    init(models: AppModels) {
        self.models = models
        _alerts = ObservedObject(wrappedValue: models.alerts)
        _skills = ObservedObject(wrappedValue: models.skills)
    }

    var body: some View {
        NavigationSplitView {
            VStack(spacing: 0) {
                HStack(spacing: 10) {
                    ReactorEmblem(size: 30)
                    Text("JARVIS").font(.system(size: 15, weight: .bold)).tracking(3)
                    Spacer()
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                List(selection: Binding(get: { section }, set: { if let new = $0 { section = new } })) {
                    Section("JARVIS") {
                        row(.overview)
                        row(.chat)
                        row(.home)
                        row(.media)
                        row(.alerts).badge(alerts.attentionCount)
                        row(.skills).badge(skills.pendingCount)
                    }
                    Section("Private") {
                        row(.donna)
                    }
                }
                .scrollContentBackground(.hidden)
            }
            .navigationSplitViewColumnWidth(min: 190, ideal: 210, max: 280)
        } detail: {
            ZStack {
                if section != .donna { CockpitBackdrop() }
                Group {
                    if !settings.isConfigured {
                        SetupPrompt()
                    } else {
                        switch section {
                        case .overview:
                            OverviewView(model: models.overview, alerts: models.alerts, home: models.home,
                                         skills: models.skills) { section = $0 }
                        case .chat: ChatView(model: models.chat)
                        case .home: HomeView(model: models.home)
                        case .media: MediaView(model: models.media)
                        case .alerts: AlertsView(model: models.alerts)
                        case .skills: SkillsView(model: models.skills)
                        case .donna: DonnaLock { DonnaView(model: models.donna) { section = .overview } }
                        }
                    }
                }
            }
            .navigationTitle(section.title)
        }
        .tint(section == .donna ? DonnaTheme.rose : JarvisTheme.accent)
        // Keep the badges current while the app is open.
        .task(id: settings.isConfigured) {
            guard settings.isConfigured else { return }
            await alerts.loadAutopilot()
            while !Task.isCancelled {
                await alerts.load()
                await skills.load()
                try? await Task.sleep(for: .seconds(60))
            }
        }
    }

    private func row(_ item: AppSection) -> some View {
        Label(item.title, systemImage: item.symbol).tag(item)
    }
}

/// The menu bar JARVIS: what needs you, the room's scenes, and a way in.
struct MenuBarPanel: View {
    @ObservedObject var models: AppModels
    @ObservedObject private var alerts: AlertsModel
    @ObservedObject private var home: HomeModel
    let open: (AppSection) -> Void

    init(models: AppModels, open: @escaping (AppSection) -> Void) {
        self.models = models
        _alerts = ObservedObject(wrappedValue: models.alerts)
        _home = ObservedObject(wrappedValue: models.home)
        self.open = open
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                ReactorEmblem(size: 34)
                VStack(alignment: .leading, spacing: 2) {
                    Text("JARVIS").font(.headline).tracking(2)
                    Text(alerts.attentionCount == 0 ? "All quiet, sir." : "\(alerts.attentionCount) thing\(alerts.attentionCount == 1 ? "" : "s") need you")
                        .font(.caption).foregroundStyle(alerts.attentionCount == 0 ? .secondary : JarvisTheme.warning)
                }
                Spacer()
            }
            ForEach(alerts.alerts.filter { $0.severity != "info" }.prefix(3)) { alert in
                Button { open(.alerts) } label: {
                    HStack(spacing: 8) {
                        Circle().fill(alert.severity == "critical" ? JarvisTheme.critical : JarvisTheme.warning).frame(width: 7, height: 7)
                        Text(alert.title).lineLimit(1)
                        Spacer()
                    }
                }
                .buttonStyle(.plain)
            }
            if !home.scenes.isEmpty {
                Text("SCENES").font(.caption2.weight(.semibold)).tracking(1.2).foregroundStyle(.secondary)
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 6) {
                    ForEach(home.scenes.prefix(8)) { scene in
                        let current = home.currentScene == scene.entityID
                        Button { Task { await home.activate(scene) } } label: {
                            HStack(spacing: 6) {
                                Image(systemName: scene.symbol)
                                Text(scene.name).lineLimit(1)
                                Spacer(minLength: 0)
                            }
                            .padding(.horizontal, 8).padding(.vertical, 6)
                            .background(current ? JarvisTheme.accent.opacity(0.18) : Color.white.opacity(0.05), in: .rect(cornerRadius: 7))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            Divider()
            HStack {
                Button("Open JARVIS") { open(.overview) }
                Button("Chat") { open(.chat) }
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }
            }
        }
        .padding(14)
        .frame(width: 320)
        .background(CockpitBackdrop())
        .task {
            await alerts.load()
            await home.load()
        }
    }
}
