import AppKit
import SwiftUI

@main
struct DonnaMacApp: App {
    @StateObject private var settings = MacSettings()

    init() {
        // Launched as a bare SwiftPM binary (swift run) there is no bundle to
        // make it a regular app; this gives it a Dock icon and keyboard focus.
        NSApplication.shared.setActivationPolicy(.regular)
    }

    var body: some Scene {
        Window("Donna", id: "donna") {
            DonnaWindow(settings: settings)
                .environmentObject(settings)
                .frame(minWidth: 420, minHeight: 480)
        }
        .defaultSize(width: 560, height: 720)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }

        Settings {
            SettingsView()
                .environmentObject(settings)
        }
    }
}
