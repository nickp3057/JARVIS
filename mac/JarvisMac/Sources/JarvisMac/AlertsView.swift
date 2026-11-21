import SwiftUI

/// Core's alert bus, last 24 hours (the phone's Needs attention card).
struct AlertsView: View {
    @ObservedObject var model: AlertsModel
    @State private var confirmingClear = false

    var body: some View {
        Group {
            if model.alerts.isEmpty {
                ContentUnavailableView {
                    Label {
                        Text("All clear")
                    } icon: {
                        ReactorEmblem(size: 72)
                    }
                } description: {
                    Text(model.error ?? "Nothing needs your attention.")
                }
            } else {
                List {
                    if let error = model.error { ErrorLine(text: error) }
                    ForEach(model.alerts) { alert in
                        row(alert)
                            .padding(10)
                            .background(JarvisTheme.panel, in: .rect(cornerRadius: 12))
                            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(JarvisTheme.border))
                            .listRowSeparator(.hidden)
                            .listRowBackground(Color.clear)
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .padding(.horizontal, 12)
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                if let autopilot = model.autopilot {
                    Toggle(isOn: Binding(get: { autopilot.enabled },
                                         set: { on in Task { await model.setAutopilot(on) } })) {
                        Label("Autopilot", systemImage: "wand.and.sparkles")
                    }
                    .toggleStyle(.button)
                    .help(autopilot.enabled
                          ? "Autopilot on: JARVIS restarts the arr stack, re-tests indexers and retries stuck downloads himself. Never Plex."
                          : "Autopilot off: every fix waits for you.")
                }
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
        .task {
            await model.load()
            await model.loadAutopilot()
        }
        .task(id: model.anyRepairRunning) {
            // A fix is running: watch it until it reports.
            while model.anyRepairRunning, !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                await model.load()
            }
        }
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
                if let repair = alert.repair { repairLine(repair) }
                if let note = model.fixNotes[alert.id] {
                    Text(note).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }
                if alert.severity != "info" {
                    HStack(spacing: 8) {
                        if alert.fixable == true {
                            Button {
                                Task { await model.fix(alert) }
                            } label: {
                                Label(alert.repair?.failed == true ? "Try again" : "Fix it", systemImage: "wrench.and.screwdriver")
                            }
                            .disabled(model.fixing.contains(alert.id) || alert.repair?.isWorking == true)
                        }
                        Button { copy(alert) } label: { Label("Copy", systemImage: "doc.on.doc") }
                        Button { Task { await model.dismiss(alert) } } label: { Label("Dismiss", systemImage: "xmark") }
                    }
                    .controlSize(.small)
                    .padding(.top, 2)
                }
            }
        }
        .padding(.vertical, 6)
        .contextMenu {
            Button("Copy") { copy(alert) }
            if alert.fixable == true { Button("Fix it") { Task { await model.fix(alert) } } }
            Button("Dismiss") { Task { await model.dismiss(alert) } }
        }
    }

    private func repairLine(_ repair: AlertRepair) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            if repair.isWorking {
                ProgressView().controlSize(.small)
            } else {
                Image(systemName: repair.succeeded ? "checkmark.circle.fill" : repair.failed ? "exclamationmark.circle.fill" : "clock")
                    .foregroundStyle(repair.succeeded ? Color.green : repair.failed ? Color.orange : Color.secondary)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(headline(repair)).font(.callout.weight(.semibold))
                if !repair.result.isEmpty {
                    Text(repair.result).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }
            }
        }
        .padding(.top, 2)
    }

    private func headline(_ repair: AlertRepair) -> String {
        if repair.isWorking { return repair.auto ? "JARVIS is fixing it" : "Fixing it…" }
        if repair.succeeded { return repair.auto ? "JARVIS fixed it" : "Fixed" }
        if repair.failed { return repair.auto ? "JARVIS couldn't fix it" : "The fix didn't work" }
        return "A fix is waiting for approval"
    }

    private func copy(_ alert: JarvisAlert) {
        var parts = [alert.title, alert.message]
        if let result = alert.repair?.result, !result.isEmpty { parts.append(result) }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(parts.filter { !$0.isEmpty }.joined(separator: " — "), forType: .string)
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
