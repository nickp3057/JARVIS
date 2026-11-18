import PhotosUI
import UniformTypeIdentifiers
import SwiftUI
import UIKit

/// Lists pushed from Home's Lists row.
enum ListRoute: Hashable {
    case todo(adding: Bool)
    case shopping(adding: Bool)
    case savedItems
}

// MARK: - Shopping list

/// What to buy, one section per store, working the way Reminders does: the
/// New Item button opens a row at the end of the section you were last in
/// (Return keeps it open for the next one), tap a name to rename it in
/// place, tick the circle and it's bought. Typing a new item also offers
/// saved items (photos and all), and pasted text or a copied image becomes
/// items straight away.
struct ShoppingListView: View {
    @ObservedObject var model: HubViewModel
    var startAdding = false

    private enum Field: Hashable {
        case add(String)
        case item(Int)
    }

    @FocusState private var focus: Field?
    /// What's typed in each section's New Item row, by store.
    @State private var drafts: [String: String] = [:]
    /// Names being edited in place, by item id; committed when focus leaves.
    @State private var renames: [Int: String] = [:]
    /// The section showing the New Item row, if any.
    @State private var addingStore: String?
    /// The section New Item / Paste add to.
    @State private var lastStore = ""
    @State private var editing: ShoppingEditorTarget?
    @State private var lastBought: ShoppingItem?
    @State private var undoTask: Task<Void, Never>?
    /// Rows ticked a moment ago: drawn checked while they leave.
    @State private var ticking: Set<Int> = []
    @State private var namingStore = false
    @State private var storeName = ""

    private var list: [ShoppingItem] { model.shopping.value?.list ?? [] }
    private var catalog: [ShoppingItem] { model.shopping.value?.catalog ?? [] }

    /// The stores on the list, then the one being added to if it's new,
    /// then the unsorted items.
    private var sections: [ShoppingGrouping.Section] {
        var out = ShoppingGrouping.sections(list).filter { !$0.store.isEmpty }
        if let store = addingStore, !store.isEmpty,
           !out.contains(where: { $0.store.caseInsensitiveCompare(store) == .orderedSame }) {
            out.append(.init(store: store, items: []))
        }
        let loose = list.filter { $0.store.trimmingCharacters(in: .whitespaces).isEmpty }
        if !loose.isEmpty || addingStore == "" { out.append(.init(store: "", items: loose)) }
        return out
    }

    private var allStores: [String] {
        var seen = Set<String>()
        return (catalog.map(\.store) + [addingStore ?? ""])
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && seen.insert($0.lowercased()).inserted }
            .sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
    }

    var body: some View {
        List {
            ForEach(sections) { section in
                Section {
                    ForEach(section.items) { row($0) }
                        .dropDestination(for: String.self) { ids, _ in _ = move(ids, to: section.store) }
                    if addingStore == section.store {
                        addRow(section.store)
                        ForEach(suggestions(for: section.store)) { suggestionRow($0, store: section.store) }
                    }
                } header: {
                    if !section.store.isEmpty || !section.items.isEmpty {
                        Text(section.store.isEmpty ? "Anywhere" : section.store)
                            .font(.title3.bold())
                            .foregroundStyle(.primary)
                            .textCase(nil)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .scrollDismissesKeyboard(.interactively)
        .background { JarvisBackdrop() }
        .overlay {
            if model.shopping.value != nil && list.isEmpty && addingStore == nil {
                ContentUnavailableView {
                    Label("Nothing to buy", systemImage: "cart")
                } description: {
                    Text("Tap New Item, paste a list, or tell JARVIS “add face lotion to my shopping list.”")
                }
            } else if model.shopping.value == nil {
                if let error = model.shopping.failureText {
                    ContentUnavailableView("Couldn’t load the list", systemImage: "wifi.exclamationmark",
                                           description: Text(error))
                } else {
                    ProgressView()
                }
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { bottomBar }
        .animation(.snappy, value: list.map(\.id))
        .animation(.snappy, value: list.map(\.store))
        .navigationTitle("Shopping")
        .toolbar(.visible, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                PasteButton(supportedContentTypes: [.image, .plainText]) { providers in paste(providers) }
                    .labelStyle(.iconOnly)
                    .buttonBorderShape(.circle)
                    .tint(JarvisTheme.cyan)
            }
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("New Store Section", systemImage: "rectangle.stack.badge.plus") {
                        storeName = ""
                        namingStore = true
                    }
                    NavigationLink(value: ListRoute.savedItems) {
                        Label("Saved Items", systemImage: "square.grid.2x2")
                    }
                } label: {
                    Label("More", systemImage: "ellipsis.circle")
                }
            }
            ToolbarItem(placement: .keyboard) {
                HStack {
                    Spacer()
                    Button("Done") { focus = nil }.fontWeight(.semibold)
                }
            }
        }
        .alert("New Store Section", isPresented: $namingStore) {
            TextField("Costco, Target…", text: $storeName)
            Button("Cancel", role: .cancel) {}
            Button("Add") {
                let name = storeName.trimmingCharacters(in: .whitespaces)
                guard !name.isEmpty else { return }
                startAdding(allStores.first { $0.caseInsensitiveCompare(name) == .orderedSame } ?? name, after: 350)
            }
        }
        .sheet(item: $editing) { target in
            ShoppingItemEditor(model: model, item: target.item, startName: target.name,
                               startStore: target.store, startImage: target.image)
        }
        .refreshable { await model.refreshShopping() }
        .task { await model.refreshShopping() }
        .onAppear { if startAdding { startAdding("", after: 450) } }
        .onChange(of: focus) { old, new in
            if case .item(let id) = old, old != new { commitRename(id) }
            if case .add(let store) = old, old != new { closeAddRow(store) }
            if case .add(let store) = new { lastStore = store }
            if case .item(let id) = new, let item = list.first(where: { $0.id == id }) { lastStore = item.store }
        }
        .sensoryFeedback(.success, trigger: lastBought?.id)
    }

    // MARK: Rows

    private func row(_ item: ShoppingItem) -> some View {
        let ticked = ticking.contains(item.id)
        let isEditing = focus == .item(item.id)
        return HStack(alignment: .center, spacing: 12) {
            Button { tick(item) } label: { TickCircle(ticked: ticked) }
                .buttonStyle(.plain)
                .accessibilityLabel("Bought \(item.name)")
            VStack(alignment: .leading, spacing: 2) {
                TextField("Item", text: nameBinding(item))
                    .font(.body)
                    .focused($focus, equals: .item(item.id))
                    .submitLabel(.done)
                    .strikethrough(ticked)
                    .foregroundStyle(ticked ? .secondary : .primary)
                    .onSubmit { focus = nil }
                if let detail = detail(item) {
                    Text(detail).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if isEditing {
                Button { focus = nil; editing = ShoppingEditorTarget(item: item) } label: {
                    Image(systemName: "info.circle").font(.title3).foregroundStyle(JarvisTheme.cyan)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Details for \(item.name)")
                .transition(.opacity)
            }
            if item.hasPhoto {
                Button { editing = ShoppingEditorTarget(item: item) } label: {
                    ShoppingThumb(model: model, item: item, size: 44)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.vertical, 2)
        .opacity(ticked ? 0.6 : 1)
        .animation(.snappy, value: isEditing)
        .draggable(String(item.id)) {
            Label(item.name, systemImage: "circle")
                .padding(.horizontal, 14).padding(.vertical, 10)
                .background(.regularMaterial, in: .capsule)
        }
        .swipeActions(edge: .trailing) {
            Button("Delete", systemImage: "trash", role: .destructive) {
                Task { await model.unlistShopping(item) }
            }
            Button("Details", systemImage: "info.circle") { editing = ShoppingEditorTarget(item: item) }
                .tint(.gray)
        }
        .contextMenu {
            Button("Details", systemImage: "info.circle") { editing = ShoppingEditorTarget(item: item) }
            Menu("Move to", systemImage: "arrow.right.square") {
                ForEach(allStores.filter { $0.caseInsensitiveCompare(item.store) != .orderedSame }, id: \.self) { store in
                    Button(store) { Task { await model.updateShopping(item) { $0.store = store } } }
                }
                if !item.store.isEmpty {
                    Button("Anywhere") { Task { await model.updateShopping(item) { $0.store = "" } } }
                }
            }
            Button("Remove from List", systemImage: "trash", role: .destructive) {
                Task { await model.unlistShopping(item) }
            }
        }
    }

    /// The New Item row at the end of a section, like Reminders'. Return
    /// adds and keeps the field ready for the next item.
    private func addRow(_ store: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "circle")
                .font(.title2.weight(.light))
                .foregroundStyle(.tertiary)
                .frame(width: 28, height: 28)
            TextField("New Item", text: draftBinding(store))
                .focused($focus, equals: .add(store))
                .submitLabel(.next)
                .onSubmit { submit(store) }
        }
        .padding(.vertical, 2)
    }

    /// Open the New Item row in a section and put the cursor in it.
    private func startAdding(_ store: String, after delay: Int = 60) {
        withAnimation(.snappy) { addingStore = store }
        lastStore = store
        Task {
            try? await Task.sleep(for: .milliseconds(delay))
            focus = .add(store)
        }
    }

    /// Focus left the New Item row: an empty one goes away (after a beat,
    /// since Return hands focus straight back).
    private func closeAddRow(_ store: String) {
        Task {
            try? await Task.sleep(for: .milliseconds(150))
            guard focus != .add(store), addingStore == store else { return }
            let typed = (drafts[store] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if !typed.isEmpty { addNamed(typed, to: store) }
            drafts[store] = ""
            withAnimation(.snappy) { addingStore = nil }
        }
    }

    private func suggestions(for store: String) -> [ShoppingItem] {
        let q = (drafts[store] ?? "").trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return [] }
        return catalog
            .filter { !$0.onList && $0.matches(q) }
            .sorted { $0.matchScore(q) > $1.matchScore(q) }
            .prefix(5).map { $0 }
    }

    private func suggestionRow(_ item: ShoppingItem, store: String) -> some View {
        Button {
            drafts[store] = ""
            add(item, to: store)
        } label: {
            HStack(spacing: 12) {
                ShoppingThumb(model: model, item: item, size: 28)
                VStack(alignment: .leading, spacing: 1) {
                    Text(item.name).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                    let parts = [item.note, item.store].filter { !$0.isEmpty }
                    if !parts.isEmpty {
                        Text(parts.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                Spacer(minLength: 4)
                Image(systemName: "plus.circle").foregroundStyle(JarvisTheme.cyan)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .listRowBackground(JarvisTheme.cyan.opacity(0.06))
        .accessibilityLabel("Add saved \(item.name)")
    }

    private func detail(_ item: ShoppingItem) -> String? {
        let parts = (item.quantity > 1 ? ["×\(item.quantity)"] : []) + (item.note.isEmpty ? [] : [item.note])
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    // MARK: Bottom bar (New Item, Undo)

    private var bottomBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let item = lastBought {
                HStack(spacing: 12) {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(JarvisTheme.success)
                    Text("Got \(item.name)").font(.subheadline.weight(.medium)).lineLimit(1)
                    Spacer(minLength: 8)
                    Button("Undo") {
                        undoTask?.cancel()
                        withAnimation(.snappy) { lastBought = nil }
                        Task { await model.undoBoughtShopping(item) }
                    }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(JarvisTheme.cyan)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .background(.regularMaterial, in: .capsule)
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
            if focus == nil {
                Button { startAdding(sectionStillThere(lastStore)) } label: {
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

    /// The last section used, or the unsorted one if it has emptied.
    private func sectionStillThere(_ store: String) -> String {
        store.isEmpty || list.contains { $0.store.caseInsensitiveCompare(store) == .orderedSame } ? store : ""
    }

    private func draftBinding(_ store: String) -> Binding<String> {
        Binding(get: { drafts[store] ?? "" }, set: { drafts[store] = $0 })
    }

    private func nameBinding(_ item: ShoppingItem) -> Binding<String> {
        Binding(get: { renames[item.id] ?? item.name }, set: { renames[item.id] = $0 })
    }

    /// Focus left a row: save a changed name; an emptied one comes off the
    /// list, the way clearing a reminder deletes it.
    private func commitRename(_ id: Int) {
        guard let text = renames.removeValue(forKey: id),
              let item = list.first(where: { $0.id == id }) else { return }
        let name = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if name.isEmpty {
            Task { await model.unlistShopping(item) }
        } else if name != item.name {
            Task { await model.updateShopping(item) { $0.name = name } }
        }
    }

    /// Return in a New Item row: a saved item of that name goes back on the
    /// list, anything else is a new item in this section. The field stays put.
    private func submit(_ store: String) {
        let text = (drafts[store] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { focus = nil; return }
        drafts[store] = ""
        addNamed(text, to: store)
        focus = .add(store)
    }

    private func addNamed(_ name: String, to store: String) {
        if let saved = catalog.first(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame }) {
            add(saved, to: store)
            return
        }
        var draft = ShoppingItemDraft()
        draft.name = name
        draft.store = store
        Task { _ = await model.saveShopping(draft, editing: nil, photo: .keep) }
    }

    /// A saved item onto the list, in the section it was added from.
    private func add(_ item: ShoppingItem, to store: String) {
        Task {
            if !store.isEmpty && item.store.caseInsensitiveCompare(store) != .orderedSame {
                await model.updateShopping(item) { $0.store = store }
            }
            if !item.onList { await model.listShopping(item) }
        }
    }

    /// Dragged onto another store's section.
    private func move(_ ids: [String], to store: String) -> Bool {
        let items = ids.compactMap { id in list.first { String($0.id) == id } }
            .filter { $0.store.caseInsensitiveCompare(store) != .orderedSame }
        guard !items.isEmpty else { return false }
        for item in items { Task { await model.updateShopping(item) { $0.store = store } } }
        return true
    }

    /// Paste: lines of text become items (bullets and checkboxes stripped,
    /// so a list copied from Notes or a text works); an image opens a new
    /// item with that photo.
    private func paste(_ providers: [NSItemProvider]) {
        let store = sectionStillThere(lastStore)
        guard let provider = providers.first else { return }
        if provider.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
            provider.loadDataRepresentation(forTypeIdentifier: UTType.image.identifier) { data, _ in
                guard let data, let image = UIImage(data: data) else { return }
                Task { @MainActor in editing = ShoppingEditorTarget(item: nil, store: store, image: image) }
            }
            return
        }
        _ = provider.loadObject(ofClass: String.self) { text, _ in
            guard let text else { return }
            let names = ShoppingPaste.lines(text)
            Task { @MainActor in for name in names { addNamed(name, to: store) } }
        }
    }

    /// Check, hold the tick for a beat so the eye sees it, then it leaves.
    private func tick(_ item: ShoppingItem) {
        guard !ticking.contains(item.id) else { return }
        if focus == .item(item.id) { focus = nil }
        ticking.insert(item.id)
        Task {
            try? await Task.sleep(for: .milliseconds(700))
            let done = await model.boughtShopping(item)
            ticking.remove(item.id)
            guard done != nil else { return }
            withAnimation(.snappy) { lastBought = item }
            undoTask?.cancel()
            undoTask = Task {
                try? await Task.sleep(for: .seconds(4))
                if !Task.isCancelled { withAnimation(.snappy) { lastBought = nil } }
            }
        }
    }
}

/// Reminders' completion circle: an outline, then a filled dot in a ring.
struct TickCircle: View {
    let ticked: Bool

    var body: some View {
        ZStack {
            Circle().strokeBorder(ticked ? JarvisTheme.cyan : Color.secondary.opacity(0.6), lineWidth: 1.5)
            if ticked {
                Circle().fill(JarvisTheme.cyan).padding(4).transition(.scale.combined(with: .opacity))
            }
        }
        .frame(width: 24, height: 24)
        .frame(width: 28, height: 28)
        .contentShape(Rectangle().inset(by: -8))
        .animation(.snappy(duration: 0.2), value: ticked)
    }
}

struct ShoppingEditorTarget: Identifiable {
    var item: ShoppingItem?
    var name = ""
    var store = ""
    var image: UIImage?
    var id: String { item.map { "item-\($0.id)" } ?? "new-\(name)" }
}

// MARK: - Saved items

/// Everything ever saved, as a shelf of photos. Tap to edit, the + to put one
/// on the list. Items already on the list wear a check.
struct ShoppingSavedItemsView: View {
    @ObservedObject var model: HubViewModel
    @State private var search = ""
    @State private var editing: ShoppingEditorTarget?

    private var items: [ShoppingItem] {
        let all = model.shopping.value?.catalog ?? []
        let q = search.trimmingCharacters(in: .whitespaces)
        return q.isEmpty ? all.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
                         : all.filter { $0.matches(q) }.sorted { $0.matchScore(q) > $1.matchScore(q) }
    }

    var body: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: 12)], spacing: 16) {
                ForEach(items) { item in
                    Button { editing = ShoppingEditorTarget(item: item) } label: { tile(item) }
                        .buttonStyle(.plain)
                        .contextMenu {
                            if item.onList {
                                Button("Remove from list", systemImage: "minus.circle") {
                                    Task { await model.unlistShopping(item) }
                                }
                            } else {
                                Button("Add to list", systemImage: "cart.badge.plus") {
                                    Task { await model.listShopping(item) }
                                }
                            }
                            Button("Delete", systemImage: "trash", role: .destructive) {
                                Task { await model.deleteShopping(item) }
                            }
                        }
                }
            }
            .padding(16)
        }
        .overlay {
            if items.isEmpty {
                ContentUnavailableView(search.isEmpty ? "No saved items" : "No matches",
                                       systemImage: "photo.on.rectangle.angled",
                                       description: Text(search.isEmpty
                                           ? "Items you add to the shopping list are saved here, photos and all."
                                           : "Nothing saved matches “\(search)”."))
            }
        }
        .background { JarvisBackdrop() }
        .searchable(text: $search, prompt: "Search saved items")
        .navigationTitle("Saved items")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.visible, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("New item", systemImage: "plus") { editing = ShoppingEditorTarget(item: nil) }
            }
        }
        .sheet(item: $editing) { target in
            ShoppingItemEditor(model: model, item: target.item, startName: target.name, listByDefault: false)
        }
        .refreshable { await model.refreshShopping() }
    }

    private func tile(_ item: ShoppingItem) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            ShoppingThumb(model: model, item: item, size: nil)
                .aspectRatio(1, contentMode: .fit)
                .overlay(alignment: .topTrailing) {
                    Button {
                        Task { item.onList ? await model.unlistShopping(item) : await model.listShopping(item) }
                    } label: {
                        Image(systemName: item.onList ? "checkmark.circle.fill" : "plus.circle.fill")
                            .font(.title2)
                            .symbolRenderingMode(.palette)
                            .foregroundStyle(item.onList ? Color.white : JarvisTheme.backgroundDeep,
                                             item.onList ? JarvisTheme.success : JarvisTheme.cyan)
                            .frame(width: 44, height: 44)
                    }
                    .accessibilityLabel(item.onList ? "On the list. Remove \(item.name)" : "Add \(item.name) to the list")
                }
            Text(item.name).font(.caption.weight(.semibold)).lineLimit(2)
            if !item.store.isEmpty {
                Text(item.store).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
        }
    }
}

// MARK: - Editor

struct ShoppingItemEditor: View {
    @ObservedObject var model: HubViewModel
    let item: ShoppingItem?
    var startName = ""
    var startStore = ""
    /// A pasted image to start a new item with.
    var startImage: UIImage?
    var listByDefault = true
    @Environment(\.dismiss) private var dismiss
    @State private var draft = ShoppingItemDraft()
    @State private var photo: ShoppingPhotoChange = .keep
    @State private var preview: UIImage?
    @State private var picked: PhotosPickerItem?
    @State private var cuttingOut = false
    @State private var cutOut = false
    @State private var showingCamera = false
    @State private var saving = false
    @State private var error: String?
    @State private var confirmingDelete = false
    @State private var loaded = false

    private var stores: [String] {
        Array(Set((model.shopping.value?.catalog ?? []).map(\.store).filter { !$0.isEmpty })).sorted()
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    photoArea
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                }
                Section {
                    TextField("Name (e.g. Face lotion)", text: $draft.name)
                        .textInputAutocapitalization(.sentences)
                    TextField("Brand, size, scent…", text: $draft.note)
                    HStack {
                        TextField("Store", text: $draft.store)
                        if !stores.isEmpty {
                            Menu {
                                ForEach(stores, id: \.self) { store in Button(store) { draft.store = store } }
                            } label: {
                                Image(systemName: "chevron.up.chevron.down").foregroundStyle(JarvisTheme.cyan)
                            }
                            .accessibilityLabel("Pick a store")
                        }
                    }
                }
                Section {
                    Stepper("Quantity: \(draft.quantity)", value: $draft.quantity, in: 1...99)
                    Toggle("On the shopping list", isOn: $draft.onList).tint(JarvisTheme.cyan)
                } footer: {
                    if let item, item.timesBought > 0 {
                        Text("Bought \(item.timesBought)×" + (item.lastBought.map {
                            ", last on \($0.formatted(.dateTime.month(.abbreviated).day()))"
                        } ?? "") + ".")
                    }
                }
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote).foregroundStyle(JarvisTheme.amber)
                }
                if item != nil {
                    Section {
                        Button("Delete item", role: .destructive) { confirmingDelete = true }
                    }
                }
            }
            .navigationTitle(item == nil ? "New item" : "Edit item")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if saving { ProgressView() } else {
                        Button("Save", action: save)
                            .disabled(draft.name.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            }
            .confirmationDialog("Delete \(item?.name ?? "item")? It's removed from your saved items too.",
                                isPresented: $confirmingDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) {
                    guard let item else { return }
                    Task { if await model.deleteShopping(item) { dismiss() } }
                }
            }
            .fullScreenCover(isPresented: $showingCamera) {
                CameraPicker { image in use(image) }.ignoresSafeArea()
            }
            .onChange(of: picked) { _, new in
                guard let new else { return }
                Task {
                    if let data = try? await new.loadTransferable(type: Data.self), let image = UIImage(data: data) {
                        use(image)
                    }
                    picked = nil
                }
            }
            .task {
                guard !loaded else { return }
                loaded = true
                if let item {
                    draft = ShoppingItemDraft(item)
                    if item.hasPhoto, let data = await model.shoppingPhoto(item) { preview = UIImage(data: data) }
                } else {
                    draft.name = startName
                    draft.store = startStore
                    draft.onList = listByDefault
                    if let startImage { use(startImage) }
                }
            }
        }
    }

    private var photoArea: some View {
        VStack(spacing: 12) {
            Group {
                if let preview {
                    Image(uiImage: preview).resizable().scaledToFill()
                } else {
                    Image(systemName: "photo.badge.plus")
                        .font(.system(size: 36, weight: .light))
                        .foregroundStyle(JarvisTheme.cyan)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(JarvisTheme.cyan.opacity(0.08))
                }
            }
            .frame(width: 160, height: 160)
            .clipShape(.rect(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(JarvisTheme.border))
            .accessibilityHidden(true)
            HStack(spacing: 10) {
                if UIImagePickerController.isSourceTypeAvailable(.camera) {
                    Button { showingCamera = true } label: { Label("Camera", systemImage: "camera") }
                        .buttonStyle(.bordered)
                }
                PhotosPicker(selection: $picked, matching: .images) {
                    Label("Library", systemImage: "photo.on.rectangle")
                }
                .buttonStyle(.bordered)
                if preview != nil {
                    Button(role: .destructive) {
                        preview = nil
                        photo = .remove
                        cutOut = false
                    } label: { Label("Remove", systemImage: "trash") }
                        .buttonStyle(.bordered)
                        .labelStyle(.iconOnly)
                }
            }
            .font(.subheadline.weight(.medium))
            .tint(JarvisTheme.cyan)
            HStack(spacing: 10) {
                // A screenshot or "Copy image" from Safari, without saving it to Photos first.
                // PasteButton reads the clipboard without iOS's "Allow Paste?" prompt.
                PasteButton(supportedContentTypes: [.image]) { providers in
                    guard let provider = providers.first else { return }
                    provider.loadDataRepresentation(forTypeIdentifier: UTType.image.identifier) { data, _ in
                        guard let data, let image = UIImage(data: data) else { return }
                        Task { @MainActor in use(image) }
                    }
                }
                .labelStyle(.titleAndIcon)
                .buttonBorderShape(.capsule)
                .tint(JarvisTheme.cyan)
                if preview != nil && !cutOut {
                    Button(action: removeBackground) {
                        if cuttingOut {
                            ProgressView().controlSize(.small)
                        } else {
                            Label("Remove background", systemImage: "wand.and.stars")
                        }
                    }
                    .buttonStyle(.bordered)
                    .font(.subheadline.weight(.medium))
                    .tint(JarvisTheme.cyan)
                    .disabled(cuttingOut)
                }
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
    }

    /// Lifts the product off its background (Walmart's white, a shelf) on the
    /// device, and saves it as a transparent PNG.
    private func removeBackground() {
        guard let preview, !cuttingOut else { return }
        cuttingOut = true
        Task {
            defer { cuttingOut = false }
            guard let cut = await BackgroundRemover.cutOut(preview), let png = cut.pngData() else {
                error = "Couldn't find the product in that photo."
                return
            }
            self.preview = cut
            photo = .replace(png)
            cutOut = true
            error = nil
        }
    }

    private func use(_ image: UIImage) {
        let small = image.shrunk(maxSide: 1200)
        preview = small
        cutOut = false
        if let jpeg = small.jpegData(compressionQuality: 0.75) { photo = .replace(jpeg) }
    }

    private func save() {
        var body = draft
        body.name = body.name.trimmingCharacters(in: .whitespacesAndNewlines)
        body.note = body.note.trimmingCharacters(in: .whitespacesAndNewlines)
        body.store = body.store.trimmingCharacters(in: .whitespacesAndNewlines)
        saving = true
        Task {
            defer { saving = false }
            if await model.saveShopping(body, editing: item, photo: photo) != nil {
                dismiss()
            } else {
                error = model.actionError
                model.actionError = nil
            }
        }
    }
}

// MARK: - Photo plumbing

/// A product photo (or a bag placeholder), fetched with the API token and
/// cached in memory by id and version.
struct ShoppingThumb: View {
    static let cache = NSCache<NSString, UIImage>()
    @ObservedObject var model: HubViewModel
    let item: ShoppingItem
    /// nil: fill whatever the parent gives it.
    let size: CGFloat?
    @State private var image: UIImage?

    private var key: NSString { "\(item.id)-\(item.photoVersion)" as NSString }

    var body: some View {
        ZStack {
            if let image = image ?? Self.cache.object(forKey: key), item.hasPhoto {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Image(systemName: "bag")
                    .font(size.map { .system(size: $0 * 0.4) } ?? .system(size: 30, weight: .light))
                    .foregroundStyle(JarvisTheme.cyan.opacity(0.7))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(JarvisTheme.cyan.opacity(0.08))
            }
        }
        .frame(width: size, height: size)
        .frame(maxWidth: size == nil ? .infinity : nil)
        .clipShape(.rect(cornerRadius: size.map { $0 * 0.22 } ?? 16))
        .accessibilityHidden(true)
        .task(id: key) {
            guard item.hasPhoto else { image = nil; return }
            if let cached = Self.cache.object(forKey: key) { image = cached; return }
            if let data = await model.shoppingPhoto(item), let loaded = UIImage(data: data) {
                Self.cache.setObject(loaded, forKey: key)
                image = loaded
            }
        }
    }
}

/// The system camera, for photographing a product on the shelf.
struct CameraPicker: UIViewControllerRepresentable {
    let onImage: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let parent: CameraPicker
        init(_ parent: CameraPicker) { self.parent = parent }

        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let image = info[.originalImage] as? UIImage { parent.onImage(image) }
            parent.dismiss()
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { parent.dismiss() }
    }
}

extension UIImage {
    /// Scaled so the longer side is at most `maxSide` points at scale 1,
    /// orientation baked in.
    func shrunk(maxSide: CGFloat) -> UIImage {
        let longest = max(size.width, size.height)
        let factor = longest > maxSide ? maxSide / longest : 1
        let target = CGSize(width: (size.width * factor).rounded(), height: (size.height * factor).rounded())
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: target, format: format).image { _ in
            draw(in: CGRect(origin: .zero, size: target))
        }
    }
}
