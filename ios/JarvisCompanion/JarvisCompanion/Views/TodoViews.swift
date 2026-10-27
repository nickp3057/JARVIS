import SwiftUI

/// Home's to-do list, opened from the To do tile. Rows are open reminders
/// (done = deleted for good) and maintenance chores that are due (done =
/// logged, back next cycle). The tile's + opens it straight into the add field.
struct TodoSheet: View {
    enum Mode: Identifiable {
        case list, add
        var id: Self { self }
    }

    struct Item: Identifiable {
        enum Source { case reminder(String), maintenance(MaintenanceTask) }
        let id: String
        let title: String
        let detail: String?
        let tint: Color
        let source: Source
    }

    @ObservedObject var model: HubViewModel
    let startAdding: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var newTodo = ""
    @FocusState private var adding: Bool

    @MainActor
    static func items(_ model: HubViewModel) -> [Item] {
        let chores = (model.maintenance.value ?? []).filter(\.needsAttention).map {
            Item(id: "m\($0.id)", title: $0.name, detail: $0.countdown.capitalized,
                 tint: $0.tint, source: .maintenance($0))
        }
        let reminders = (model.now.value?.reminders ?? [])
            .filter { !model.finishedTodoIDs.contains($0.id) }
            .map { reminder in
                Item(id: reminder.id, title: reminder.text,
                     detail: reminder.dueAt.map {
                         Date(timeIntervalSince1970: $0).formatted(.dateTime.weekday(.abbreviated).hour().minute())
                     },
                     tint: reminder.isDue ? JarvisTheme.amber : .secondary,
                     source: .reminder(reminder.id))
            }
        return chores + reminders
    }

    var body: some View {
        let items = Self.items(model)
        NavigationStack {
            List {
                ForEach(items) { row($0) }
                HStack(spacing: 12) {
                    Image(systemName: "plus")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(JarvisTheme.cyan)
                        .frame(width: 26)
                    TextField("Add a to-do", text: $newTodo)
                        .focused($adding)
                        .submitLabel(.done)
                        .onSubmit(add)
                }
            }
            .scrollContentBackground(.hidden)
            .background { JarvisBackdrop() }
            .animation(.snappy, value: items.map(\.id))
            .navigationTitle("To do")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .navigationDestination(for: MaintenanceRoute.self) { route in
                switch route {
                case .all: MaintenanceListView(model: model)
                case .task(let id): MaintenanceDetailView(model: model, taskID: id)
                }
            }
        }
        .presentationDetents([.medium, .large])
        .onAppear { if startAdding { adding = true } }
    }

    private func row(_ item: Item) -> some View {
        HStack(spacing: 12) {
            Button { complete(item) } label: {
                Image(systemName: "circle")
                    .font(.title3)
                    .foregroundStyle(JarvisTheme.cyan)
                    .frame(width: 26, height: 36)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Done: \(item.title)")
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.subheadline.weight(.medium)).lineLimit(2)
                if let detail = item.detail {
                    Text(detail).font(.caption.weight(.medium)).foregroundStyle(item.tint)
                }
            }
            if case .maintenance(let task) = item.source {
                Spacer(minLength: 0)
                NavigationLink("", value: MaintenanceRoute.task(task.id))
                    .fixedSize()
                    .accessibilityLabel("\(item.title) history")
            }
        }
    }

    private func complete(_ item: Item) {
        switch item.source {
        case .reminder(let id):
            model.completeTodo(id: id)
        case .maintenance(let task):
            Task { await model.completeMaintenance(task, note: "", on: nil) }
        }
    }

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
