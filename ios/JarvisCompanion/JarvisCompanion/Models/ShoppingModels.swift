import Foundation

/// One saved product. It stays in the catalog forever; `onList` says whether
/// it's on the shopping list right now (brain/shopping.py).
struct ShoppingItem: Codable, Identifiable, Hashable {
    let id: Int
    var name: String
    var note: String
    var store: String
    var quantity: Int
    var onList: Bool
    var hasPhoto: Bool
    var photoVersion: Int
    var timesBought: Int
    var addedAt: Double?
    var lastBoughtAt: Double?

    enum CodingKeys: String, CodingKey {
        case id, name, note, store, quantity
        case onList = "on_list"
        case hasPhoto = "has_photo"
        case photoVersion = "photo_version"
        case timesBought = "times_bought"
        case addedAt = "added_at"
        case lastBoughtAt = "last_bought_at"
    }

    var lastBought: Date? { lastBoughtAt.map { Date(timeIntervalSince1970: $0) } }

    /// Same rule as the server's suggester: every typed word must start a
    /// word in the name, note or store. Used to filter while typing, so the
    /// suggestions appear without a round trip.
    func matches(_ query: String) -> Bool {
        let wanted = Self.words(query)
        guard !wanted.isEmpty else { return false }
        let have = Self.words("\(name) \(note) \(store)")
        return wanted.allSatisfy { w in have.contains { $0.hasPrefix(w) } }
    }

    func matchScore(_ query: String) -> Double {
        guard matches(query) else { return 0 }
        let q = query.lowercased().trimmingCharacters(in: .whitespaces)
        let n = name.lowercased()
        var score = 1 + 0.1 * Double(min(timesBought, 20))
        if n == q { score += 10 } else if n.hasPrefix(q) { score += 4 } else if n.contains(q) { score += 2 }
        return score
    }

    static func words(_ text: String) -> [String] {
        text.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init)
    }
}

struct ShoppingOverview: Decodable {
    var list: [ShoppingItem]
    var catalog: [ShoppingItem]
}

struct ShoppingItemDraft: Encodable {
    var name = ""
    var note = ""
    var store = ""
    var quantity = 1
    var onList = true

    init() {}

    init(_ item: ShoppingItem) {
        name = item.name
        note = item.note
        store = item.store
        quantity = item.quantity
        onList = item.onList
    }

    enum CodingKeys: String, CodingKey {
        case name, note, store, quantity
        case onList = "on_list"
    }
}

/// The list, grouped the way you walk a store: one section per store
/// (in the server's order), "Anywhere" last.
enum ShoppingGrouping {
    struct Section: Identifiable {
        let store: String
        let items: [ShoppingItem]
        var id: String { store }
        var title: String { store.isEmpty ? "Anywhere" : store }
    }

    static func sections(_ items: [ShoppingItem]) -> [Section] {
        var order: [String] = []
        var groups: [String: [ShoppingItem]] = [:]
        for item in items {
            let key = item.store.trimmingCharacters(in: .whitespaces)
            if groups[key] == nil { order.append(key) }
            groups[key, default: []].append(item)
        }
        order.sort { a, b in a.isEmpty != b.isEmpty ? !a.isEmpty : a.localizedCaseInsensitiveCompare(b) == .orderedAscending }
        return order.map { Section(store: $0, items: groups[$0] ?? []) }
    }
}

/// Pasted text as item names: one per line, list bullets, numbering and
/// checkboxes stripped, blanks and repeats dropped.
enum ShoppingPaste {
    static func lines(_ text: String) -> [String] {
        var seen = Set<String>()
        return text.components(separatedBy: .newlines).compactMap { raw in
            var line = raw.trimmingCharacters(in: .whitespaces)
            if let range = line.range(of: #"^(\[[ xX]?\]\s*|[-*•◦▪☐☑✓✔]\s*|\d+[.)]\s+)"#, options: .regularExpression) {
                line.removeSubrange(range)
            }
            line = line.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, seen.insert(line.lowercased()).inserted else { return nil }
            return String(line.prefix(120))
        }
    }
}

/// What the editor does to an item's photo on save.
enum ShoppingPhotoChange {
    case keep
    case remove
    case replace(Data)
}
