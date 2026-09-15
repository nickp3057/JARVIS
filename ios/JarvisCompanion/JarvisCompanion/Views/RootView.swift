import SwiftUI

/// Top-level shell: Chat, Hub, and Settings as peer destinations. Chat keeps its
/// whole existing NavigationStack untouched inside its tab.
struct RootView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var chat: ChatViewModel
    @ObservedObject var hub: HubViewModel
    @State private var selection = 0

    var body: some View {
        TabView(selection: $selection) {
            ChatView(model: chat)
                .tabItem {
                    Label("Chat", systemImage: "bubble.left.and.bubble.right")
                }
                .tag(0)

            HubView(model: hub, onAsk: { prompt in
                if chat.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    chat.draft = prompt
                } else {
                    chat.draft += "\n" + prompt
                }
                chat.draftChanged()
                selection = 0
            })
                .tabItem {
                    Label("Hub", systemImage: "gauge.with.dots.needle.50percent")
                }
                .tag(1)
            SettingsView(embedded: true, studyBrief: hub.studyBrief.value)
                .tabItem { Label("Settings", systemImage: "slider.horizontal.3") }
                .tag(2)
        }
        .tint(JarvisTheme.cyan)
        .preferredColorScheme(.dark)
        .toolbarBackground(JarvisTheme.background, for: .tabBar)
        .toolbarBackground(.visible, for: .tabBar)
        .onChange(of: settings.studyAlertsEnabled) { Task { await hub.updateNotificationPreferences() } }
        .onChange(of: selection) { _, tab in
            if tab == 0 { chat.refreshConnectionStatus() }
        }
    }
}
