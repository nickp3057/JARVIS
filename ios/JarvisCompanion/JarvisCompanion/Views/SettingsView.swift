import SwiftUI
import UserNotifications

struct SettingsView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    var embedded = false
    @State private var revealToken = false
    @State private var testing = false
    @State private var connectionResult: String?
    @State private var connectionSucceeded = false
    @State private var notificationStatus = "Checking permission…"

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
                        Toggle("Daily assessment reminders", isOn: $settings.studyAlertsEnabled).tint(JarvisTheme.cyan)
                        if settings.studyAlertsEnabled {
                            Picker("Delivery time", selection: $settings.studyAlertHour) {
                                ForEach(0..<24, id: \.self) { hour in
                                    Text(Self.hourLabel(hour)).tag(hour)
                                }
                            }
                            .tint(JarvisTheme.cyan)
                            Text("One daily brief with the days left until your quizzes and exams. Refresh the Hub to pick up calendar changes.")
                                .font(.footnote).foregroundStyle(.secondary)
                            HStack {
                                Text(notificationStatus).font(.caption).foregroundStyle(.secondary)
                                Spacer()
                                Button("Permissions") {
                                    if let url = URL(string: UIApplication.openNotificationSettingsURLString) { openURL(url) }
                                }.font(.caption.weight(.medium))
                            }
                        }
                    }
                    HStack(spacing: 10) {
                        JarvisCoreMark(size: 36)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("JARVIS COMPANION").font(.caption2.monospaced().weight(.semibold)).tracking(1)
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
        switch await UNUserNotificationCenter.current().notificationSettings().authorizationStatus {
        case .authorized, .provisional, .ephemeral: notificationStatus = "Notifications allowed"
        case .denied: notificationStatus = "Notifications disabled in iOS Settings"
        case .notDetermined: notificationStatus = "Permission requested when assessments sync"
        @unknown default: notificationStatus = "Check iOS notification settings"
        }
    }

    private static func hourLabel(_ hour: Int) -> String {
        let calendar = Calendar.current
        let date = calendar.date(bySettingHour: hour, minute: 0, second: 0, of: Date()) ?? Date()
        return date.formatted(date: .omitted, time: .shortened)
    }
}
