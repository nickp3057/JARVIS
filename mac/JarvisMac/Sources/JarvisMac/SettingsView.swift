import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var settings: MacSettings
    @State private var address = ""
    @State private var token = ""
    @State private var status: String?

    var body: some View {
        Form {
            Section {
                TextField("Core address", text: $address, prompt: Text("10.0.0.73 or a Tailscale address"))
                SecureField("API token", text: $token, prompt: Text("JARVIS_API_TOKEN"))
            } footer: {
                Text("The token is kept in a file only your account can read.")
                    .font(.caption).foregroundStyle(.secondary)
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
            token = settings.token
        }
    }

    private func save() {
        settings.address = address
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
}
