import XCTest
@testable import JarvisCompanion

/// The poll republishes a section only when it changed (HubViewModel.same).
final class HubSectionTests: XCTestCase {
    private typealias S = HubViewModel.Section

    func testEqualValuesAreTheSame() {
        var a = S<[String]>(); a.value = ["x"]
        var b = a; b.value = ["x"]
        XCTAssertTrue(HubViewModel.same(a, b))
    }

    func testChangedValueErrorOrLoadingIsNotTheSame() {
        var a = S<[String]>(); a.value = ["x"]
        var b = a; b.value = ["y"]
        XCTAssertFalse(HubViewModel.same(a, b))
        b = a; b.loadError = "offline"
        XCTAssertFalse(HubViewModel.same(a, b))
        b = a; b.isLoading = true
        XCTAssertFalse(HubViewModel.same(a, b))
        b = a; b.value = nil
        XCTAssertFalse(HubViewModel.same(a, b))
    }

    func testNonEquatableValuesAlwaysCountAsChanged() {
        final class Box {}
        var a = S<Box>(); a.value = Box()
        XCTAssertFalse(HubViewModel.same(a, a))
    }
}
