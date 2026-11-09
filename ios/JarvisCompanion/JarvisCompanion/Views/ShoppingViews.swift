import PhotosUI
import SwiftUI
import UIKit

/// Lists pushed from Home's Lists row.
enum ListRoute: Hashable {
    case todo(adding: Bool)
    case shopping(adding: Bool)
    case savedItems
}

// MARK: - Shopping list

/// What to buy, grouped by store. Typing in the add field suggests saved
/// items (with their photos); tapping one puts it on the list. Ticking an
/// item off takes it off the list (it stays saved), with an Undo.
struct ShoppingListView: View {
    @ObservedObject var model: HubViewModel
    var startAdding = false
    @State private var query = ""
    @FocusState private var adding: Bool
    @State private var editing: ShoppingEditorTarget?
    @State private var lastBought: ShoppingItem?
    @State private var undoTask: Task<Void, Never>?
    /// Rows ticked a moment ago: drawn checked while they leave.
    @State private var ticking: Set<Int> = []

    private var list: [ShoppingItem] { model.shopping.value?.list ?? [] }

    /// Saved items matching what's typed, best first, items already on the
    /// list last (tapping one of those just bumps nothing).
    private var suggestions: [ShoppingItem] {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return [] }
        return (model.shopping.value?.catalog ?? [])
            .filter { $0.matches(q) }
            .sorted { ($0.onList ? 1 : 0, -$0.matchScore(q)) < ($1.onList ? 1 : 0, -$1.matchScore(q)) }
            .prefix(8).map { $0 }
    }

    var body: some View {
        List {
            Section {
                addField
                if !query.trimmingCharacters(in: .whitespaces).isEmpty {
                    ForEach(suggestions) { suggestionRow($0) }
                    Button { newItem() } label: {
                        Label("New item “\(query.trimmingCharacters(in: .whitespaces))”…", systemImage: "plus.square.dashed")
                            .font(.subheadline.weight(.medium))
                    }
                    .foregroundStyle(JarvisTheme.cyan)
                }
            }

            if query.isEmpty {
                ForEach(ShoppingGrouping.sections(list)) { section in
                    Section(section.title) {
                        ForEach(section.items) { listRow($0) }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background { JarvisBackdrop() }
        .overlay {
            if list.isEmpty && query.isEmpty {
                if let error = model.shopping.failureText, model.shopping.value == nil {
                    ContentUnavailableView("Couldn’t load the list", systemImage: "wifi.exclamationmark",
                                           description: Text(error))
                } else if model.shopping.value != nil {
                    ContentUnavailableView {
                        Label("Nothing to buy", systemImage: "cart")
                    } description: {
                        Text("Type above, or tell JARVIS: “add face lotion to my shopping list.”")
                    }
                    .padding(.top, 80)
                } else {
                    ProgressView()
                }
            }
        }
        .overlay(alignment: .bottom) { undoBar }
        .animation(.snappy, value: list.map(\.id))
        .animation(.snappy, value: query.isEmpty)
        .navigationTitle("Shopping")
        .toolbar(.visible, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink(value: ListRoute.savedItems) {
                    Label("Saved items", systemImage: "square.grid.2x2")
                }
            }
        }
        .sheet(item: $editing) { target in
            ShoppingItemEditor(model: model, item: target.item, startName: target.name)
        }
        .refreshable { await model.refreshShopping() }
        .task { await model.refreshShopping() }
        .onAppear { if startAdding { adding = true } }
        .sensoryFeedback(.success, trigger: lastBought?.id)
    }

    private var addField: some View {
        HStack(spacing: 12) {
            Image(systemName: "plus.circle.fill")
                .font(.title3)
                .foregroundStyle(JarvisTheme.cyan)
                .frame(width: 44)
            TextField("Add an item", text: $query)
                .focused($adding)
                .submitLabel(.done)
                .autocorrectionDisabled()
                .onSubmit(submit)
            if !query.isEmpty {
                Button { query = "" } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.tertiary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Clear")
            }
        }
        .padding(.vertical, 2)
    }

    private func suggestionRow(_ item: ShoppingItem) -> some View {
        Button {
            Task { await model.listShopping(item) }
            query = ""
        } label: {
            HStack(spacing: 12) {
                ShoppingThumb(model: model, item: item, size: 44)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.name).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                    detailText(item, showsBought: true)
                }
                Spacer(minLength: 4)
                if item.onList {
                    Text("On list").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                } else {
                    Image(systemName: "plus.circle").font(.title3).foregroundStyle(JarvisTheme.cyan)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(item.onList)
        .accessibilityLabel(item.onList ? "\(item.name), already on the list" : "Add \(item.name)")
    }

    private func listRow(_ item: ShoppingItem) -> some View {
        let ticked = ticking.contains(item.id)
        return HStack(spacing: 12) {
            Button { tick(item) } label: {
                Image(systemName: ticked ? "checkmark.circle.fill" : "circle")
                    .font(.title2)
                    .foregroundStyle(ticked ? JarvisTheme.success : JarvisTheme.cyan)
                    .contentTransition(.symbolEffect(.replace))
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Bought \(item.name)")
            Button { editing = ShoppingEditorTarget(item: item) } label: {
                HStack(spacing: 12) {
                    ShoppingThumb(model: model, item: item, size: 44)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.quantity > 1 ? "\(item.name)  ×\(item.quantity)" : item.name)
                            .font(.body.weight(.medium))
                            .strikethrough(ticked)
                            .foregroundStyle(ticked ? .secondary : .primary)
                        if !item.note.isEmpty {
                            Text(item.note).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .listRowInsets(EdgeInsets(top: 4, leading: 4, bottom: 4, trailing: 16))
        .swipeActions(edge: .leading) {
            Button("Bought", systemImage: "checkmark") { tick(item) }.tint(JarvisTheme.success)
        }
        .swipeActions(edge: .trailing) {
            Button("Remove", systemImage: "minus.circle") {
                Task { await model.unlistShopping(item) }
            }
            .tint(.gray)
        }
        .contextMenu {
            Button("Edit", systemImage: "pencil") { editing = ShoppingEditorTarget(item: item) }
            Button("Remove from list", systemImage: "minus.circle") { Task { await model.unlistShopping(item) } }
        }
    }

    @ViewBuilder
    private func detailText(_ item: ShoppingItem, showsBought: Bool) -> some View {
        let parts = [item.note, item.store].filter { !$0.isEmpty }
            + (showsBought && item.timesBought > 0 ? ["bought \(item.timesBought)×"] : [])
        if !parts.isEmpty {
            Text(parts.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary).lineLimit(1)
        }
    }

    @ViewBuilder private var undoBar: some View {
        if let item = lastBought {
            HStack(spacing: 12) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(JarvisTheme.success)
                Text("Got \(item.name)").font(.subheadline.weight(.medium)).lineLimit(1)
                Spacer(minLength: 8)
                Button("Undo") {
                    undoTask?.cancel()
                    lastBought = nil
                    Task { await model.undoBoughtShopping(item) }
                }
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(JarvisTheme.cyan)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(.regularMaterial, in: .capsule)
            .padding(.horizontal, 16)
            .padding(.bottom, 12)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    /// Check, hold the tick for a beat so the eye sees it, then it leaves.
    private func tick(_ item: ShoppingItem) {
        guard !ticking.contains(item.id) else { return }
        ticking.insert(item.id)
        Task {
            try? await Task.sleep(for: .milliseconds(450))
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

    /// Return: an exact saved name goes on the list; anything else becomes
    /// a new item right away (photo and store can be added later).
    private func submit() {
        let text = query.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return }
        query = ""
        adding = true
        if let saved = model.shopping.value?.catalog.first(where: { $0.name.caseInsensitiveCompare(text) == .orderedSame }) {
            if !saved.onList { Task { await model.listShopping(saved) } }
            return
        }
        var draft = ShoppingItemDraft()
        draft.name = text
        Task { _ = await model.saveShopping(draft, editing: nil, photo: .keep) }
    }

    private func newItem() {
        let name = query.trimmingCharacters(in: .whitespaces)
        query = ""
        editing = ShoppingEditorTarget(item: nil, name: name)
    }
}

struct ShoppingEditorTarget: Identifiable {
    var item: ShoppingItem?
    var name = ""
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
    var listByDefault = true
    @Environment(\.dismiss) private var dismiss
    @State private var draft = ShoppingItemDraft()
    @State private var photo: ShoppingPhotoChange = .keep
    @State private var preview: UIImage?
    @State private var picked: PhotosPickerItem?
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
                    draft.onList = listByDefault
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
                    } label: { Label("Remove", systemImage: "trash") }
                        .buttonStyle(.bordered)
                        .labelStyle(.iconOnly)
                }
            }
            .font(.subheadline.weight(.medium))
            .tint(JarvisTheme.cyan)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
    }

    private func use(_ image: UIImage) {
        let small = image.shrunk(maxSide: 1200)
        preview = small
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
