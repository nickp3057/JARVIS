import SwiftUI

/// Search Radarr/Sonarr and request a title without going through chat.
/// Tapping a result asks the server to open a proposal; the add happens only
/// after "Add" in the confirmation dialog (the server's two-step gate).
struct MediaRequestView: View {
    @StateObject private var model: MediaRequestModel
    @Environment(\.dismiss) private var dismiss

    init(makeClient: @escaping () throws -> JarvisAPIClient) {
        _model = StateObject(wrappedValue: MediaRequestModel(makeClient: makeClient))
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Picker("Type", selection: $model.kind) {
                        ForEach(MediaRequestModel.Kind.allCases) { Text($0.label).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    HStack {
                        TextField("Title, optionally with year", text: $model.query)
                            .textInputAutocapitalization(.words)
                            .autocorrectionDisabled()
                            .submitLabel(.search)
                            .onSubmit { model.search() }
                        if model.isSearching {
                            ProgressView()
                        } else {
                            Button("Search", systemImage: "magnifyingglass") { model.search() }
                                .labelStyle(.iconOnly)
                                .disabled(model.query.trimmingCharacters(in: .whitespaces).isEmpty)
                        }
                    }
                    if let notice = model.notice {
                        Label(notice, systemImage: model.noticeIsError ? "exclamationmark.triangle" : "checkmark.circle")
                            .font(.footnote)
                            .foregroundStyle(model.noticeIsError ? Color.orange : JarvisTheme.cyan)
                    }
                }
                Section {
                    ForEach(model.results) { item in
                        Button { model.request(item) } label: { row(item) }
                            .disabled(item.inLibrary || model.busyID != nil)
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background { JarvisBackdrop() }
            .navigationTitle("Request media")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } }
            }
            .onChange(of: model.kind) { model.search() }
            .confirmationDialog(
                model.proposal?.title ?? "Add",
                isPresented: Binding(get: { model.proposal != nil }, set: { if !$0 { model.proposal = nil } }),
                titleVisibility: .visible,
                presenting: model.proposal
            ) { proposal in
                Button("Add") { model.confirm(proposal) }
                Button("Cancel", role: .cancel) { model.proposal = nil }
            } message: { proposal in
                Text("Will \(proposal.description).")
            }
        }
    }

    private func row(_ item: MediaSearchResult) -> some View {
        HStack(alignment: .top, spacing: 12) {
            AsyncImage(url: item.poster.flatMap { URL(string: $0) }) { image in
                image.resizable().scaledToFill()
            } placeholder: {
                Image(systemName: item.kind == "movie" ? "film" : "tv")
                    .foregroundStyle(.secondary)
            }
            .frame(width: 46, height: 68)
            .background(JarvisTheme.backgroundDeep)
            .clipShape(.rect(cornerRadius: 6))

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(item.title).font(.subheadline.weight(.semibold)).foregroundStyle(.primary)
                    if let year = item.year { Text(String(year)).font(.caption).foregroundStyle(.secondary) }
                    Spacer(minLength: 4)
                    if model.busyID == item.id { ProgressView() }
                }
                if item.inLibrary {
                    Text("Already in your library").font(.caption).foregroundStyle(JarvisTheme.cyan)
                }
                if !item.overview.isEmpty {
                    Text(item.overview).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                }
            }
        }
        .padding(.vertical, 2)
    }
}
