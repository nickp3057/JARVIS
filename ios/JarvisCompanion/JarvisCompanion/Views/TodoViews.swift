import SwiftUI
import UniformTypeIdentifiers

/// To-dos: JARVIS's open reminders, nothing else (maintenance and shopping
/// have their own lists). Works like Reminders, same as the shopping list:
/// New Item opens a row at the end (Return keeps it open for the next one),
/// long ones are cut off with "…" and tapping one opens the whole text to
/// read or edit in place, and ticking the circle finishes it (with Undo).
struct TodoListView: View {
    @ObservedObject var model: HubViewModel
    var startAdding = false

    private enum Field: Hashable {
        case add
        case item(String)
    }

    @FocusState private var focus: Field?
    @State private var adding = false
    @State private var newTodo = ""
    /// Wording being edited in place, by id; saved when focus leaves.
    @State private var edits: [String: String] = [:]
    /// Saved wording shown until the Now feed catches up.
    @State private var renamed: [String: String] = [:]
    @State private var ticking: Set<String> = []
    @State private var lastDone: NowReminder?
    @State private var undoTask: Task<Void, Never>?

    @MainActor
    static func open(_ model: HubViewModel) -> [NowReminder] {
        (model.now.value?.reminders ?? []).filter { !model.finishedTodoIDs.contains($0.id) }
    }

    var body: some View {
        let items = Self.open(model)
        List {
            if !items.isEmpty || adding {
                Section {
                    ForEach(items) { row($0) }
                    if adding { addRow }
                }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .scrollDismissesKeyboard(.interactively)
        .background { JarvisBackdrop() }
        .overlay {
            if items.isEmpty, !adding, model.now.value != nil {
                ContentUnavailableView {
                    Label("All clear", systemImage: "checkmark.circle")
                } description: {
                    Text("Nothing on your plate. Tap New Item, or tell JARVIS “remind me to…”.")
                }
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { bottomBar }
        .animation(.snappy, value: items.map(\.id))
        .navigationTitle("To do")
        .toolbar(.visible, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                // A list copied from Notes or a text: one to-do per line.
                PasteButton(supportedContentTypes: [.plainText]) { providers in
                    _ = providers.first?.loadObject(ofClass: String.self) { text, _ in
                        guard let text else { return }
                        let lines = ShoppingPaste.lines(text)
                        Task { @MainActor in for line in lines { await model.addTodo(line) } }
                    }
                }
                .labelStyle(.iconOnly)
                .buttonBorderShape(.circle)
                .tint(JarvisTheme.cyan)
            }
            ToolbarItem(placement: .keyboard) {
                HStack {
                    Spacer()
                    Button("Done") { focus = nil }.fontWeight(.semibold)
                }
            }
        }
        .refreshable { await model.refreshNow() }
        .onAppear { if startAdding { beginAdding(after: 450) } }
        .onChange(of: focus) { old, new in
            if case .item(let id) = old, old != new { commitEdit(id) }
            if old == .add, new != .add { closeAddRow() }
        }
        .onChange(of: items.map(\.text)) { _, _ in
            // The feed has the new wording: stop overriding it.
            renamed = renamed.filter { id, text in items.first { $0.id == id }?.text != text }
        }
        .sensoryFeedback(.success, trigger: lastDone?.id)
    }

    // MARK: Rows

    private func text(_ reminder: NowReminder) -> String { renamed[reminder.id] ?? reminder.text }

    private func row(_ reminder: NowReminder) -> some View {
        let ticked = ticking.contains(reminder.id)
        let isEditing = focus == .item(reminder.id)
        return HStack(alignment: .top, spacing: 12) {
            Button { complete(reminder) } label: { TickCircle(ticked: ticked) }
                .buttonStyle(.plain)
                .accessibilityLabel("Done: \(text(reminder))")
            VStack(alignment: .leading, spacing: 2) {
                if isEditing || edits[reminder.id] != nil {
                    // Open: the whole thing, editable.
                    TextField("To-do", text: editBinding(reminder), axis: .vertical)
                        .focused($focus, equals: .item(reminder.id))
                        .submitLabel(.done)
                } else {
                    // Closed: one line, cut off with "…"; tap to open.
                    Text(text(reminder))
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .strikethrough(ticked)
                        .foregroundStyle(ticked ? .secondary : .primary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                        .onTapGesture { beginEditing(reminder) }
                        .accessibilityHint("Opens the whole to-do")
                }
                if let dueAt = reminder.dueAt {
                    Label(Date(timeIntervalSince1970: dueAt).formatted(.dateTime.weekday(.abbreviated).hour().minute()),
                          systemImage: reminder.isDue ? "exclamationmark.circle" : "bell")
                        .font(.subheadline)
                        .foregroundStyle(reminder.isDue ? JarvisTheme.amber : .secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 3)
        }
        .padding(.vertical, 2)
        .opacity(ticked ? 0.6 : 1)
        .swipeActions(edge: .trailing) {
            Button("Delete", systemImage: "trash", role: .destructive) { model.completeTodo(id: reminder.id) }
        }
        .contextMenu {
            Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = text(reminder) }
            Button("Delete", systemImage: "trash", role: .destructive) { model.completeTodo(id: reminder.id) }
        }
    }

    private var addRow: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "circle")
                .font(.title2.weight(.light))
                .foregroundStyle(.tertiary)
                .frame(width: 28, height: 28)
            TextField("New Item", text: $newTodo, axis: .vertical)
                .focused($focus, equals: .add)
                .submitLabel(.next)
                .padding(.top, 3)
                .onChange(of: newTodo) { _, text in
                    // Return in a vertical field inserts a newline: it means "add".
                    if text.contains("\n") {
                        newTodo = text.replacingOccurrences(of: "\n", with: " ")
                        submit()
                    }
                }
        }
        .padding(.vertical, 2)
    }

    // MARK: Bottom bar (New Item, Undo)

    private var bottomBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let done = lastDone {
                HStack(spacing: 12) {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(JarvisTheme.success)
                    Text("Done: \(done.text)").font(.subheadline.weight(.medium)).lineLimit(1)
                    Spacer(minLength: 8)
                    if done.dueAt == nil {
                        Button("Undo") {
                            undoTask?.cancel()
                            withAnimation(.snappy) { lastDone = nil }
                            Task { await model.addTodo(done.text) }
                        }
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(JarvisTheme.cyan)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .background(.regularMaterial, in: .capsule)
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
            if focus == nil {
                Button { beginAdding() } label: {
                    Label("New Item", systemImage: "plus.circle.fill")
                        .font(.headline)
                        .foregroundStyle(JarvisTheme.cyan)
                        .padding(.horizontal, 14).padding(.vertical, 10)
                        .background(.regularMaterial, in: .capsule)
                }
                .buttonStyle(.plain)
                .transition(.opacity)
            }
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .animation(.snappy, value: focus == nil)
    }

    // MARK: Actions

    private func editBinding(_ reminder: NowReminder) -> Binding<String> {
        Binding(get: { edits[reminder.id] ?? text(reminder) }, set: { new in
            // Return in the vertical field means "done editing".
            if new.contains("\n") {
                edits[reminder.id] = new.replacingOccurrences(of: "\n", with: "")
                focus = nil
            } else {
                edits[reminder.id] = new
            }
        })
    }

    private func beginEditing(_ reminder: NowReminder) {
        edits[reminder.id] = text(reminder)
        Task {
            try? await Task.sleep(for: .milliseconds(30))
            focus = .item(reminder.id)
        }
    }

    /// Focus left a to-do: save new wording; emptied out means delete, the
    /// way clearing a reminder does.
    private func commitEdit(_ id: String) {
        guard let typed = edits.removeValue(forKey: id),
              let reminder = Self.open(model).first(where: { $0.id == id }) else { return }
        let new = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        if new.isEmpty {
            model.completeTodo(id: id)
        } else if new != text(reminder) {
            renamed[id] = new
            Task { if !(await model.renameTodo(id: id, text: new)) { renamed[id] = nil } }
        }
    }

    private func beginAdding(after delay: Int = 60) {
        withAnimation(.snappy) { adding = true }
        Task {
            try? await Task.sleep(for: .milliseconds(delay))
            focus = .add
        }
    }

    /// Return: add it and keep the row open for the next one.
    private func submit() {
        let text = newTodo.trimmingCharacters(in: .whitespacesAndNewlines)
        newTodo = ""
        guard !text.isEmpty else { focus = nil; return }
        Task { if !(await model.addTodo(text)) { newTodo = text } }
        focus = .add
    }

    /// Focus left the New Item row: keep what was typed, close the row.
    private func closeAddRow() {
        Task {
            try? await Task.sleep(for: .milliseconds(150))
            guard focus != .add else { return }
            let text = newTodo.trimmingCharacters(in: .whitespacesAndNewlines)
            newTodo = ""
            if !text.isEmpty { await model.addTodo(text) }
            withAnimation(.snappy) { adding = false }
        }
    }

    /// Check, hold the tick for a beat so the eye sees it, then it leaves.
    private func complete(_ reminder: NowReminder) {
        guard !ticking.contains(reminder.id) else { return }
        if focus == .item(reminder.id) { focus = nil }
        ticking.insert(reminder.id)
        Task {
            try? await Task.sleep(for: .milliseconds(700))
            model.completeTodo(id: reminder.id)
            ticking.remove(reminder.id)
            withAnimation(.snappy) { lastDone = reminder }
            undoTask?.cancel()
            undoTask = Task {
                try? await Task.sleep(for: .seconds(4))
                if !Task.isCancelled { withAnimation(.snappy) { lastDone = nil } }
            }
        }
    }
}
