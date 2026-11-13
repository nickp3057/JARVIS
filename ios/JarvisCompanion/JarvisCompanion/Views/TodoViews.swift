import SwiftUI

/// To-dos: JARVIS's open reminders, nothing else (maintenance and shopping
/// have their own lists). Checked means done and deleted, with a beat to see
/// the tick land first.
struct TodoListView: View {
    @ObservedObject var model: HubViewModel
    var startAdding = false
    @State private var newTodo = ""
    @FocusState private var adding: Bool
    @State private var ticking: Set<String> = []
    @State private var expanded: Set<String> = []

    @MainActor
    static func open(_ model: HubViewModel) -> [NowReminder] {
        (model.now.value?.reminders ?? []).filter { !model.finishedTodoIDs.contains($0.id) }
    }

    var body: some View {
        let items = Self.open(model)
        List {
            Section {
                // Like Reminders: the + is a real button (adds what's typed,
                // or puts the cursor in the field), Return adds and keeps the
                // field open for the next one.
                HStack(spacing: 12) {
                    Button { if hasText { add() } else { adding = true } } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.title3)
                            .foregroundStyle(JarvisTheme.cyan)
                            .frame(width: 44, height: 44)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(hasText ? "Add this to-do" : "New to-do")
                    TextField("Add a to-do", text: $newTodo, axis: .vertical)
                        .lineLimit(1...4)
                        .focused($adding)
                        .submitLabel(.done)
                        .onSubmit(add)
                        .onChange(of: newTodo) { _, text in
                            // Return in a vertical field inserts a newline; treat it as submit.
                            if text.hasSuffix("\n") { newTodo = String(text.dropLast()); add() }
                        }
                    if hasText {
                        Button("Add", action: add)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(JarvisTheme.cyan)
                            .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 2)
            }
            if !items.isEmpty {
                Section {
                    ForEach(items) { row($0) }
                }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background { JarvisBackdrop() }
        .overlay {
            if items.isEmpty, model.now.value != nil {
                ContentUnavailableView {
                    Label("All clear", systemImage: "checkmark.circle")
                } description: {
                    Text("Nothing on your plate. Add one above, or tell JARVIS “remind me to…”.")
                }
                .padding(.top, 80)
            }
        }
        .animation(.snappy, value: items.map(\.id))
        .navigationTitle("To do")
        .toolbar(.visible, for: .navigationBar)
        .refreshable { await model.refreshNow() }
        .onAppear { if startAdding { adding = true } }
    }

    private func row(_ reminder: NowReminder) -> some View {
        let ticked = ticking.contains(reminder.id)
        return HStack(spacing: 12) {
            Button { complete(reminder) } label: {
                Image(systemName: ticked ? "checkmark.circle.fill" : "circle")
                    .font(.title2)
                    .foregroundStyle(ticked ? JarvisTheme.success : JarvisTheme.cyan)
                    .contentTransition(.symbolEffect(.replace))
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Done: \(reminder.text)")
            VStack(alignment: .leading, spacing: 2) {
                Text(reminder.text)
                    .font(.body.weight(.medium))
                    .strikethrough(ticked)
                    .foregroundStyle(ticked ? .secondary : .primary)
                    .lineLimit(expanded.contains(reminder.id) ? nil : 1)
                    .truncationMode(.tail)
                if let dueAt = reminder.dueAt {
                    Label(Date(timeIntervalSince1970: dueAt).formatted(.dateTime.weekday(.abbreviated).hour().minute()),
                          systemImage: reminder.isDue ? "exclamationmark.circle" : "bell")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(reminder.isDue ? JarvisTheme.amber : .secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .onTapGesture {  // cut off with "…"; tap to read the whole thing
                withAnimation(.snappy) {
                    if expanded.contains(reminder.id) { expanded.remove(reminder.id) } else { expanded.insert(reminder.id) }
                }
            }
            .accessibilityHint("Shows the whole to-do")
        }
        .listRowInsets(EdgeInsets(top: 4, leading: 4, bottom: 4, trailing: 16))
        .swipeActions(edge: .leading) {
            Button("Done", systemImage: "checkmark") { complete(reminder) }.tint(JarvisTheme.success)
        }
    }

    private func complete(_ reminder: NowReminder) {
        guard !ticking.contains(reminder.id) else { return }
        ticking.insert(reminder.id)
        Task {
            try? await Task.sleep(for: .milliseconds(450))
            model.completeTodo(id: reminder.id)
            ticking.remove(reminder.id)
        }
    }

    private var hasText: Bool { !newTodo.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    private func add() {
        let text = newTodo
        guard !text.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        newTodo = ""
        adding = true
        Task {
            if !(await model.addTodo(text)) { newTodo = text }
        }
    }
}
