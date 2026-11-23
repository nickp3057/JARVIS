import AppKit
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var settings: MacSettings
    @State private var address = ""
    @State private var awayAddress = ""
    @State private var passphrase = ""
    @State private var certificateStatus: String?
    @State private var token = ""
    @State private var status: String?

    var body: some View {
        Form {
            Section {
                TextField("Home address", text: $address, prompt: Text("10.0.0.73"))
                TextField("Away address", text: $awayAddress, prompt: Text(MacSettings.defaultAwayAddress))
                SecureField("API token", text: $token, prompt: Text("JARVIS_API_TOKEN"))
            } footer: {
                Text("Home is used whenever it answers, otherwise away (now: \(settings.routeLabel)). The token is kept in a file only your account can read.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Away access") {
                Text(settings.certificateName.map { "Device certificate: \($0)" } ?? "Not set up")
                SecureField("Certificate passphrase", text: $passphrase)
                HStack {
                    Button("Import .p12…") { importCertificate() }.disabled(passphrase.isEmpty)
                    if settings.certificateName != nil {
                        Button("Remove", role: .destructive) { settings.removeCertificate(); certificateStatus = nil }
                    }
                }
                if let certificateStatus { Text(certificateStatus).font(.callout).foregroundStyle(.secondary) }
            }
            HStack {
                if let status { Text(status).font(.callout).foregroundStyle(.secondary) }
                Spacer()
                Button("Save") { save() }.keyboardShortcut(.defaultAction)
            }
        }
        .formStyle(.grouped)
        .frame(width: 460)
        .onAppear {
            address = settings.address
            awayAddress = settings.awayAddress
            token = settings.token
        }
    }

    private func save() {
        settings.address = address
        settings.awayAddress = awayAddress
        settings.token = token
        guard let url = settings.baseURL else {
            status = "That address doesn't look right."
            return
        }
        status = "Checking \(url.host() ?? "")…"
        Task {
            do {
                _ = try await JarvisAPI.make(settings).sessions()
                status = "Connected."
            } catch {
                status = error.localizedDescription
            }
        }
    }

    private func importCertificate() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pkcs12]
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url, let data = try? Data(contentsOf: url) else { return }
        do {
            try settings.importCertificate(data, passphrase: passphrase)
            certificateStatus = "Imported. Away access is ready on this Mac."
            passphrase = ""
        } catch {
            certificateStatus = error.localizedDescription
        }
    }
}
