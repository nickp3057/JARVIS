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
                        JarvisCoreMark(size: 36)
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
