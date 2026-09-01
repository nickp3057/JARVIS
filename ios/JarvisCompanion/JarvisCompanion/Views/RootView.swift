import SwiftUI

/// Top-level shell: Chat and the Hub as peer destinations. Chat keeps its
/// whole existing NavigationStack untouched inside its tab.
struct RootView: View {
    @ObservedObject var chat: ChatViewModel
    @ObservedObject var hub: HubViewModel

    var body: some View {
        TabView {
            ChatView(model: chat)
                .tabItem {
                    Label("Chat", systemImage: "bubble.left.and.bubble.right")
                }

            HubView(model: hub)
                .tabItem {
                    Label("Hub", systemImage: "gauge.with.dots.needle.50percent")
                }
        }
    }
}
