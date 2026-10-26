import SwiftUI

/// Top-level shell, hub-first: Home (default) and Media are dedicated Hub
/// tabs over one shared HubViewModel, Chat keeps its whole NavigationStack,
/// and Settings is the only place settings live.
struct RootView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var chat: ChatViewModel
    @ObservedObject var hub: HubViewModel
    @State private var selection = RootTab.home

    enum RootTab: Hashable { case home, media, chat, settings }

    var body: some View {
        TabView(selection: $selection) {
            HubView(model: hub, onAsk: ask, initialFocus: .today, isTab: true)
                .tabItem { Label("Home", systemImage: "house") }
                .tag(RootTab.home)

            HubView(model: hub, onAsk: ask, initialFocus: .media, isTab: true)
                .tabItem { Label("Media", systemImage: "play.rectangle.on.rectangle") }
                .tag(RootTab.media)

            ChatView(model: chat)
                .tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right") }
                .tag(RootTab.chat)

            SettingsView(embedded: true, studyBrief: hub.studyBrief.value)
                .tabItem { Label("Settings", systemImage: "slider.horizontal.3") }
                .tag(RootTab.settings)
        }
        .tint(JarvisTheme.cyan)
        .preferredColorScheme(.dark)
        .toolbarBackground(JarvisTheme.background, for: .tabBar)
        .toolbarBackground(.visible, for: .tabBar)
        .onChange(of: settings.studyAlertsEnabled) { Task { await hub.updateNotificationPreferences() } }
        .onChange(of: selection) { _, tab in
            if tab == .chat { chat.refreshConnectionStatus() }
        }
    }

    /// Hub "ask" shortcuts drop the prompt into Chat's composer and switch there.
    private func ask(_ prompt: String) {
        if chat.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            chat.draft = prompt
        } else {
            chat.draft += "\n" + prompt
        }
        chat.draftChanged()
        selection = .chat
    }
}
