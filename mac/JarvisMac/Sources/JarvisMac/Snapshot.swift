import AppKit
import SwiftUI

/// `JARVIS --snapshot <dir>`: draws each main page to a PNG (no window, no
/// screen recording) and quits, for checking the layout. Live data loads for
/// a few seconds first when Core is reachable.
@MainActor
enum Snapshot {
    static func runIfAsked() {
        let args = CommandLine.arguments
        guard let index = args.firstIndex(of: "--snapshot"), index + 1 < args.count else { return }
        let dir = URL(fileURLWithPath: args[index + 1])
        let settings = MacSettings()
        let models = AppModels(settings: settings)
        Task { @MainActor in
            await models.overview.load()
            await models.alerts.load()
            await models.alerts.loadAutopilot()
            await models.home.load()
            await models.skills.load()
            await models.media.loadTorrents()
            await models.media.loadTitles()
            let pages: [(String, AnyView)] = [
                ("overview", AnyView(OverviewView(model: models.overview, alerts: models.alerts, home: models.home,
                                                  skills: models.skills) { _ in })),
                ("home", AnyView(HomeView(model: models.home))),
                ("media", AnyView(MediaView(model: models.media))),
                ("skills", AnyView(SkillsView(model: models.skills))),
                ("chat", AnyView(ChatView(model: models.chat))),
            ]
            for (name, page) in pages {
                let view = ZStack { CockpitBackdrop(); page }
                    .environmentObject(settings)
                    .environment(\.colorScheme, .dark)
                let host = NSHostingView(rootView: view)
                let window = NSWindow(contentRect: NSRect(x: -4000, y: -4000, width: 1100, height: 800),
                                      styleMask: [.borderless], backing: .buffered, defer: false)
                window.appearance = NSAppearance(named: .darkAqua)
                window.contentView = host
                window.orderFrontRegardless()
                try? await Task.sleep(for: .seconds(2.5))  // let tasks load and posters arrive
                host.layoutSubtreeIfNeeded()
                if let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
                    host.cacheDisplay(in: host.bounds, to: rep)
                    try? rep.representation(using: .png, properties: [:])?.write(to: dir.appendingPathComponent("\(name).png"))
                }
                window.orderOut(nil)
            }
            exit(0)
        }
    }
}
