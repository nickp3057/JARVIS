import SwiftUI

/// Skills & Fixes: skills JARVIS drafted for himself (approve to teach him),
/// what Nick asked for that he can't do yet, and repairs waiting or done.
struct SkillsView: View {
    @ObservedObject var model: SkillsModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let error = model.error { ErrorLine(text: error) }
                skillsPanel
                wishesPanel
                repairsPanel
            }
            .padding(24)
        }
        .toolbar {
            Button { Task { await model.load() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                .keyboardShortcut("r")
                .help("Refresh (⌘R)")
        }
        .task {
            while !Task.isCancelled {
                await model.load()
                try? await Task.sleep(for: .seconds(15))
            }
        }
    }

    // MARK: Skills

    private var skillsPanel: some View {
        let drafts = model.skills.filter(\.isDraft).count
        return Panel("Skills JARVIS has learned", symbol: "wand.and.stars", trailing: {
            if drafts > 0 { Pill(text: "\(drafts) to approve", color: JarvisTheme.gold) }
        }) {
            if model.skills.isEmpty {
                Text("None yet. Ask JARVIS for a routine (\u{201C}set up date night: movie scene, jazz, volume 30\u{201D}) or draft one from a request below.")
                    .foregroundStyle(.secondary)
            }
            VStack(spacing: 10) {
                ForEach(model.skills) { skill in skillRow(skill) }
            }
        }
    }

    private func skillRow(_ skill: LearnedSkill) -> some View {
        GlowTile(active: skill.status == "active", tint: skill.isDraft ? JarvisTheme.gold : JarvisTheme.accent) {
            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(skill.title).font(.headline)
                        if skill.isDraft { Pill(text: "DRAFT", color: JarvisTheme.gold) }
                        if skill.status == "disabled" { Pill(text: "OFF", color: .gray) }
                    }
                    Text(skill.description).foregroundStyle(.secondary)
                    Text(skill.steps.map { $0.tool.replacingOccurrences(of: "_", with: " ") }.joined(separator: "  →  ")
                         + (skill.params.isEmpty ? "" : "   ·   asks for " + skill.params.map(\.name).joined(separator: ", ")))
                        .font(.caption.monospaced()).foregroundStyle(.tertiary)
                }
                Spacer()
                if model.busy.contains(skill.id) { ProgressView().controlSize(.small) }
                if skill.isDraft {
                    Button("Dismiss") { Task { await model.setSkill(skill, status: "deleted") } }
                    Button("Approve") { Task { await model.setSkill(skill, status: "active") } }
                        .buttonStyle(.borderedProminent)
                        .help("JARVIS can use this skill from now on")
                } else {
                    Menu {
                        Button(skill.status == "active" ? "Switch off" : "Switch on") {
                            Task { await model.setSkill(skill, status: skill.status == "active" ? "disabled" : "active") }
                        }
                        Button("Delete", role: .destructive) { Task { await model.setSkill(skill, status: "deleted") } }
                    } label: { Image(systemName: "ellipsis.circle") }
                    .menuStyle(.borderlessButton)
                    .frame(width: 30)
                }
            }
        }
    }

    // MARK: Requests

    private var wishesPanel: some View {
        Panel("Skill requests", symbol: "sparkles", trailing: {
            if !model.wishes.isEmpty { Pill(text: "\(model.wishes.count)") }
        }) {
            if model.wishes.isEmpty {
                Text("When you ask for something JARVIS can't do yet, it lands here.").foregroundStyle(.secondary)
            }
            VStack(spacing: 10) {
                ForEach(model.wishes) { wish in
                    HStack(alignment: .top, spacing: 14) {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 8) {
                                Text(wish.summary).font(.headline)
                                if wish.count > 1 { Pill(text: "asked \(wish.count)×", color: JarvisTheme.warning) }
                            }
                            if let ask = wish.asks.last { Text("\u{201C}\(ask)\u{201D}").font(.callout).foregroundStyle(.secondary) }
                            if wish.drafting == true {
                                Label("JARVIS is drafting a skill…", systemImage: "hourglass").font(.caption).foregroundStyle(JarvisTheme.accent)
                            } else if wish.draftSkillID != nil {
                                Label("A draft is waiting above", systemImage: "arrow.up").font(.caption).foregroundStyle(JarvisTheme.gold)
                            } else if let note = wish.note, !note.isEmpty {
                                Text(note).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                            }
                        }
                        Spacer()
                        if model.busy.contains(wish.id) { ProgressView().controlSize(.small) }
                        Button("Draft a skill") { Task { await model.draft(wish) } }
                            .disabled(wish.drafting == true)
                        Menu {
                            Button("JARVIS can do this now") { Task { await model.closeWish(wish, status: "done") } }
                            Button("Not needed") { Task { await model.closeWish(wish, status: "declined") } }
                        } label: { Image(systemName: "ellipsis.circle") }
                        .menuStyle(.borderlessButton)
                        .frame(width: 30)
                    }
                    .padding(12)
                    .background(Color.white.opacity(0.03), in: .rect(cornerRadius: 10))
                }
            }
        }
    }

    // MARK: Repairs

    private var repairsPanel: some View {
        let shown = model.repairs.filter { $0.status != "rejected" }.prefix(8)
        return Panel("Fixes", symbol: "wrench.and.screwdriver") {
            if shown.isEmpty { Text("No repairs in the last day.").foregroundStyle(.secondary) }
            VStack(spacing: 10) {
                ForEach(Array(shown)) { repair in
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: icon(repair)).foregroundStyle(color(repair)).frame(width: 18)
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 8) {
                                Text(repair.label).font(.callout.weight(.medium))
                                if repair.approvedBy == "auto" { Pill(text: "AUTOPILOT", color: JarvisTheme.accent) }
                            }
                            Text(repair.isPending ? repair.rationale : (repair.result.isEmpty ? repair.status.capitalized : repair.result))
                                .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                        }
                        Spacer()
                        if repair.isPending {
                            Button("Dismiss") { Task { await model.resolve(repair, approve: false) } }
                            Button("Approve") { Task { await model.resolve(repair, approve: true) } }
                                .buttonStyle(.borderedProminent)
                        }
                    }
                }
            }
        }
    }

    private func icon(_ repair: RepairItem) -> String {
        switch repair.status {
        case "applied": return "checkmark.circle.fill"
        case "running": return "clock"
        case "pending": return "hand.raised"
        default: return "exclamationmark.circle.fill"
        }
    }

    private func color(_ repair: RepairItem) -> Color {
        switch repair.status {
        case "applied": return JarvisTheme.success
        case "running", "pending": return JarvisTheme.accent
        default: return JarvisTheme.warning
        }
    }
}
