import AppKit
import SwiftUI

enum AppSection: String, CaseIterable, Identifiable {
    case chat, home, alerts, donna
    var id: String { rawValue }

    var title: String {
        switch self {
        case .chat: return "Chat"
        case .home: return "Home"
        case .alerts: return "Alerts"
        case .donna: return "Donna"
        }
    }

    var symbol: String {
        switch self {
        case .chat: return "bubble.left.and.text.bubble.right"
        case .home: return "house"
        case .alerts: return "bell.badge"
        case .donna: return "heart.text.square"
        }
    }

    var shortcut: KeyEquivalent {
        switch self {
        case .chat: return "1"
        case .home: return "2"
        case .alerts: return "3"
        case .donna: return "4"
        }
    }
}

extension Notification.Name {
    static let jarvisNewChat = Notification.Name("jarvisNewChat")
}

@main
struct JarvisMacApp: App {
    @StateObject private var settings = MacSettings()
    @AppStorage("section") private var section: AppSection = .chat

    init() {
        // Run as a bare binary (swift run) there's no bundle to make it a
        // regular app; this gives it a Dock icon and keyboard focus.
        NSApplication.shared.setActivationPolicy(.regular)
    }

    var body: some Scene {
        Window("JARVIS", id: "main") {
            RootView(settings: settings)
                .environmentObject(settings)
                .frame(minWidth: 760, minHeight: 520)
        }
        .defaultSize(width: 1080, height: 760)
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

        Settings {
            SettingsView()
                .environmentObject(settings)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var settings: MacSettings
    @AppStorage("section") private var section: AppSection = .chat
    @StateObject private var chat: ChatModel
    @StateObject private var home: HomeModel
    @StateObject private var alerts: AlertsModel
    @StateObject private var donna: DonnaModel

    init(settings: MacSettings) {
        _chat = StateObject(wrappedValue: ChatModel(settings: settings))
        _home = StateObject(wrappedValue: HomeModel(settings: settings))
        _alerts = StateObject(wrappedValue: AlertsModel(settings: settings))
        _donna = StateObject(wrappedValue: DonnaModel(settings: settings))
    }

    var body: some View {
        NavigationSplitView {
            List(selection: Binding(get: { section }, set: { if let new = $0 { section = new } })) {
                Section("JARVIS") {
                    row(.chat)
                    row(.home)
                    row(.alerts).badge(alerts.attentionCount)
                }
                Section("Private") {
                    row(.donna)
                }
            }
            .navigationSplitViewColumnWidth(min: 170, ideal: 190, max: 260)
        } detail: {
            Group {
                if !settings.isConfigured {
                    SetupPrompt()
                } else {
                    switch section {
                    case .chat: ChatView(model: chat)
                    case .home: HomeView(model: home)
                    case .alerts: AlertsView(model: alerts)
                    case .donna: DonnaLock { DonnaView(model: donna) { section = .chat } }
                    }
                }
            }
            .navigationTitle(section.title)
        }
        .tint(section == .donna ? DonnaTheme.rose : JarvisTheme.accent)
        // Keep the alert badge current while the app is open.
        .task(id: settings.isConfigured) {
            guard settings.isConfigured else { return }
            while !Task.isCancelled {
                await alerts.load()
                try? await Task.sleep(for: .seconds(60))
            }
        }
    }

    private func row(_ item: AppSection) -> some View {
        Label(item.title, systemImage: item.symbol).tag(item)
    }
}
