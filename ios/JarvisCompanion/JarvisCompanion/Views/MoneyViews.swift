import SwiftUI

/// Core's money advisor (`brain/finance_advisor.py`): `GET /api/money`.
struct MoneyStatus: Decodable {
    let enabled: Bool
    let settings: MoneySettings
    let log: [MoneyNotice]
}

struct MoneySettings: Codable, Equatable {
    var weeklyBudget: Double
    var bigPurchase: Double
    var quietStart: String
    var quietEnd: String
    var dryRun: Bool

    enum CodingKeys: String, CodingKey {
        case weeklyBudget = "weekly_budget", bigPurchase = "big_purchase"
        case quietStart = "quiet_start", quietEnd = "quiet_end", dryRun = "dry_run"
    }
}

struct MoneyNotice: Decodable, Identifiable {
    let at: String
    let kind: String
    let title: String
    let body: String
    let sent: Bool
    var id: String { at + kind }
}

/// Settings > Money: the weekly budget, the heads-up line, quiet hours (no
/// money notices then) and dry run, kept on Core so every device agrees.
struct MoneyPanel: View {
    @EnvironmentObject private var settings: AppSettings
    @State private var status: MoneyStatus?
    @State private var draft: MoneySettings?
    @State private var error: String?
    @State private var saving = false

    var body: some View {
        CockpitPanel("Money advisor", symbol: "dollarsign.circle") {
            if let draft = Binding($draft) {
                if status?.enabled == false {
                    Text("Mooney isn't linked to Core yet, so nothing is watched.").font(.caption).foregroundStyle(JarvisTheme.amber)
                }
                amountRow("Weekly budget", value: draft.weeklyBudget, note: "Monday to Sunday, MACU debit")
                amountRow("Heads-up over", value: draft.bigPurchase, note: "Any card")
                timeRow("Quiet from", text: draft.quietStart)
                timeRow("Quiet until", text: draft.quietEnd)
                Toggle("Dry run (log only, no notifications)", isOn: draft.dryRun).tint(JarvisTheme.cyan)
                Button(action: save) {
                    HStack {
                        if saving { ProgressView().tint(JarvisTheme.background) }
                        Text("Save").font(.subheadline.weight(.semibold))
                        Spacer()
                    }
                    .padding(14).foregroundStyle(JarvisTheme.background)
                    .background(JarvisTheme.cyan, in: .rect(cornerRadius: 12))
                }
                .disabled(saving || draft.wrappedValue == status?.settings)
            } else if error == nil {
                ProgressView()
            }
            if let error { Label(error, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(JarvisTheme.amber) }
        }
        .task { await load() }
        if let log = status?.log, !log.isEmpty {
            CockpitPanel("Recent notices", symbol: "text.bubble") {
                ForEach(log.reversed()) { notice in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text(notice.title).font(.subheadline.weight(.semibold))
                            Spacer()
                            if !notice.sent { Text("dry run").font(.caption2).foregroundStyle(.secondary) }
                        }
                        Text(notice.body).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func amountRow(_ label: String, value: Binding<Double>, note: String) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                Text(note).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            TextField("0", value: value, format: .currency(code: "USD").precision(.fractionLength(0)))
                .keyboardType(.decimalPad).multilineTextAlignment(.trailing).frame(maxWidth: 110)
        }
    }

    /// "22:00" on Core, a time picker here.
    private func timeRow(_ label: String, text: Binding<String>) -> some View {
        let date = Binding<Date>(
            get: {
                let parts = text.wrappedValue.split(separator: ":").compactMap { Int($0) }
                return Calendar.current.date(bySettingHour: parts.first ?? 22, minute: parts.count > 1 ? parts[1] : 0,
                                             second: 0, of: .now) ?? .now
            },
            set: { new in
                let c = Calendar.current.dateComponents([.hour, .minute], from: new)
                text.wrappedValue = String(format: "%02d:%02d", c.hour ?? 0, c.minute ?? 0)
            })
        return DatePicker(label, selection: date, displayedComponents: .hourAndMinute)
    }

    private func load() async {
        do {
            let fresh = try await JarvisAPIClient.make(from: settings).moneyStatus()
            status = fresh
            draft = fresh.settings
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func save() {
        guard let draft else { return }
        saving = true
        Task {
            defer { saving = false }
            do {
                let fresh = try await JarvisAPIClient.make(from: settings).saveMoneySettings(draft)
                status = fresh
                self.draft = fresh.settings
                error = nil
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
