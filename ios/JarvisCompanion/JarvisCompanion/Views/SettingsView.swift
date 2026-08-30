import SwiftUI

/// Server address and API token. Deliberately spare — these two values are the
/// app's entire configuration.
struct SettingsView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @State private var revealToken = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://100.x.y.z:8000", text: $settings.baseURLString)
                        .textContentType(.URL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("Server")
                } footer: {
                    Text("The base URL of JARVIS Core, without the /chat path. A bare host is fine — http:// is assumed.")
                }

                Section {
                    HStack {
                        Group {
                            if revealToken {
                                TextField("Token", text: $settings.apiToken)
                            } else {
                                SecureField("Token", text: $settings.apiToken)
                            }
                        }
                        .textContentType(.password)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                        Button {
                            revealToken.toggle()
                        } label: {
                            Image(systemName: revealToken ? "eye.slash" : "eye")
                        }
                        .buttonStyle(.borderless)
                        .accessibilityLabel(revealToken ? "Hide token" : "Show token")
                    }
                } header: {
                    Text("API token")
                } footer: {
                    Text("Sent as a bearer token on every request. Stored in UserDefaults for now, not the Keychain.")
                }

                Section {
                    LabeledContent("Status") {
                        Text(settings.isConfigured ? "Ready" : "Incomplete")
                            .foregroundStyle(settings.isConfigured ? .green : .secondary)
                    }
                    if settings.isConfigured, let url = settings.resolvedBaseURL {
                        LabeledContent("Endpoint") {
                            Text(url.appending(path: "chat").absoluteString)
                                .font(.footnote.monospaced())
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                                .truncationMode(.head)
                        }
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
