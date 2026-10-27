import SwiftUI

/// Tint for a task's countdown: overdue is the warning state, due today and
/// due this week are amber, everything else stays quiet.
extension MaintenanceTask {
    var tint: Color {
        switch status {
        case "overdue": return JarvisTheme.critical
        case "due", "soon": return JarvisTheme.amber
        default: return .secondary
        }
    }

    var symbol: String {
        switch status {
        case "overdue": return "exclamationmark.circle.fill"
        case "log": return "list.bullet.clipboard"
        default: return "hammer"
        }
    }
}

/// Navigation inside the Home tab's stack.
enum MaintenanceRoute: Hashable {
    case all
    case task(Int)
}

/// Every task, pushed from the Hub's Maintenance card. Swipe right to log one done.
struct MaintenanceListView: View {
    @ObservedObject var model: HubViewModel
    @State private var editing = false
    @State private var completing: MaintenanceTask?

    var body: some View {
        List {
            if let tasks = model.maintenance.value {
                ForEach(tasks) { task in
                    NavigationLink(value: MaintenanceRoute.task(task.id)) { MaintenanceRow(task: task) }
                        .swipeActions(edge: .leading) {
                            Button("Done", systemImage: "checkmark") { completing = task }
                                .tint(JarvisTheme.success)
                        }
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background { JarvisBackdrop() }
        .navigationTitle("Maintenance")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Add task", systemImage: "plus") { editing = true }
            }
        }
        .sheet(isPresented: $editing) { MaintenanceEditor(model: model, task: nil) }
        .sheet(item: $completing) { MaintenanceCompleteSheet(model: model, task: $0) }
        .refreshable { await model.refreshMaintenance() }
    }
}

struct MaintenanceRow: View {
    let task: MaintenanceTask

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: task.symbol)
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(task.status == "ok" || task.status == "log" ? JarvisTheme.cyan : task.tint)
                .frame(width: 34, height: 34)
                .background((task.status == "ok" || task.status == "log" ? JarvisTheme.cyan : task.tint).opacity(0.11), in: .circle)
            VStack(alignment: .leading, spacing: 3) {
                Text(task.name).font(.subheadline.weight(.medium)).lineLimit(2)
                Text("\(task.lastDoneText) · \(task.scheduleLabel.prefix(1).lowercased() + task.scheduleLabel.dropFirst())")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer(minLength: 6)
            Text(task.countdown)
                .font(.caption.monospacedDigit().weight(.semibold))
                .foregroundStyle(task.tint)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}

/// One task: countdown, a big "Mark done", and the log.
struct MaintenanceDetailView: View {
    @ObservedObject var model: HubViewModel
    let taskID: Int
    @Environment(\.dismiss) private var dismiss
    @State private var history: [MaintenanceLogEntry] = []
    @State private var historyError: String?
    @State private var completing = false
    @State private var editing = false
    @State private var confirmingDelete = false

    private var task: MaintenanceTask? { model.maintenance.value?.first { $0.id == taskID } }

    var body: some View {
        List {
            if let task {
                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(task.countdown)
                            .font(.title2.monospacedDigit().weight(.semibold))
                            .foregroundStyle(task.status == "ok" || task.status == "log" ? JarvisTheme.cyan : task.tint)
                        Text(subtitle(task)).font(.subheadline).foregroundStyle(.secondary)
                        if !task.notes.isEmpty {
                            Text(task.notes).font(.footnote).foregroundStyle(.secondary).padding(.top, 2)
                        }
                    }
                    .padding(.vertical, 4)
                }
                Section {
                    Button { completing = true } label: {
                        Label("Mark done", systemImage: "checkmark.circle.fill")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(JarvisTheme.backgroundDeep)
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets())
                }
                Section("History") {
                    if let historyError {
                        Label(historyError, systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote).foregroundStyle(JarvisTheme.amber)
                    } else if history.isEmpty {
                        Text("Nothing logged yet.").foregroundStyle(.secondary)
                    }
                    ForEach(history) { entry in
                        VStack(alignment: .leading, spacing: 3) {
                            if let date = entry.date {
                                Text(date, format: .dateTime.weekday(.abbreviated).month(.abbreviated).day().year())
                                    .font(.subheadline.weight(.medium))
                            } else {
                                Text(entry.completedAt).font(.subheadline.weight(.medium))
                            }
                            if !entry.note.isEmpty {
                                Text(entry.note).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .swipeActions {
                            Button("Delete", role: .destructive) {
                                Task {
                                    if await model.deleteMaintenanceEntry(entry, of: task) { await loadHistory() }
                                }
                            }
                        }
                    }
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background { JarvisBackdrop() }
        .navigationTitle(task?.name ?? "Maintenance")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if task != nil {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu("Options", systemImage: "ellipsis.circle") {
                        Button("Edit", systemImage: "pencil") { editing = true }
                        Button("Delete task", systemImage: "trash", role: .destructive) { confirmingDelete = true }
                    }
                }
            }
        }
        .confirmationDialog("Delete \(task?.name ?? "task") and its history?",
                            isPresented: $confirmingDelete, titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                guard let task else { return }
                Task { if await model.deleteMaintenance(task) { dismiss() } }
            }
        }
        .sheet(isPresented: $completing, onDismiss: { Task { await loadHistory() } }) {
            if let task { MaintenanceCompleteSheet(model: model, task: task) }
        }
        .sheet(isPresented: $editing) {
            if let task { MaintenanceEditor(model: model, task: task) }
        }
        .task { await loadHistory() }
        .refreshable {
            await model.refreshMaintenance()
            await loadHistory()
        }
    }

    private func subtitle(_ task: MaintenanceTask) -> String {
        var parts = [task.scheduleLabel, task.lastDoneText]
        if let due = task.nextDue.flatMap(MaintenanceTask.parse) {
            parts.append("Due \(due.formatted(.dateTime.month(.abbreviated).day().year()))")
        }
        return parts.joined(separator: " · ")
    }

    private func loadHistory() async {
        guard let task else { return }
        do {
            history = try await model.maintenanceHistory(for: task)
            historyError = nil
        } catch {
            historyError = error.localizedDescription
        }
    }
}

/// "Mark done" with an optional note, and an optional earlier day.
struct MaintenanceCompleteSheet: View {
    @ObservedObject var model: HubViewModel
    let task: MaintenanceTask
    @Environment(\.dismiss) private var dismiss
    @State private var note = ""
    @State private var earlier = false
    @State private var day = Date()
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Note (optional)", text: $note, axis: .vertical)
                        .lineLimit(1...4)
                }
                Section {
                    Toggle("Done on an earlier day", isOn: $earlier.animation())
                    if earlier {
                        DatePicker("Day", selection: $day, in: ...Date(), displayedComponents: .date)
                    }
                }
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote).foregroundStyle(JarvisTheme.amber)
                }
            }
            .navigationTitle(task.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if saving { ProgressView() } else { Button("Done", action: save) }
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func save() {
        saving = true
        Task {
            defer { saving = false }
            if await model.completeMaintenance(task, note: note, on: earlier ? day : nil) {
                dismiss()
            } else {
                error = model.actionError
                model.actionError = nil
            }
        }
    }
}

/// Create or edit a task. Most chores are "every N months"; a fixed date
/// and a plain log are there for the rest.
struct MaintenanceEditor: View {
    @ObservedObject var model: HubViewModel
    let task: MaintenanceTask?
    @Environment(\.dismiss) private var dismiss
    @State private var draft: MaintenanceTaskDraft
    @State private var hasLastDone = false
    @State private var lastDone = Date()
    @State private var hasDay = false
    @State private var saving = false
    @State private var error: String?

    init(model: HubViewModel, task: MaintenanceTask?) {
        self.model = model
        self.task = task
        _draft = State(initialValue: task.map(MaintenanceTaskDraft.init) ?? MaintenanceTaskDraft())
        _hasDay = State(initialValue: task?.fixedDay != nil)
    }

    private var monthBinding: Binding<Int> {
        Binding(get: { draft.fixedMonth ?? 1 }, set: { draft.fixedMonth = $0 })
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name", text: $draft.name)
                        .textInputAutocapitalization(.sentences)
                }
                Section("Schedule") {
                    Picker("Repeats", selection: $draft.scheduleType) {
                        Text("Every…").tag("interval")
                        Text("Same date yearly").tag("fixed_calendar")
                        Text("Just a log").tag("log_only")
                    }
                    switch draft.scheduleType {
                    case "interval":
                        Stepper(value: Binding(get: { draft.intervalValue ?? 1 }, set: { draft.intervalValue = $0 }),
                                in: 1...365) {
                            Text("Every \(draft.intervalValue ?? 1)")
                        }
                        Picker("Unit", selection: Binding(get: { draft.intervalUnit ?? "months" },
                                                          set: { draft.intervalUnit = $0 })) {
                            Text("Days").tag("days")
                            Text("Months").tag("months")
                            Text("Years").tag("years")
                        }
                        .pickerStyle(.segmented)
                    case "fixed_calendar":
                        Picker("Month", selection: monthBinding) {
                            ForEach(1...12, id: \.self) { Text(Calendar.current.monthSymbols[$0 - 1]).tag($0) }
                        }
                        Toggle("Specific day", isOn: $hasDay)
                        if hasDay {
                            Stepper(value: Binding(get: { draft.fixedDay ?? 1 }, set: { draft.fixedDay = $0 }),
                                    in: 1...31) {
                                Text("Day \(draft.fixedDay ?? 1)")
                            }
                        }
                    default:
                        Text("No countdown — just a record of when it was done.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                if task == nil {
                    Section {
                        Toggle("I've done this before", isOn: $hasLastDone.animation())
                        if hasLastDone {
                            DatePicker("Last done", selection: $lastDone, in: ...Date(), displayedComponents: .date)
                        }
                    } footer: {
                        Text("Starts the countdown from then instead of today.")
                    }
                }
                Section("Notes") {
                    TextField("Optional", text: $draft.notes, axis: .vertical).lineLimit(1...5)
                }
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote).foregroundStyle(JarvisTheme.amber)
                }
            }
            .navigationTitle(task == nil ? "New task" : "Edit task")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if saving {
                        ProgressView()
                    } else {
                        Button("Save", action: save)
                            .disabled(draft.name.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            }
        }
    }

    private func save() {
        var body = draft
        body.name = body.name.trimmingCharacters(in: .whitespacesAndNewlines)
        if body.scheduleType == "fixed_calendar" {
            body.fixedMonth = body.fixedMonth ?? 1
            body.fixedDay = hasDay ? (body.fixedDay ?? 1) : nil
        }
        body.lastDone = task == nil && hasLastDone ? HubViewModel.dayString(lastDone) : nil
        saving = true
        Task {
            defer { saving = false }
            if await model.saveMaintenance(body, editing: task) {
                dismiss()
            } else {
                error = model.actionError
                model.actionError = nil
            }
        }
    }
}
