import SwiftUI

/// "Why is this stuck?" for one queued title: the server's media doctor walks
/// arr -> indexers -> qBittorrent -> import -> Plex and names the stalled
/// stage. Read-only; fixes are offered in chat, where they need a "yes".
struct MediaDiagnosisView: View {
    let title: String
    let kind: String
    let makeClient: () throws -> JarvisAPIClient
    @Environment(\.dismiss) private var dismiss
    @State private var report: MediaDiagnosis?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                if let report {
                    Section {
                        Label(report.summary, systemImage: report.stalled ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                            .foregroundStyle(report.stalled ? JarvisTheme.amber : JarvisTheme.cyan)
                    }
                    Section("Checked") {
                        ForEach(Array(report.steps.enumerated()), id: \.offset) { _, step in
                            HStack(alignment: .top, spacing: 10) {
                                Image(systemName: symbol(step.status))
                                    .foregroundStyle(color(step.status))
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(step.stage.capitalized).font(.caption.weight(.semibold))
                                    Text(step.detail).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                    if report.stalled {
                        Section {
                            Text("Ask JARVIS to fix it in chat; fixes need your yes.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                } else if let error {
                    Label(error, systemImage: "wifi.exclamationmark").foregroundStyle(JarvisTheme.amber)
                } else {
                    HStack { ProgressView(); Text("Tracing \(title)…").foregroundStyle(.secondary) }
                }
            }
            .scrollContentBackground(.hidden)
            .background { JarvisBackdrop() }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .task {
                do {
                    report = try await makeClient().diagnoseMedia(title: title, kind: kind)
                } catch {
                    self.error = error.localizedDescription
                }
            }
        }
    }

    private func symbol(_ status: String) -> String {
        switch status {
        case "ok": return "checkmark.circle"
        case "fail": return "xmark.octagon"
        case "pending": return "clock"
        default: return "minus.circle"
        }
    }

    private func color(_ status: String) -> Color {
        switch status {
        case "ok": return JarvisTheme.cyan
        case "fail": return JarvisTheme.critical
        case "pending": return JarvisTheme.amber
        default: return .secondary
        }
    }
}
