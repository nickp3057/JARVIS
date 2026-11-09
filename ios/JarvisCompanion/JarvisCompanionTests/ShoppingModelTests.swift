import XCTest
@testable import JarvisCompanion

final class ShoppingModelTests: XCTestCase {
    private func item(_ id: Int, _ name: String, note: String = "", store: String = "", bought: Int = 0) -> ShoppingItem {
        ShoppingItem(id: id, name: name, note: note, store: store, quantity: 1, onList: true, hasPhoto: false,
                     photoVersion: 0, timesBought: bought, addedAt: nil, lastBoughtAt: nil)
    }

    func testDecodesServerPayload() throws {
        let json = """
        {"list": [], "catalog": [{"id": 3, "name": "Face lotion", "note": "CeraVe PM", "store": "Walmart",
          "quantity": 2, "on_list": false, "has_photo": true, "photo_version": 4, "times_bought": 5,
          "added_at": null, "last_bought_at": 1762700000.0}]}
        """
        let overview = try JSONDecoder().decode(ShoppingOverview.self, from: Data(json.utf8))
        let lotion = try XCTUnwrap(overview.catalog.first)
        XCTAssertEqual(lotion.photoVersion, 4)
        XCTAssertTrue(lotion.hasPhoto)
        XCTAssertFalse(lotion.onList)
        XCTAssertNotNil(lotion.lastBought)
    }

    func testEveryWordMustPrefixAWord() {
        let lotion = item(1, "Face lotion", note: "CeraVe PM", store: "Walmart")
        XCTAssertTrue(lotion.matches("face lot"))
        XCTAssertTrue(lotion.matches("cerave"))
        XCTAssertTrue(lotion.matches("walm"))
        XCTAssertFalse(lotion.matches("body lotion"))
        XCTAssertFalse(lotion.matches("  "))
        XCTAssertGreaterThan(lotion.matchScore("face lotion"), item(2, "Lotion, face").matchScore("face lotion"))
    }

    func testSectionsByStoreWithAnywhereLast() {
        let sections = ShoppingGrouping.sections([
            item(1, "Batteries"), item(2, "Eggs", store: "Walmart"), item(3, "Coffee", store: "Costco"),
            item(4, "Milk", store: "Walmart"),
        ])
        XCTAssertEqual(sections.map(\.title), ["Costco", "Walmart", "Anywhere"])
        XCTAssertEqual(sections[1].items.map(\.name), ["Eggs", "Milk"])
    }
}
