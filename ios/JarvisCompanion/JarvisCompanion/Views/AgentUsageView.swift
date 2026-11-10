import SwiftUI

/// `GET /api/agents/usage?days=N`: estimated model spend per agent.
struct AgentUsage: Decodable {
    struct Agent: Decodable, Identifiable {
        let id: String
        let name: String
        let symbol: String
        let usd: Double
        let calls: Int
        let inputTokens: Int
        let outputTokens: Int
        let models: [String]

        enum CodingKeys: String, CodingKey {
            case id, name, symbol, usd, calls, models
            case inputTokens = "input_tokens"
            case outputTokens = "output_tokens"
        }
    }
    struct Day: Decodable, Identifiable {
        let day: String
        let usd: Double
        var id: String { day }
    }

    let totalUSD: Double
    let trackingSince: Double?
    let agents: [Agent]
    let daily: [Day]

    enum CodingKeys: String, CodingKey {
        case agents, daily
        case totalUSD = "total_usd"
        case trackingSince = "tracking_since"
    }
}

/// What each agent costs: pick a range, see the total and who spent it.
struct AgentUsageView: View {
    enum Range: Int, CaseIterable, Identifiable {
        case today = 1, week = 7, month = 30, quarter = 90, all = 0
        var id: Int { rawValue }
        var label: String {
            switch self {
            case .today: return "Today"
            case .week: return "Week"
            case .month: return "30 days"
            case .quarter: return "90 days"
            case .all: return "All"
            }
        }
        var phrase: String {
            switch self {
            case .today: return "in the last 24 hours"
            case .week: return "in the last 7 days"
            case .month: return "in the last 30 days"
            case .quarter: return "in the last 90 days"
            case .all: return "since tracking began"
            }
        }
    }

    @EnvironmentObject private var settings: AppSettings
    @AppStorage("agents.usageRange") private var rangeValue = Range.week.rawValue
    @State private var usage: AgentUsage?
    @State private var error: String?
    @State private var loading = false

    private var range: Range { Range(rawValue: rangeValue) ?? .week }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Picker("Range", selection: $rangeValue) {
                    ForEach(Range.allCases) { Text($0.label).tag($0.rawValue) }
                }
                .pickerStyle(.segmented)

                VStack(alignment: .leading, spacing: 4) {
                    Text(usage.map { Self.money($0.totalUSD) } ?? "–")
                        .font(.system(size: 44, weight: .semibold, design: .rounded).monospacedDigit())
                        .contentTransition(.numericText())
                    Text("Estimated \(range.phrase)")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                .accessibilityElement(children: .combine)

                if let error {
                    Label(error, systemImage: "wifi.exclamationmark")
                        .font(.footnote).foregroundStyle(JarvisTheme.amber)
                }

                if let usage {
                    VStack(spacing: 0) {
                        ForEach(Array(usage.agents.enumerated()), id: \.element.id) { index, agent in
                            row(agent, share: usage.totalUSD > 0 ? agent.usd / usage.totalUSD : 0)
                            if index < usage.agents.count - 1 { Divider().padding(.leading, 56) }
                        }
                    }
                    .jarvisSurface(in: .rect(cornerRadius: 20))
                    .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(JarvisTheme.border))

                    Text(footnote(usage))
                        .font(.footnote).foregroundStyle(.secondary)
                        .padding(.horizontal, 4)
                } else if loading {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .padding(20)
            .frame(maxWidth: 680)
            .frame(maxWidth: .infinity)
        }
        .background { JarvisBackdrop() }
        .navigationTitle("Usage")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: rangeValue) { await load() }
        .refreshable { await load() }
        .animation(.snappy, value: usage?.totalUSD)
    }

    private func row(_ agent: AgentUsage.Agent, share: Double) -> some View {
        HStack(spacing: 12) {
            Image(systemName: agent.symbol)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(agent.usd > 0 ? JarvisTheme.cyan : .secondary)
                .frame(width: 32, height: 32)
                .background(JarvisTheme.cyan.opacity(agent.usd > 0 ? 0.13 : 0.06), in: .circle)
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text(agent.name).font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(Self.money(agent.usd))
                        .font(.subheadline.weight(.semibold).monospacedDigit())
                        .foregroundStyle(agent.usd > 0 ? .primary : .secondary)
                }
                GeometryReader { geo in
                    Capsule().fill(JarvisTheme.cyan.opacity(0.12))
                        .overlay(alignment: .leading) {
                            Capsule().fill(JarvisTheme.cyan)
                                .frame(width: max(agent.usd > 0 ? 4 : 0, geo.size.width * share))
                        }
                }
                .frame(height: 5)
                .accessibilityHidden(true)
                Text(detail(agent)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
    }

    private func detail(_ agent: AgentUsage.Agent) -> String {
        guard agent.calls > 0 else { return "Nothing in this range" }
        let tokens = (agent.inputTokens + agent.outputTokens).formatted(.number.notation(.compactName))
        let models = agent.models.map(Self.shortModel).joined(separator: ", ")
        return "\(agent.calls.formatted()) calls · \(tokens) tokens" + (models.isEmpty ? "" : " · \(models)")
    }

    private func footnote(_ usage: AgentUsage) -> String {
        var text = "Estimates from Anthropic's list prices (cache discounts and web searches included), not your actual bill."
        if let since = usage.trackingSince {
            text += " Tracking began \(Date(timeIntervalSince1970: since).formatted(date: .abbreviated, time: .omitted))."
        } else {
            text += " Tracking starts with the next thing JARVIS does."
        }
        return text
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            usage = try await JarvisAPIClient.make(from: settings).agentUsage(days: range.rawValue)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Dollars at a glance: cents shown, sub-cent amounts called out as such.
    static func money(_ usd: Double) -> String {
        if usd <= 0 { return "$0" }
        if usd < 0.01 { return "<1¢" }
        return usd.formatted(.currency(code: "USD").precision(.fractionLength(2)))
    }

    /// "claude-sonnet-5-5" → "Sonnet 5.5".
    static func shortModel(_ id: String) -> String {
        let parts = id.replacingOccurrences(of: "claude-", with: "").split(separator: "-")
        guard let family = parts.first else { return id }
        let version = parts.dropFirst().prefix { $0.allSatisfy(\.isNumber) && $0.count <= 2 }.joined(separator: ".")
        return family.capitalized + (version.isEmpty ? "" : " " + version)
    }
}
