import SwiftUI
import UIKit

/// Top-level shell: Media, Hub and Home (default, in the middle) are HubView tabs over one
/// shared HubViewModel, Chat keeps its whole NavigationStack, and Settings is
/// the only place settings live.
struct RootView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var chat: ChatViewModel
    @ObservedObject var hub: HubViewModel
    @State private var selection = RootView.isMac ? RootTab.desk : RootView.isPad ? RootTab.room : RootTab.home
    /// Horizontal offset of the visible tab while swiping, and while the new
    /// tab slides in. The tab bar itself never moves.
    @State private var swipeOffset: CGFloat = 0
    /// Re-identifies the tab content so every palette colour re-resolves.
    @AppStorage(JarvisPalette.storageKey) private var palette = JarvisPalette.arc.rawValue
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase
    @ObservedObject private var wakeRinging = WakeRinging.shared
    /// The iPad as the room camera: runs the lens while Nick's phone watches.
    @StateObject private var camera = RoomCameraController()
    /// iPad: slide back to Room after a few idle minutes on another tab.
    @AppStorage(RoomView.autoReturnKey) private var autoReturn = true
    /// Bumped by any touch, so the trip back to Room waits for a quiet spell.
    @State private var touches = 0
    /// Room's full screen: the iPad stays on Room, no swiping to other tabs.
    @AppStorage("room.fullScreen") private var roomFullScreen = false

    /// No swipe to another tab: in Room's full screen, or while a control
    /// (the volume slider, a tile drag) has the finger (`SwipeLock`).
    private var swipeBlocked: Bool {
        (Self.isPad && !Self.isMac && selection == .room && roomFullScreen) || SwipeLock.active
    }

    /// Tab-bar (and swipe) order: Home sits in the middle. The iPad adds Room
    /// first (its default): the tablet is mainly a panel for the room. The
    /// Mac swaps Room and Home for Desk, everything on one screen.
    enum RootTab: Hashable, CaseIterable { case room, desk, media, hub, home, chat, settings }

    /// True on the Mac too (Mac Catalyst runs the iPad layout, Room tab included).
    static let isPad = UIDevice.current.userInterfaceIdiom == .pad
    /// The laptop app: no night screen, no wall-panel habits.
    static let isMac = ProcessInfo.processInfo.isMacCatalystApp
    static let tabsInOrder = tabs
    private static let tabs = RootTab.allCases.filter { tab in
        switch tab {
        case .room: return isPad && !isMac
        case .desk: return isMac
        case .home: return !isMac
        default: return true
        }
    }

    var body: some View {
        TabView(selection: $selection) {
            if Self.isMac {
                HubView(model: hub, onAsk: ask, onNavigate: navigate, onSend: sendNow, focus: .desk)
                    .swipePage(swipeOffset)
                    .tabItem { Label("Desk", systemImage: "rectangle.3.group") }
                    .tag(RootTab.desk)
            }

            if Self.isPad && !Self.isMac {
                RoomView(model: hub)
                    .swipePage(swipeOffset)
                    .tabItem { Label("Room", systemImage: "lightbulb.2") }
                    .tag(RootTab.room)
            }

            HubView(model: hub, onAsk: ask, focus: .media)
                .swipePage(swipeOffset)
                .tabItem { Label("Media", systemImage: "play.rectangle.on.rectangle") }
                .tag(RootTab.media)

            HubView(model: hub, onAsk: ask, onNavigate: navigate, focus: .hub)
                .swipePage(swipeOffset)
                .tabItem { Label("Hub", systemImage: "square.grid.2x2") }
                .tag(RootTab.hub)

            if !Self.isMac {
                HubView(model: hub, onAsk: ask, onNavigate: navigate, focus: .home)
                    .swipePage(swipeOffset)
                    .tabItem { Label("Home", systemImage: "house") }
                    .tag(RootTab.home)
            }

            ChatView(model: chat)
                .swipePage(swipeOffset)
                .tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right") }
                .tag(RootTab.chat)

            SettingsView(embedded: true, studyBrief: hub.studyBrief.value)
                .swipePage(swipeOffset)
                .tabItem { Label("Settings", systemImage: "slider.horizontal.3") }
                .tag(RootTab.settings)
        }
        .id(palette)
        .tint(JarvisTheme.reactor)
        .simultaneousGesture(DragGesture(minimumDistance: 30).onChanged(track).onEnded(swipe))
        .simultaneousGesture(TapGesture().onEnded { touches &+= 1 })
        .task(id: "\(selection)-\(touches)-\(autoReturn)") {
            // Chat is left alone: a half-typed message shouldn't be yanked away.
            guard Self.isPad, !Self.isMac, autoReturn, selection != .room, selection != .chat else { return }
            try? await Task.sleep(for: .seconds(180))
            if !Task.isCancelled, !wakeRinging.showing { selection = .room }
        }
        .sensoryFeedback(.selection, trigger: selection)
        .focusedSceneValue(\.rootTab, $selection)
        .onAppear(perform: sizeMacWindow)
        .fullScreenCover(isPresented: $wakeRinging.showing) { WakeRingingView().environmentObject(settings) }
        .overlay(alignment: .top) {
            // The visible "Nick is watching" signal while the iPad's lens is on.
            if camera.watching {
                CameraWatchOrb().padding(.top, 60).transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.3), value: camera.watching)
        .task { camera.activate(settings: settings) }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { camera.activate(settings: settings) } else { camera.deactivate() }
        }
        .task(id: scenePhase) {
            // Follow the shared theme while the app is open (a pick on the TV,
            // Barn Door engaging or standing down), not just on launch.
            guard scenePhase == .active else { return }
            // While the home stream is up it brings theme changes at once
            // (HubViewModel), so this only runs when it's down.
            await ThemeSync.pull(settings)
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(10))
                if !hub.homeStreamLive { await ThemeSync.pull(settings) }
            }
        }
        .onChange(of: settings.studyAlertsEnabled) { Task { await hub.updateNotificationPreferences() } }
        .onChange(of: selection) { _, tab in
            if tab == .chat { chat.refreshConnectionStatus() }
        }
    }

    /// Mac: keep the window big enough for the Room tab's two columns.
    private func sizeMacWindow() {
        guard Self.isMac else { return }
        for case let scene as UIWindowScene in UIApplication.shared.connectedScenes {
            scene.sizeRestrictions?.minimumSize = CGSize(width: 900, height: 640)
        }
    }

    private func neighbour(towards dx: CGFloat) -> RootTab? {
        let tabs = Self.tabs
        guard let index = tabs.firstIndex(of: selection) else { return nil }
        let next = dx < 0 ? index + 1 : index - 1
        return tabs.indices.contains(next) ? tabs[next] : nil
    }

    /// The page follows the finger (rubber-banded at the first/last tab) only
    /// while the drag is clearly horizontal, so vertical scrolling is untouched.
    private func track(_ drag: DragGesture.Value) {
        touches &+= 1
        guard !reduceMotion, drag.startLocation.x > 24, !swipeBlocked else {
            if swipeOffset != 0 { withAnimation(.spring(duration: 0.25)) { swipeOffset = 0 } }
            return
        }
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
        guard !swipeBlocked, drag.startLocation.x > 24, abs(dx) > 60, abs(dx) > 2.5 * abs(dy),
              let next = neighbour(towards: dx) else {
            withAnimation(.spring(duration: 0.3)) { swipeOffset = 0 }
            return
        }
        selection = next
        guard !reduceMotion else { return }
        swipeOffset = dx < 0 ? 140 : -140
        withAnimation(.spring(duration: 0.35, bounce: 0.12)) { swipeOffset = 0 }
    }

    /// Home's chips: slide over to Hub or Media (which scrolls to the card).
    private func navigate(_ focus: HubView.HubFocus) {
        let target: RootTab = focus == .media ? .media : focus == .hub ? .hub : focus == .desk ? .desk : .home
        guard target != selection, let from = Self.tabs.firstIndex(of: selection),
              let to = Self.tabs.firstIndex(of: target) else { return }
        selection = target
        guard !reduceMotion else { return }
        swipeOffset = to > from ? 140 : -140
        withAnimation(.spring(duration: 0.35, bounce: 0.12)) { swipeOffset = 0 }
    }

    /// Desk's ask bar: straight into Chat and sent.
    private func sendNow(_ prompt: String) {
        chat.draft = prompt
        chat.draftChanged()
        selection = .chat
        chat.send()
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

extension RootView.RootTab {
    var title: String {
        switch self {
        case .room: return "Room"
        case .desk: return "Desk"
        case .media: return "Media"
        case .hub: return "Hub"
        case .home: return "Home"
        case .chat: return "Chat"
        case .settings: return "Settings"
        }
    }
}

private struct RootTabKey: FocusedValueKey {
    typealias Value = Binding<RootView.RootTab>
}

extension FocusedValues {
    /// The front window's tab, for the menu bar's Go menu.
    var rootTab: Binding<RootView.RootTab>? {
        get { self[RootTabKey.self] }
        set { self[RootTabKey.self] = newValue }
    }
}

/// Mac (and iPad keyboard) menus: ⌘1… for the tabs, ⌘, for Settings.
struct RootTabCommands: Commands {
    @FocusedBinding(\.rootTab) private var tab

    var body: some Commands {
        CommandGroup(replacing: .appSettings) {
            Button("Settings…") { tab = .settings }
                .keyboardShortcut(",")
                .disabled(tab == nil)
        }
        CommandMenu("Go") {
            ForEach(Array(RootView.tabsInOrder.enumerated()), id: \.element) { index, item in
                Button(item.title) { tab = item }
                    .keyboardShortcut(KeyEquivalent(Character(String(index + 1))))
                    .disabled(tab == nil)
            }
        }
    }
}
