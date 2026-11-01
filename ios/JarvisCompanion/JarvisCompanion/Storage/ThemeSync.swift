import Foundation

/// Keeps the colour theme the same on the phone and the TV: the pick lives on
/// JARVIS Core (`/api/theme`). Pulled every 10 s while the app is open
/// (RootView), pushed when Nick picks one. The Experimenting switches ride along. An older Core without the endpoint leaves the local pick alone.
@MainActor
enum ThemeSync {
    static func pull(_ settings: AppSettings) async {
        guard settings.isConfigured, let client = try? JarvisAPIClient.make(from: settings),
              let shared = try? await client.sharedTheme() else { return }
        let local = JarvisPalette.current.rawValue
        // Experimenting switches follow Core too (unset ones keep the app default).
        for (name, on) in shared.experiments ?? [:] {
            guard let experiment = Experiment(rawValue: name), experiment.isOn != on else { continue }
            UserDefaults.standard.set(on, forKey: experiment.storageKey)
        }
        if shared.chosen == true {
            if shared.theme != local, JarvisPalette(rawValue: shared.theme) != nil {
                UserDefaults.standard.set(shared.theme, forKey: JarvisPalette.storageKey)
            }
        } else if shared.theme != local {
            // Nobody has picked on Core yet: the phone's existing pick becomes the shared one.
            try? await client.saveSharedTheme(local)
        }
    }

    static func pushExperiment(_ experiment: Experiment, on: Bool, settings: AppSettings) async {
        guard settings.isConfigured, let client = try? JarvisAPIClient.make(from: settings) else { return }
        try? await client.saveExperiments([experiment.rawValue: on])
    }

    static func push(_ theme: String, settings: AppSettings) async {
        guard settings.isConfigured, let client = try? JarvisAPIClient.make(from: settings) else { return }
        try? await client.saveSharedTheme(theme)
    }
}
