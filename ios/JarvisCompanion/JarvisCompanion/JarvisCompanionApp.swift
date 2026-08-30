import SwiftUI

@main
struct JarvisCompanionApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var settings: AppSettings
    @StateObject private var chat: ChatViewModel

    init() {
        let settings = AppSettings()
        _settings = StateObject(wrappedValue: settings)
        _chat = StateObject(wrappedValue: ChatViewModel(settings: settings))
    }

    var body: some Scene {
        WindowGroup {
            ChatView(model: chat)
                .environmentObject(settings)
                .onChange(of: scenePhase) { _, phase in
                    switch phase {
                    case .active:
                        chat.appBecameActive()
                    case .inactive, .background:
                        chat.appBecameInactive()
                    @unknown default:
                        break
                    }
                }
        }
    }
}
