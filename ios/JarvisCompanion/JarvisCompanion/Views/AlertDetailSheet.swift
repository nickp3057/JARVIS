import SwiftUI
import UIKit

/// One alert, in full: what happened (every word of it, selectable), what
/// JARVIS is doing about it, and what Nick can do: Fix it, Ask JARVIS (sent
/// straight to Chat), Copy, Dismiss. Opened from a Needs attention row.
struct AlertDetailSheet: View {
    @ObservedObject var model: HubViewModel
    let alertID: Int
    /// Sends a message to JARVIS now and switches to Chat.
    var onAsk: ((String) -> Void)?
    @Environment(\.dismiss) private var dismiss
    @State private var outcome: String?
    @State private var asking = false
    @State private var copied = false

    /// The live copy, so autopilot's progress updates while the sheet is open.
    private var alert: JarvisAlert? { model.alerts.value?.first { $0.id == alertID } }

    var body: some View {
        NavigationStack {
            ScrollView {
                if let alert {
                    VStack(alignment: .leading, spacing: 18) {
                        header(alert)
                        if !alert.message.isEmpty {
                            Text(alert.message)
                                .font(.body)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(14)
                                .background(JarvisTheme.cyan.opacity(0.06), in: .rect(cornerRadius: 14))
                        }
                        if let repair = alert.repair { repairPanel(repair) }
                        if let outcome {
                            Label(outcome, systemImage: "info.circle")
                                .font(.footnote).foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                        actions(alert)
                    }
                    .padding(20)
                } else {
                    VStack(spacing: 10) {
                        Image(systemName: "checkmark.circle").font(.largeTitle).foregroundStyle(JarvisTheme.success)
                        Text("This one's cleared.").font(.headline)
                        Text("It was fixed or dismissed.").font(.subheadline).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.top, 80)
                }
            }
            .navigationTitle("Alert")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private func header(_ alert: JarvisAlert) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Circle().fill(Self.tint(alert)).frame(width: 9, height: 9)
                Text(alert.severity == "critical" ? "CRITICAL" : "WARNING")
                    .font(.caption.weight(.semibold)).foregroundStyle(Self.tint(alert))
                Spacer()
                if let repeats = alert.repeats, repeats > 1 {
                    Text("\(repeats)× today").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            Text(alert.title)
                .font(.title3.weight(.semibold))
                .textSelection(.enabled)
            Text("\(alert.date, style: .relative) ago · \(alert.date.formatted(date: .omitted, time: .shortened)) · \(alert.source)")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func repairPanel(_ repair: AlertRepair) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                if repair.isWorking {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: repair.succeeded ? "checkmark.circle.fill"
                          : repair.failed ? "exclamationmark.circle.fill" : "clock")
                        .foregroundStyle(repair.succeeded ? JarvisTheme.success
                                         : repair.failed ? JarvisTheme.amber : .secondary)
                }
                Text(Self.repairHeadline(repair)).font(.subheadline.weight(.semibold))
            }
            Text(repair.label).font(.caption).foregroundStyle(.secondary)
            if !repair.result.isEmpty {
                Text(repair.result).font(.callout).textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(JarvisTheme.cyan.opacity(0.06), in: .rect(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(JarvisTheme.border))
    }

    private func actions(_ alert: JarvisAlert) -> some View {
        let working = alert.repair?.isWorking == true || model.fixingAlertIDs.contains(alert.id)
        return VStack(spacing: 10) {
            if alert.fixable == true {
                Button {
                    Task {
                        if let result = await model.fixAlert(alert) {
                            outcome = result.started ? nil : result.message
                        }
                    }
                } label: {
                    Label(working ? "Fixing…" : (alert.repair?.failed == true ? "Try the fix again" : "Fix it"),
                          systemImage: "wrench.and.screwdriver")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(working)
            }
            if onAsk != nil {
                Button {
                    asking = true
                    Task {
                        let prompt = await model.incidentPrompt(for: alert) ?? HubViewModel.fallbackPrompt(for: alert)
                        asking = false
                        dismiss()
                        onAsk?(prompt)
                    }
                } label: {
                    Label(asking ? "Gathering logs…" : "Ask JARVIS about it", systemImage: "bubble.left.and.text.bubble.right")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(asking)
            }
            HStack(spacing: 10) {
                Button {
                    UIPasteboard.general.string = Self.copyText(alert)
                    copied = true
                } label: {
                    Label(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc")
                        .frame(maxWidth: .infinity)
                }
                Button(role: .destructive) {
                    model.dismissAlert(alert)
                    dismiss()
                } label: {
                    Label("Dismiss", systemImage: "xmark").frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.bordered)
            .sensoryFeedback(.success, trigger: copied)
        }
        .controlSize(.large)
    }

    static func tint(_ alert: JarvisAlert) -> Color {
        alert.severity == "critical" ? JarvisTheme.critical : JarvisTheme.amber
    }

    static func repairHeadline(_ repair: AlertRepair) -> String {
        let who = repair.auto ? "JARVIS" : "Your fix"
        if repair.isWorking { return repair.auto ? "JARVIS is fixing it" : "Fixing it…" }
        if repair.succeeded { return repair.auto ? "JARVIS fixed it" : "Fixed" }
        if repair.failed { return "\(who) couldn't fix it" }
        return "A fix is waiting for approval"
    }

    /// Short status for the Needs attention row, or nil when nothing's running.
    static func repairBadge(_ repair: AlertRepair?) -> (text: String, color: Color)? {
        guard let repair else { return nil }
        if repair.isWorking { return ("FIXING", JarvisTheme.cyan) }
        if repair.succeeded { return ("FIXED", JarvisTheme.success) }
        if repair.failed { return ("FIX FAILED", JarvisTheme.amber) }
        return nil
    }

    static func copyText(_ alert: JarvisAlert) -> String {
        var parts = [alert.title]
        if !alert.message.isEmpty { parts.append(alert.message) }
        if let repair = alert.repair, !repair.result.isEmpty { parts.append(repair.result) }
        return parts.joined(separator: " — ")
    }
}
