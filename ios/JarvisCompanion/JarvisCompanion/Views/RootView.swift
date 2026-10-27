import SwiftUI

/// Top-level shell, hub-first: Home (default) and Media are dedicated Hub
/// tabs over one shared HubViewModel, Chat keeps its whole NavigationStack,
/// and Settings is the only place settings live.
struct RootView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var chat: ChatViewModel
    @ObservedObject var hub: HubViewModel
    @State private var selection = RootTab.home
    /// Horizontal offset of the visible tab while swiping, and while the new
    /// tab slides in. The tab bar itself never moves.
    @State private var swipeOffset: CGFloat = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    enum RootTab: Hashable, CaseIterable { case home, media, chat, settings }

    var body: some View {
        TabView(selection: $selection) {
            HubView(model: hub, onAsk: ask, initialFocus: .today, isTab: true)
                .swipePage(swipeOffset)
                .tabItem { Label("Home", systemImage: "house") }
                .tag(RootTab.home)

            HubView(model: hub, onAsk: ask, initialFocus: .media, isTab: true)
                .swipePage(swipeOffset)
                .tabItem { Label("Media", systemImage: "play.rectangle.on.rectangle") }
                .tag(RootTab.media)

            ChatView(model: chat)
                .swipePage(swipeOffset)
                .tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right") }
                .tag(RootTab.chat)

            SettingsView(embedded: true, studyBrief: hub.studyBrief.value)
                .swipePage(swipeOffset)
                .tabItem { Label("Settings", systemImage: "slider.horizontal.3") }
                .tag(RootTab.settings)
        }
        .tint(JarvisTheme.cyan)
        .simultaneousGesture(DragGesture(minimumDistance: 30).onChanged(track).onEnded(swipe))
        .sensoryFeedback(.selection, trigger: selection)
        .onChange(of: settings.studyAlertsEnabled) { Task { await hub.updateNotificationPreferences() } }
        .onChange(of: selection) { _, tab in
            if tab == .chat { chat.refreshConnectionStatus() }
        }
    }

    private func neighbour(towards dx: CGFloat) -> RootTab? {
        let tabs = RootTab.allCases
        guard let index = tabs.firstIndex(of: selection) else { return nil }
        let next = dx < 0 ? index + 1 : index - 1
        return tabs.indices.contains(next) ? tabs[next] : nil
    }

    /// The page follows the finger (rubber-banded at the first/last tab) only
    /// while the drag is clearly horizontal, so vertical scrolling is untouched.
    private func track(_ drag: DragGesture.Value) {
        guard !reduceMotion, drag.startLocation.x > 24 else { return }
        let dx = drag.translation.width, dy = drag.translation.height
        guard abs(dx) > 2.5 * abs(dy) else {
            if swipeOffset != 0 { withAnimation(.spring(duration: 0.25)) { swipeOffset = 0 } }
            return
        }
        swipeOffset = neighbour(towards: dx) == nil ? dx * 0.15 : dx * 0.5
    }

    /// Horizontal swipe moves to the neighbouring tab, which slides in from
    /// the side the finger came from. It must be clearly horizontal and must
    /// not start at the left edge (that's the system back gesture inside
    /// pushed screens).
    private func swipe(_ drag: DragGesture.Value) {
        let dx = drag.translation.width, dy = drag.translation.height
        guard drag.startLocation.x > 24, abs(dx) > 60, abs(dx) > 2.5 * abs(dy), let next = neighbour(towards: dx) else {
            withAnimation(.spring(duration: 0.3)) { swipeOffset = 0 }
            return
        }
        selection = next
        guard !reduceMotion else { return }
        swipeOffset = dx < 0 ? 140 : -140
        withAnimation(.spring(duration: 0.35, bounce: 0.12)) { swipeOffset = 0 }
    }

    /// Hub "ask" shortcuts drop the prompt into Chat's composer and switch there.
    /// (Programmatic switches don't slide; only a swipe does.)
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

private extension View {
    /// Offset and gentle fade for the tab being swiped.
    func swipePage(_ offset: CGFloat) -> some View {
        self.offset(x: offset)
            .opacity(1 - min(abs(offset) / 500, 0.3))
    }
}
