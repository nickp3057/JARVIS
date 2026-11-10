import XCTest
import SwiftUI
import UIKit
@testable import JarvisCompanion

@MainActor
final class PhoneTabsTests: XCTestCase {
    func testSlowDragFlickAndReversal() {
        XCTAssertTrue(SwipeCompletion.shouldFinish(distance: 160, velocity: 0, width: 390))
        XCTAssertTrue(SwipeCompletion.shouldFinish(distance: 40, velocity: 900, width: 390))
        XCTAssertFalse(SwipeCompletion.shouldFinish(distance: 40, velocity: 0, width: 390))
        XCTAssertFalse(SwipeCompletion.shouldFinish(distance: 160, velocity: -900, width: 390))
        XCTAssertFalse(SwipeCompletion.shouldFinish(distance: -10, velocity: 900, width: 390))
    }

    func testNativeInteractiveCompletionAndCancellationPreservePages() async throws {
        var selected = RootView.RootTab.home
        let tabs = PhoneTabs(selection: Binding(get: { selected }, set: { selected = $0 }),
                             pages: (0..<5).map { AnyView(Text("Page \($0)")) }, reduceMotion: false)
        let coordinator = tabs.makeCoordinator()
        let controller = tabs.makeController(coordinator: coordinator)
        let hosts = controller.viewControllers!
        let scene = try XCTUnwrap(UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first)
        let window = UIWindow(windowScene: scene)
        window.frame = CGRect(x: 0, y: 0, width: 390, height: 844)
        window.rootViewController = controller
        window.makeKeyAndVisible()
        controller.view.layoutIfNeeded()
        defer { window.isHidden = true; window.rootViewController = nil }
        try await Task.sleep(for: .milliseconds(100))

        coordinator.handlePan(state: .began, translation: 0, velocity: -600, width: 390)
        try await Task.sleep(for: .milliseconds(30))
        coordinator.handlePan(state: .changed, translation: -180, velocity: -600, width: 390)
        coordinator.handlePan(state: .ended, translation: -180, velocity: -600, width: 390)
        try await Task.sleep(for: .milliseconds(600))
        XCTAssertEqual(selected, .chat)
        XCTAssertEqual(controller.selectedIndex, 3)
        XCTAssertFalse(coordinator.transitioning)

        coordinator.handlePan(state: .began, translation: 0, velocity: 600, width: 390)
        try await Task.sleep(for: .milliseconds(30))
        coordinator.handlePan(state: .changed, translation: 80, velocity: 0, width: 390)
        coordinator.handlePan(state: .cancelled, translation: 80, velocity: 0, width: 390)
        try await Task.sleep(for: .milliseconds(600))
        XCTAssertEqual(selected, .chat)
        XCTAssertEqual(controller.selectedIndex, 3)
        XCTAssertFalse(coordinator.transitioning)
        for (index, host) in hosts.enumerated() {
            XCTAssertTrue(controller.viewControllers![index] === host)
            XCTAssertEqual(host.view.transform, .identity)
        }
    }

    func testKeyboardKeepsTabShellFullHeightAndOnlyLiftsInput() async throws {
        let pages = (0..<5).map { index in
            AnyView(VStack {
                Spacer()
                if index == 3 { TextField("Message", text: .constant("")) }
                else { Text("Page \(index)") }
            }.padding())
        }
        let tabs = PhoneTabs(selection: .constant(.chat), pages: pages, reduceMotion: true)
        let root = UIHostingController(rootView: tabs.ignoresSafeArea())
        let scene = try XCTUnwrap(UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first)
        let window = UIWindow(windowScene: scene)
        window.rootViewController = root
        window.makeKeyAndVisible()
        defer { window.endEditing(true); window.isHidden = true; window.rootViewController = nil }
        try await Task.sleep(for: .milliseconds(300))
        func tabController(_ parent: UIViewController) -> PhoneTabBarController? {
            if let tabs = parent as? PhoneTabBarController { return tabs }
            return parent.children.compactMap { tabController($0) }.first
        }
        func input(_ parent: UIView) -> UITextField? {
            if let field = parent as? UITextField { return field }
            return parent.subviews.compactMap { input($0) }.first
        }
        let controller = try XCTUnwrap(tabController(root))
        let field = try XCTUnwrap(input(controller.view))
        let originalHeight = controller.view.bounds.height
        let originalInputBottom = field.convert(field.bounds, to: window).maxY
        XCTAssertTrue(field.becomeFirstResponder())
        try await Task.sleep(for: .milliseconds(800))
        XCTAssertTrue(controller.keyboardVisible)
        if #available(iOS 18.0, *) { XCTAssertTrue(controller.isTabBarHidden) }
        XCTAssertEqual(controller.view.bounds.height, originalHeight, accuracy: 1)
        XCTAssertLessThan(field.convert(field.bounds, to: window).maxY, originalInputBottom - 100)
        let attachment = XCTAttachment(image: UIGraphicsImageRenderer(bounds: window.bounds).image { _ in
            window.drawHierarchy(in: window.bounds, afterScreenUpdates: true)
        })
        attachment.name = "Keyboard — input above keyboard without tab row"
        attachment.lifetime = .keepAlways
        add(attachment)
        field.resignFirstResponder()
        try await Task.sleep(for: .milliseconds(500))
        XCTAssertFalse(controller.keyboardVisible)
        if #available(iOS 18.0, *) { XCTAssertFalse(controller.isTabBarHidden) }
    }

    func testReduceMotionSwitchesOnlyAfterReleaseAndRespectsEdges() {
        var selected = RootView.RootTab.settings
        let tabs = PhoneTabs(selection: Binding(get: { selected }, set: { selected = $0 }),
                             pages: (0..<5).map { AnyView(Text("Page \($0)")) }, reduceMotion: true)
        let coordinator = tabs.makeCoordinator()
        let controller = tabs.makeController(coordinator: coordinator)
        coordinator.handlePan(state: .began, translation: 0, velocity: -600, width: 390)
        XCTAssertFalse(coordinator.transitioning) // no tab beyond Settings
        coordinator.handlePan(state: .began, translation: 0, velocity: 600, width: 390)
        XCTAssertEqual(selected, .settings)
        coordinator.handlePan(state: .ended, translation: 160, velocity: 0, width: 390)
        XCTAssertEqual(selected, .chat)
        XCTAssertEqual(controller.selectedIndex, 3)
    }
}

extension PhoneTabsTests {
    /// Home sits in the middle; the tab order is the swipe order.
    func testHomeIsTheMiddleTab() {
        XCTAssertEqual(PhoneTabs.tabs, [.media, .hub, .home, .chat, .settings])
        XCTAssertEqual(RootView.RootTab.hub.title, "Control")
    }
}
