import SwiftUI

/// One note in the Obsidian vault JARVIS reads (`GET /api/knowledge`).
struct KnowledgeNote: Decodable, Identifiable, Hashable {
    let path: String
    let title: String
    let folder: String
    let modified: Double
    var id: String { path }

    /// "Jarvis Memory/Journal" -> "Journal"; the top folder reads as "Memory".
    var section: String {
        let last = folder.split(separator: "/").last.map(String.init) ?? folder
        return last == "Jarvis Memory" ? "Memory" : last
    }

    var isProfile: Bool { path.hasSuffix("Jarvis Memory/Profile.md") }
    var displayTitle: String { isProfile ? "What I know about you" : title }
}

struct KnowledgeNoteText: Decodable {
    let path: String
    let title: String
    let text: String
}

/// What JARVIS knows: the profile memory (facts he's learned), the daily
/// journal and captured notes, read-only, from the Obsidian vault on Core.
/// A tab on the iPad (and Mac); a sheet from Settings on the phone.
struct KnowledgeView: View {
    @EnvironmentObject private var settings: AppSettings
    @State private var notes: [KnowledgeNote] = []
    @State private var selection: KnowledgeNote.ID?
    @State private var error: String?
    @State private var loaded = false

    private var sections: [(String, [KnowledgeNote])] {
        var order: [String] = []
        var grouped: [String: [KnowledgeNote]] = [:]
        for note in notes {
            if grouped[note.section] == nil { order.append(note.section) }
            grouped[note.section, default: []].append(note)
        }
        return order.map { ($0, grouped[$0] ?? []) }
    }

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(JarvisTheme.amber)
                } else if loaded && notes.isEmpty {
                    Text("No notes yet. JARVIS writes here as he learns about you.").foregroundStyle(.secondary)
                }
                ForEach(sections, id: \.0) { section, items in
                    Section(section) {
                        ForEach(items) { note in
                            Label(note.displayTitle, systemImage: note.isProfile ? "person.text.rectangle" : "doc.text")
                                .tag(note.id)
                        }
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background { JarvisBackdrop() }
            .navigationTitle("What JARVIS knows")
            .refreshable { await load() }
        } detail: {
            if let selection {
                NoteDetail(path: selection).id(selection)
            } else {
                ContentUnavailableView("Pick a note", systemImage: "brain",
                                       description: Text("Everything JARVIS remembers about you lives in these notes."))
                    .background { JarvisBackdrop() }
            }
        }
        .task {
            await load()
            if selection == nil { selection = notes.first?.id }
        }
    }

    private func load() async {
        do {
            notes = try await JarvisAPIClient.make(from: settings).knowledgeNotes()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        loaded = true
    }
}

private struct NoteDetail: View {
    @EnvironmentObject private var settings: AppSettings
    let path: String
    @State private var note: KnowledgeNoteText?
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 6) {
                if let note {
                    MarkdownBlocks(text: note.text)
                } else if let error {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(JarvisTheme.amber)
                } else {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .padding(24)
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .background { JarvisBackdrop() }
        .navigationTitle(note?.title ?? "")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task { await load() }
    }

    private func load() async {
        do {
            note = try await JarvisAPIClient.make(from: settings).knowledgeNote(path)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// Just enough Markdown for the vault: headings, bullets (nested by indent),
/// quotes and paragraphs, with inline bold/italic/links/code.
private struct MarkdownBlocks: View {
    let text: String

    var body: some View {
        ForEach(Array(text.components(separatedBy: "\n").enumerated()), id: \.offset) { _, line in
            row(line)
        }
    }

    @ViewBuilder
    private func row(_ raw: String) -> some View {
        let trimmed = raw.trimmingCharacters(in: .whitespaces)
        let indent = CGFloat(raw.prefix { $0 == " " }.count / 2) * 18
        if trimmed.isEmpty {
            Spacer().frame(height: 4)
        } else if trimmed.hasPrefix("# ") {
            Text(inline(String(trimmed.dropFirst(2)))).font(.title.weight(.bold)).padding(.bottom, 4)
        } else if trimmed.hasPrefix("## ") {
            Text(inline(String(trimmed.dropFirst(3)).capitalized)).font(.title3.weight(.semibold))
                .foregroundStyle(JarvisTheme.cyan).padding(.top, 10)
        } else if trimmed.hasPrefix("### ") {
            Text(inline(String(trimmed.dropFirst(4)))).font(.headline).padding(.top, 6)
        } else if trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Circle().fill(JarvisTheme.cyan.opacity(0.8)).frame(width: 5, height: 5)
                Text(inline(String(trimmed.dropFirst(2)))).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, indent)
        } else if trimmed.hasPrefix("> ") {
            Text(inline(String(trimmed.dropFirst(2)))).font(.callout).foregroundStyle(.secondary)
                .padding(.leading, 10)
                .overlay(alignment: .leading) { Rectangle().fill(.secondary.opacity(0.5)).frame(width: 2) }
        } else {
            Text(inline(trimmed)).fixedSize(horizontal: false, vertical: true).padding(.leading, indent)
        }
    }

    private func inline(_ text: String) -> AttributedString {
        (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(text)
    }
}
