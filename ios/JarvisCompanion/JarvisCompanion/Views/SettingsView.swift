import SwiftUI
import UserNotifications

struct SettingsView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    var embedded = false
    /// The schedule as JARVIS Core reports it (nil until the Hub has loaded).
    var studyBrief: StudyBrief? = nil
    @State private var revealToken = false
    @State private var testing = false
    @State private var connectionResult: String?
    @State private var connectionSucceeded = false
    @State private var notificationStatus = "Checking permission…"
    @State private var permissionUndetermined = false
    @State private var showingKnowledge = false
    @ObservedObject private var push = PushRegistrar.shared

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    CockpitHeader(eyebrow: "Personal command center", title: "Make it yours.",
                                  subtitle: "Your connection. Your daily rhythm.")
                    CockpitPanel("Connection", symbol: "network") {
                        Text("JARVIS Core").font(.headline)
                        TextField("Server address", text: $settings.baseURLString)
                            .keyboardType(.URL).textContentType(.URL)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                            .padding(13).background(.black.opacity(0.2), in: .rect(cornerRadius: 12))
                            .accessibilityLabel("Server address")
                        HStack {
                            Group {
                                if revealToken { TextField("API token", text: $settings.apiToken) }
                                else { SecureField("API token", text: $settings.apiToken) }
                            }
                            .textContentType(.password).textInputAutocapitalization(.never).autocorrectionDisabled()
                            Button { revealToken.toggle() } label: {
                                Image(systemName: revealToken ? "eye.slash" : "eye").frame(width: 44, height: 44)
                            }
                            .accessibilityLabel(revealToken ? "Hide token" : "Show token")
                        }
                        .padding(.leading, 13).background(.black.opacity(0.2), in: .rect(cornerRadius: 12))
                        Text("Your token is stored securely in Keychain. Use your home network or Tailscale address.")
                            .font(.caption).foregroundStyle(.secondary)
                        if let error = settings.tokenStorageError {
                            Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(JarvisTheme.amber)
                        }
                        Button(action: testConnection) {
                            HStack {
                                if testing { ProgressView().tint(JarvisTheme.background) }
                                Text(testing ? "Connecting…" : "Test connection").font(.subheadline.weight(.semibold))
                                Spacer()
                                Image(systemName: "arrow.up.right")
                            }
                            .padding(14).foregroundStyle(JarvisTheme.background)
                            .background(JarvisTheme.cyan, in: .rect(cornerRadius: 12))
                        }
                        .disabled(testing || !settings.isConfigured)
                        .opacity(settings.isConfigured ? 1 : 0.45)
                        if let connectionResult {
                            Label(connectionResult, systemImage: connectionSucceeded ? "checkmark.circle.fill" : "wifi.exclamationmark")
                                .font(.footnote).foregroundStyle(connectionSucceeded ? JarvisTheme.success : JarvisTheme.amber)
                        }
                    }
                    if !RootView.isPad {
                        CockpitPanel("Memory", symbol: "brain") {
                            Button { showingKnowledge = true } label: {
                                HStack {
                                    Text("What JARVIS knows about you").foregroundStyle(.primary)
                                    Spacer()
                                    Image(systemName: "chevron.right").foregroundStyle(.secondary)
                                }
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            Text("His profile memory, journal and notes from the Obsidian vault, read-only.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                    CockpitPanel("Theme", symbol: "paintpalette") {
                        ThemePicker()
                        Text("Your TV follows this too.").font(.caption).foregroundStyle(.secondary)
                    }
                    CockpitPanel("Wake-up", symbol: "alarm") {
                        NavigationLink {
                            WakeScheduleView()
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("Wake-up schedule").font(.headline).foregroundStyle(.primary)
                                    Text("Music on the TV, a different time each day.")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "chevron.right").font(.footnote.weight(.semibold))
                                    .foregroundStyle(.secondary)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                    NightVolumePanel()
                    SchwabPanel()
                    CockpitPanel("Study brief", symbol: "graduationcap") {
                        Toggle("Deliver on this phone", isOn: $settings.studyAlertsEnabled).tint(JarvisTheme.cyan)
                        if settings.studyAlertsEnabled {
                            if let brief = studyBrief {
                                Text(Self.scheduleSummary(brief)).font(.subheadline.weight(.medium))
                                if !brief.error.isEmpty {
                                    Text(brief.error).font(.caption).foregroundStyle(JarvisTheme.amber)
                                } else if let next = brief.briefs.first {
                                    Text("Next: \(next.fireDate.formatted(date: .abbreviated, time: .shortened))")
                                        .font(.caption.monospacedDigit()).foregroundStyle(JarvisTheme.cyan)
                                    Text(next.body).font(.caption).foregroundStyle(.secondary).lineLimit(4)
                                } else {
                                    Text(brief.enabled ? "Nothing on your calendar inside the warning window yet."
                                                       : "JARVIS has the brief switched off.")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            } else {
                                Text("Schedule arrives from JARVIS Core once the Hub loads.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Text("JARVIS writes this brief and owns its schedule. To change the hour, warning window, keywords, or tone, just tell him: “move my study brief to 7” or “be blunter in the morning brief.”")
                                .font(.footnote).foregroundStyle(.secondary)
                            Label(push.status, systemImage: "bell.badge")
                                .font(.caption).foregroundStyle(.secondary)
                            HStack {
                                Text(notificationStatus).font(.caption).foregroundStyle(.secondary)
                                Spacer()
                                if permissionUndetermined {
                                    Button("Allow notifications") { Task { await requestPermissionNow() } }
                                        .font(.caption.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
                                } else {
                                    Button("Permissions") {
                                        if let url = URL(string: UIApplication.openNotificationSettingsURLString) { openURL(url) }
                                    }.font(.caption.weight(.medium))
                                }
                            }
                        }
                    }
                    HStack(spacing: 10) {
                        JarvisCoreMark(size: 36, animated: false)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("JARVIS Companion").font(.caption.weight(.semibold))
                            Text("Version \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—")")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Image(systemName: "lock.shield").foregroundStyle(JarvisTheme.cyan)
                    }.padding(8)
                }
                .frame(maxWidth: 680).padding(20).frame(maxWidth: .infinity)
            }
            .scrollDismissesKeyboard(.interactively)
            .sheet(isPresented: $showingKnowledge) { KnowledgeView() }
            .background { JarvisBackdrop() }
            .navigationTitle("Settings").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if !embedded {
                    ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
                }
            }
            .task { await refreshPermission() }
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { Task { await refreshPermission() } }
            }
            .onChange(of: settings.connectionIdentity) { connectionResult = nil }
        }
    }

    private func testConnection() {
        testing = true
        connectionResult = nil
        let identity = settings.connectionIdentity
        Task {
            defer { testing = false }
            do {
                try await JarvisAPIClient.make(from: settings).checkHealth()
                guard identity == settings.connectionIdentity else { return }
                connectionSucceeded = true
                connectionResult = "Connected. JARVIS is within reach."
            } catch {
                guard identity == settings.connectionIdentity else { return }
                connectionSucceeded = false
                connectionResult = error.localizedDescription
            }
        }
    }

    private func refreshPermission() async {
        let status = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        permissionUndetermined = status == .notDetermined
        switch status {
        case .authorized, .provisional, .ephemeral: notificationStatus = "Notifications allowed"
        case .denied: notificationStatus = "Notifications disabled in iOS Settings"
        case .notDetermined: notificationStatus = "Notifications not yet allowed"
        @unknown default: notificationStatus = "Check iOS notification settings"
        }
    }

    private func requestPermissionNow() async {
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
        await refreshPermission()
        await push.refresh()
    }

    static func scheduleSummary(_ brief: StudyBrief) -> String {
        guard brief.enabled else { return "Switched off by JARVIS" }
        let days = brief.windowDays == 1 ? "1-day" : "\(brief.windowDays)-day"
        return "Daily at \(hourLabel(brief.hour)) · \(days) warning"
    }

    private static func hourLabel(_ hour: Int) -> String {
        let calendar = Calendar.current
        let date = calendar.date(bySettingHour: hour, minute: 0, second: 0, of: Date()) ?? Date()
        return date.formatted(date: .omitted, time: .shortened)
    }
}

/// Colour theme choice; the whole app re-themes as soon as one is picked,
/// and JARVIS Core shares it with the TV.
struct ThemePicker: View {
    @EnvironmentObject private var settings: AppSettings
    @AppStorage(JarvisPalette.storageKey) private var palette = JarvisPalette.arc.rawValue

    var body: some View {
        VStack(spacing: 0) {
            ForEach(JarvisPalette.pickable) { option in
                Button {
                    palette = option.rawValue
                    Task { await ThemeSync.push(option.rawValue, settings: settings) }
                } label: {
                    HStack(spacing: 12) {
                        HStack(spacing: -6) {
                            swatch(option.accent)
                            swatch(option.secondary)
                            if option.reactor.0 != option.accent.0 { swatch(option.reactor) }
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            Text(option.name).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                            Text(option.blurb).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if palette == option.rawValue {
                            Image(systemName: "checkmark").font(.footnote.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
                        }
                    }
                    .padding(.vertical, 8)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(palette == option.rawValue ? .isSelected : [])
            }
            if palette == JarvisPalette.experimenting.rawValue {
                ExperimentToggles()
            }
        }
        .sensoryFeedback(.selection, trigger: palette)
    }

    private func swatch(_ pair: (RGB, RGB)) -> some View {
        let c = pair.0
        return Circle().fill(Color(red: c.0, green: c.1, blue: c.2))
            .frame(width: 22, height: 22)
            .overlay(Circle().strokeBorder(.white.opacity(0.25)))
    }
}

/// Charles Schwab link status and the weekly log-in (Schwab ends every login
/// after 7 days). Read-only: JARVIS sees the portfolio, never trades.
struct SchwabPanel: View {
    @EnvironmentObject private var settings: AppSettings
    @State private var portfolio: Portfolio?
    @State private var loggingIn = false

    var body: some View {
        CockpitPanel("Charles Schwab", symbol: "chart.line.uptrend.xyaxis") {
            Text(status).font(.subheadline.weight(.medium))
            Text("Read-only. Schwab ends every login after 7 days; JARVIS reminds you the day before.")
                .font(.caption).foregroundStyle(.secondary)
            if portfolio?.configured == true {
                Button { loggingIn = true } label: {
                    HStack {
                        Text(portfolio?.linked == true ? "Log in again" : "Log in to Schwab")
                            .font(.subheadline.weight(.semibold))
                        Spacer()
                        Image(systemName: "arrow.up.right")
                    }
                    .padding(14).foregroundStyle(JarvisTheme.background)
                    .background(JarvisTheme.cyan, in: .rect(cornerRadius: 12))
                }
            }
        }
        .task { await load() }
        .sheet(isPresented: $loggingIn) {
            SchwabLoginView { Task { await load() } }
        }
    }

    private var status: String {
        guard let portfolio else { return "Checking…" }
        guard portfolio.configured else { return "Not set up on JARVIS Core yet." }
        guard portfolio.linked, let expires = portfolio.loginExpiresAt else { return "Not logged in." }
        let date = Date(timeIntervalSince1970: expires)
        return "Linked until \(date.formatted(.dateTime.weekday(.wide).hour().minute()))"
    }

    private func load() async {
        guard settings.isConfigured else { return }
        portfolio = try? await JarvisAPIClient.make(from: settings).portfolio()
    }
}

/// Night volume: the room speaker's quiet-hours level (0-10), editable at
/// any hour. Core applies it at once only during quiet hours. In Settings
/// (a panel) and the iPad Room's Menu › Room settings (`inForm`, a Form
/// section, so it's reachable without leaving full screen). Hidden on an
/// older Core that doesn't report `night_level`.
struct NightVolumePanel: View {
    var inForm = false
    @EnvironmentObject private var settings: AppSettings
    @State private var level: Int?
    @State private var failure: String?
    @State private var pending: Task<Void, Never>?

    private var caption: String { failure ?? "JARVIS's speaker level during quiet hours (10 pm–7 am)." }

    var body: some View {
        Group {
            if let shown = level {
                if inForm {
                    Section {
                        stepper(shown)
                    } header: {
                        Text("JARVIS speaker")
                    } footer: {
                        Text(caption).foregroundStyle(failure == nil ? Color.secondary : JarvisTheme.amber)
                    }
                } else {
                    CockpitPanel("Room speaker", symbol: "moon.zzz") {
                        stepper(shown)
                        Text(caption)
                            .font(.caption).foregroundStyle(failure == nil ? Color.secondary : JarvisTheme.amber)
                    }
                }
            }
        }
        .task { await load() }
    }

    private func stepper(_ shown: Int) -> some View {
        Stepper(value: Binding(get: { shown }, set: { change($0) }), in: 0...10) {
            HStack {
                Label("Night volume", systemImage: "moon.zzz").font(inForm ? .body : .headline)
                Spacer()
                Text("\(shown)").font(.headline).monospacedDigit().foregroundStyle(JarvisTheme.cyan)
            }
        }
        .accessibilityValue("\(shown) of 10")
    }

    private func load() async {
        guard settings.isConfigured,
              let volume = try? await JarvisAPIClient.make(from: settings).roomVolume() else { return }
        level = volume.nightLevel
    }

    private func change(_ value: Int) {
        level = value
        pending?.cancel()
        pending = Task {
            try? await Task.sleep(for: .milliseconds(400))  // one request per burst of taps
            guard !Task.isCancelled else { return }
            do {
                let volume = try await JarvisAPIClient.make(from: settings).setRoomNightVolume(value)
                if let saved = volume.nightLevel { level = saved }
                failure = nil
            } catch {
                failure = "Couldn't save the night volume"
            }
        }
    }
}
