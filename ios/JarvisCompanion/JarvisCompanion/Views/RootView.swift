import SwiftUI

/// Top-level shell, hub-first: Home (default) and Media are dedicated Hub
/// tabs over one shared HubViewModel, Chat keeps its whole NavigationStack,
/// and Settings is the only place settings live.
struct RootView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var chat: ChatViewModel
    @ObservedObject var hub: HubViewModel
    @State private var selection = RootTab.home
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    enum RootTab: Hashable, CaseIterable { case home, media, chat, settings }

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
        .simultaneousGesture(DragGesture(minimumDistance: 30).onEnded(swipe))
        .sensoryFeedback(.selection, trigger: selection)
        .onChange(of: settings.studyAlertsEnabled) { Task { await hub.updateNotificationPreferences() } }
        .onChange(of: selection) { _, tab in
            if tab == .chat { chat.refreshConnectionStatus() }
        }
    }

    /// Horizontal swipe moves to the neighbouring tab. It must be clearly
    /// horizontal and must not start at the left edge (that's the system back
    /// gesture inside pushed screens).
    private func swipe(_ drag: DragGesture.Value) {
        let dx = drag.translation.width, dy = drag.translation.height
        guard drag.startLocation.x > 24, abs(dx) > 60, abs(dx) > 2.5 * abs(dy) else { return }
        let tabs = RootTab.allCases
        guard let index = tabs.firstIndex(of: selection) else { return }
        let next = dx < 0 ? index + 1 : index - 1
        guard tabs.indices.contains(next) else { return }
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.25)) { selection = tabs[next] }
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
