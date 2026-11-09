import SwiftUI

/// `GET /api/agents`: JARVIS (the head) and every agent that works for him.
struct AgentRoster: Decodable {
    struct Head: Decodable { let id: String; let name: String; let role: String; let model: String }
    struct Run: Decodable, Hashable { let title: String; let state: String; let at: Double; let summary: String }
    struct Agent: Decodable, Identifiable {
        let id: String
        let name: String
        let role: String
        let symbol: String
        let schedule: String
        let state: String          // running | idle | off
        let note: String
        let needsYou: Int
        let task: String?
        let recent: [Run]
        /// "subagent" (own tools, several steps) or "helper" (one quick AI call).
        let kind: String

        var isHelper: Bool { kind == "helper" }

        enum CodingKeys: String, CodingKey {
            case id, name, role, symbol, schedule, state, note, task, recent, kind
            case needsYou = "needs_you"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = try c.decode(String.self, forKey: .id)
            name = try c.decode(String.self, forKey: .name)
            role = try c.decode(String.self, forKey: .role)
            symbol = try c.decode(String.self, forKey: .symbol)
            schedule = try c.decode(String.self, forKey: .schedule)
            state = try c.decode(String.self, forKey: .state)
            note = try c.decode(String.self, forKey: .note)
            needsYou = try c.decode(Int.self, forKey: .needsYou)
            task = try c.decodeIfPresent(String.self, forKey: .task)
            recent = try c.decode([Run].self, forKey: .recent)
            kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? "subagent"
        }
    }
    let head: Head
    let agents: [Agent]
}

/// JARVIS in the middle, the agents he hands work to around him. Subagents
/// (own tools, several steps) have a solid ring; helpers (one quick AI call)
/// a smaller dashed one. A link carries a moving dot while that agent is
/// working; an agent that's off is dimmed with a dashed link. Tap one to read
/// what it does and what it last did. Draws whatever Core lists, so a new
/// agent appears without an app update. Refreshes every few seconds.
struct AgentMapView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    @State private var roster: AgentRoster?
    @State private var error: String?
    @State private var selected: String?

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                GeometryReader { geo in
                    TimelineView(.animation(minimumInterval: 1.0 / 30)) { context in
                        ZStack {
                            Canvas { canvas, size in drawLinks(&canvas, size: size, now: context.date) }
                            headBadge.position(center(geo.size))
                            ForEach(Array(agents.enumerated()), id: \.element.id) { index, agent in
                                agentBadge(agent)
                                    .contentShape(Rectangle())
                                    .onTapGesture {
                                        withAnimation(.easeInOut(duration: 0.2)) {
                                            selected = selected == agent.id ? nil : agent.id
                                        }
                                    }
                                    .position(spot(index, geo.size))
                            }
                        }
                    }
                }
                .frame(minHeight: 340)
                detail
            }
            .padding(20)
            .background { JarvisBackdrop() }
            .navigationTitle("Agents")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Text(headline)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(workingCount > 0 ? JarvisTheme.success : Color.secondary)
                }
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .task { await poll() }
    }

    private var agents: [AgentRoster.Agent] {
        let all = roster?.agents ?? []
        return all.filter { !$0.isHelper } + all.filter(\.isHelper)
    }
    private var workingCount: Int { agents.filter { $0.state == "running" }.count }

    private var headline: String {
        if roster == nil { return error == nil ? "Connecting…" : "Can't reach Core" }
        return workingCount > 0 ? "\(workingCount) working" : "All quiet"
    }

    private func poll() async {
        while !Task.isCancelled {
            do {
                let fresh = try await JarvisAPIClient.make(from: settings).agents()
                withAnimation(.easeInOut(duration: 0.3)) { roster = fresh }
                error = nil
            } catch {
                self.error = error.localizedDescription
            }
            try? await Task.sleep(nanoseconds: 4_000_000_000)
        }
    }

    // MARK: Layout

    private func center(_ size: CGSize) -> CGPoint { CGPoint(x: size.width / 2, y: size.height / 2) }

    /// Agents evenly around JARVIS, starting at the top; an ellipse so a wide iPad uses its width.
    private func spot(_ index: Int, _ size: CGSize) -> CGPoint {
        let count = max(agents.count, 1)
        let angle = -Double.pi / 2 + 2 * Double.pi * Double(index) / Double(count)
        let c = center(size)
        let rx = size.width / 2 - 70, ry = size.height / 2 - 52
        return CGPoint(x: c.x + rx * cos(angle), y: c.y + ry * sin(angle))
    }

    private func drawLinks(_ canvas: inout GraphicsContext, size: CGSize, now: Date) {
        let core = center(size)
        for (index, agent) in agents.enumerated() {
            let end = spot(index, size)
            let running = agent.state == "running"
            let off = agent.state == "off"
            var line = Path()
            line.move(to: core)
            line.addLine(to: end)
            canvas.stroke(line, with: .color(JarvisTheme.reactor.opacity(off ? 0.10 : running ? 0.6 : 0.25)),
                          style: StrokeStyle(lineWidth: running ? 2.5 : 1.5, lineCap: .round, dash: off ? [4, 6] : []))
            guard running else { continue }
            // Work flowing out from JARVIS to the agent: a steady moving dot, no flashing.
            let f = now.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 1.6) / 1.6
            let dot = CGPoint(x: core.x + (end.x - core.x) * f, y: core.y + (end.y - core.y) * f)
            canvas.fill(Path(ellipseIn: CGRect(x: dot.x - 4, y: dot.y - 4, width: 8, height: 8)), with: .color(.white.opacity(0.9)))
        }
    }

    // MARK: Badges

    private var headBadge: some View {
        VStack(spacing: 2) {
            JarvisCoreMark(size: 104, active: agents.contains { $0.state == "running" })
            Text("JARVIS").font(.headline.weight(.semibold)).tracking(3)
            Text("The head").font(.caption2).foregroundStyle(.secondary)
        }
        .fixedSize()
        .onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { selected = selected == "jarvis" ? nil : "jarvis" } }
    }

    private func agentBadge(_ agent: AgentRoster.Agent) -> some View {
        let running = agent.state == "running"
        let off = agent.state == "off"
        let chosen = selected == agent.id
        let ring = JarvisTheme.cyan.opacity(off ? 0.2 : running || chosen ? 0.95 : 0.45)
        let side: CGFloat = agent.isHelper ? 44 : 56
        return VStack(spacing: 6) {
            ZStack(alignment: .topTrailing) {
                ZStack {
                    Circle().fill(JarvisTheme.cyan.opacity(off ? 0.04 : running || chosen ? 0.30 : agent.isHelper ? 0.06 : 0.12))
                    Circle().strokeBorder(ring, style: StrokeStyle(lineWidth: 1.5, dash: agent.isHelper ? [3, 3] : []))
                    Image(systemName: agent.symbol)
                        .font(.system(size: agent.isHelper ? 17 : 21, weight: .medium))
                        .foregroundStyle(off ? Color.secondary : running ? .white : JarvisTheme.cyan)
                }
                .frame(width: side, height: side)
                if agent.needsYou > 0 {
                    Text("\(agent.needsYou)")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.black)
                        .frame(minWidth: 18, minHeight: 18)
                        .background(Circle().fill(JarvisTheme.cyan))
                        .offset(x: 4, y: -4)
                        .accessibilityLabel("\(agent.needsYou) waiting for you")
                }
            }
            Text(agent.name).font(.caption.weight(.semibold)).foregroundStyle(off ? .tertiary : .secondary).fixedSize()
            Text(agent.isHelper ? "helper" : "subagent")
                .font(.system(size: 9, weight: .semibold)).textCase(.uppercase).tracking(1)
                .foregroundStyle(.tertiary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(agent.name), \(agent.isHelper ? "helper" : "subagent"), \(running ? "working" : off ? "off" : "idle")")
    }

    // MARK: Detail card

    @ViewBuilder private var detail: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let id = selected, id != "jarvis", let agent = agents.first(where: { $0.id == id }) {
                agentDetail(agent)
            } else if selected == "jarvis", let head = roster?.head {
                Label("JARVIS", systemImage: "cpu").font(.footnote.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
                Text(head.role).font(.subheadline)
                Text("Model: \(head.model)").font(.caption.monospaced()).foregroundStyle(.secondary)
                Text("\(agents.count) agents report to him.").font(.caption).foregroundStyle(.secondary)
            } else {
                HStack {
                    Text("Who's working").font(.footnote.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
                    Spacer()
                    Text("Tap an agent").font(.caption).foregroundStyle(.secondary)
                }
                section("Subagents · own tools, work in steps", agents.filter { !$0.isHelper })
                section("Helpers · one quick AI call", agents.filter(\.isHelper))
            }
        }
        .frame(maxWidth: .infinity, minHeight: 150, alignment: .topLeading)
        .padding(16)
        .jarvisSurface(in: .rect(cornerRadius: 20))
    }

    @ViewBuilder private func section(_ title: String, _ list: [AgentRoster.Agent]) -> some View {
        if !list.isEmpty {
            Text(title).font(.caption2.weight(.semibold)).textCase(.uppercase).foregroundStyle(.tertiary).padding(.top, 4)
            ForEach(list) { agent in
                HStack(spacing: 8) {
                    Image(systemName: agent.symbol).frame(width: 20).foregroundStyle(JarvisTheme.cyan)
                    Text(agent.name).font(.footnote.weight(.semibold))
                    Text(status(agent)).font(.footnote).foregroundStyle(.secondary).lineLimit(1)
                    Spacer(minLength: 0)
                }
            }
        }
    }

    private func status(_ agent: AgentRoster.Agent) -> String {
        if let task = agent.task { return task }
        if agent.state == "off" { return agent.note.isEmpty ? "Off" : agent.note }
        if agent.needsYou > 0 { return "\(agent.needsYou) waiting for you" }
        if let last = agent.recent.first {
            return "Last: " + Date(timeIntervalSince1970: last.at).formatted(.relative(presentation: .named))
        }
        return agent.schedule
    }

    private func agentDetail(_ agent: AgentRoster.Agent) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(agent.name, systemImage: agent.symbol).font(.footnote.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
                Spacer()
                Button { withAnimation { selected = nil } } label: { Image(systemName: "xmark.circle.fill") }
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Close")
            }
            Text(agent.isHelper ? "Helper: one quick AI call, no tools." : "Subagent: its own tools, works through steps on its own.")
                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            Text(agent.role).font(.subheadline)
            Text("\(agent.schedule) · \(agent.task ?? agent.state.capitalized)").font(.caption).foregroundStyle(.secondary)
            if !agent.note.isEmpty { Text(agent.note).font(.caption).foregroundStyle(.secondary) }
            if agent.needsYou > 0 {
                Text("\(agent.needsYou) waiting for your OK. Ask JARVIS \"what's waiting on my portfolio?\"")
                    .font(.caption.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if agent.recent.isEmpty {
                        Text("Hasn't run since Core started.").font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(agent.recent, id: \.self) { run in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(run.title).font(.caption.weight(.semibold))
                                Spacer()
                                Text(Date(timeIntervalSince1970: run.at), format: .relative(presentation: .numeric, unitsStyle: .narrow))
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                            Text(run.summary).font(.footnote).foregroundStyle(run.state == "failed" ? .orange : .primary)
                        }
                    }
                }
            }
            .frame(maxHeight: 220)
        }
    }
}
