import SwiftUI

/// Core's alert bus, last 24 hours (the phone's Needs attention card).
struct AlertsView: View {
    @ObservedObject var model: AlertsModel
    @State private var confirmingClear = false

    var body: some View {
        Group {
            if model.alerts.isEmpty {
                ContentUnavailableView {
                    Label("All clear", systemImage: "checkmark.seal")
                } description: {
                    Text(model.error ?? "Nothing needs your attention.")
                }
            } else {
                List {
                    if let error = model.error { ErrorLine(text: error) }
                    ForEach(model.alerts) { alert in row(alert) }
                }
                .listStyle(.inset)
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { Task { await model.recheck() } } label: {
                    if model.refreshing {
                        ProgressView().controlSize(.small)
                    } else {
                        Label("Re-check", systemImage: "arrow.clockwise")
                    }
                }
                .keyboardShortcut("r")
                .help("Re-check each alert against the live system (⌘R)")
                .disabled(model.refreshing)
                Button { confirmingClear = true } label: { Label("Clear", systemImage: "checkmark.circle") }
                    .help("Clear the list")
                    .disabled(model.alerts.isEmpty)
            }
        }
        .confirmationDialog("Clear all alerts?", isPresented: $confirmingClear) {
            Button("Clear") { Task { await model.clear() } }
        } message: {
            Text("Anything raised later still shows.")
        }
        .task { await model.load() }
    }

    private func row(_ alert: JarvisAlert) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon(alert.severity))
                .font(.system(size: 17))
                .foregroundStyle(color(alert.severity))
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(alert.title).font(.headline)
                    if let repeats = alert.repeats, repeats > 1 {
                        Text("×\(repeats)").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text(Date(timeIntervalSince1970: alert.createdAt), format: .relative(presentation: .named))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text(alert.message).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
        .padding(.vertical, 6)
    }

    private func icon(_ severity: String) -> String {
        switch severity {
        case "critical": return "exclamationmark.octagon"
        case "warning": return "exclamationmark.triangle"
        default: return "info.circle"
        }
    }

    private func color(_ severity: String) -> Color {
        switch severity {
        case "critical": return .red
        case "warning": return .orange
        default: return JarvisTheme.accent
        }
    }
}
