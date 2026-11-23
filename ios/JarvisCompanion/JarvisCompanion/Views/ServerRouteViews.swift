import SwiftUI
import UniformTypeIdentifiers

/// Settings > Server: the home and away addresses, the optional Tailscale
/// fallback, and how the app picks between them (`ServerRoute`).
struct ServerAddressFields: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject private var route = ServerRoute.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            field("Home address", text: $settings.homeURLString, prompt: AppSettings.defaultHomeURL)
            field("Away address", text: $settings.awayURLString, prompt: AppSettings.defaultAwayURL)
            field("Tailscale fallback (optional)", text: $settings.fallbackURLString, prompt: "http://100.x.y.z:8000")
            Picker("Route", selection: $settings.routeMode) {
                ForEach(ServerRoute.Mode.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            HStack(spacing: 6) {
                if route.checking { ProgressView().controlSize(.mini) }
                Text(status).font(.caption).foregroundStyle(.secondary)
            }
            Text("Automatic uses the home address on your Wi-Fi and jarvis.nickolaspearson.com everywhere else, so Tailscale isn't needed on this device.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var status: String {
        settings.routeMode == .auto ? "Connected \(route.current.label)" : "Always \(route.current.label)"
    }

    private func field(_ label: String, text: Binding<String>, prompt: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            TextField(prompt, text: text)
                .keyboardType(.URL).textContentType(.URL)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .padding(13).background(.black.opacity(0.2), in: .rect(cornerRadius: 12))
                .accessibilityLabel(label)
        }
    }
}

/// Settings > Server > Away access: import this device's certificate (the
/// `.p12` from `ops/remote/make-device-cert.sh`, AirDropped or in Files).
struct AwayAccessPanel: View {
    @State private var summary = DeviceCertificate.shared.summary
    @State private var picking = false
    @State private var pendingFile: Data?
    @State private var passphrase = ""
    @State private var message: String?
    @State private var failed = false

    var body: some View {
        CockpitPanel("Away access", symbol: "lock.shield") {
            if let summary {
                Label("Device certificate: \(summary.name)", systemImage: "checkmark.seal.fill")
                    .font(.subheadline).foregroundStyle(JarvisTheme.success)
            } else {
                Label("Not set up", systemImage: "seal").font(.subheadline).foregroundStyle(.secondary)
            }
            Text("Away from home, jarvis.nickolaspearson.com only lets in devices holding a certificate. Import the .p12 made for this device on the Mac.")
                .font(.caption).foregroundStyle(.secondary)
            if pendingFile != nil {
                SecureField("Certificate passphrase", text: $passphrase)
                    .textContentType(.password).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .padding(13).background(.black.opacity(0.2), in: .rect(cornerRadius: 12))
                    .onSubmit(install)
                HStack {
                    Button("Cancel") { pendingFile = nil; passphrase = "" }
                    Spacer()
                    Button("Import", action: install).fontWeight(.semibold).disabled(passphrase.isEmpty)
                }
            } else {
                HStack {
                    Button(summary == nil ? "Import device certificate" : "Replace certificate") { picking = true }
                        .fontWeight(.semibold).foregroundStyle(JarvisTheme.cyan)
                    Spacer()
                    if summary != nil {
                        Button("Remove", role: .destructive) {
                            DeviceCertificate.shared.remove()
                            summary = nil
                            message = nil
                            ServerRoute.shared.reevaluate()
                        }
                    }
                }
            }
            if let message {
                Label(message, systemImage: failed ? "exclamationmark.triangle" : "checkmark.circle.fill")
                    .font(.footnote).foregroundStyle(failed ? JarvisTheme.amber : JarvisTheme.success)
            }
        }
        .fileImporter(isPresented: $picking, allowedContentTypes: [.pkcs12, .data]) { result in
            switch result {
            case .success(let url):
                let scoped = url.startAccessingSecurityScopedResource()
                defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                if let data = try? Data(contentsOf: url) {
                    pendingFile = data
                    message = nil
                } else {
                    failed = true
                    message = "Couldn't read that file."
                }
            case .failure(let error):
                failed = true
                message = error.localizedDescription
            }
        }
    }

    private func install() {
        guard let data = pendingFile else { return }
        do {
            summary = try DeviceCertificate.shared.install(data, passphrase: passphrase)
            failed = false
            message = "Imported. Away access is ready on this device."
            pendingFile = nil
            ServerRoute.shared.reevaluate()
        } catch {
            failed = true
            message = error.localizedDescription
        }
        passphrase = ""
    }
}
