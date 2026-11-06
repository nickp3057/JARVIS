import SwiftUI
import UIKit

/// Keep UIKit's tab bar and persistent page controllers; only a horizontal
/// content pan installs an interactive transition. SwiftUI never redraws the
/// whole root for individual drag samples.
struct PhoneTabs: UIViewControllerRepresentable {
    @Binding var selection: RootView.RootTab
    var pages: [AnyView]
    var reduceMotion: Bool

    private static let tabs: [RootView.RootTab] = [.media, .hub, .home, .chat, .settings]
    private static let symbols = ["play.rectangle.on.rectangle", "square.grid.2x2", "house",
                                  "bubble.left.and.bubble.right", "slider.horizontal.3"]

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIViewController(context: Context) -> UITabBarController {
        makeController(coordinator: context.coordinator)
    }

    func makeController(coordinator: Coordinator) -> UITabBarController {
        let controller = PhoneTabBarController()
        controller.reduceMotion = reduceMotion
        controller.viewControllers = pages.enumerated().map { index, page in
            let host = UIHostingController(rootView: page)
            host.tabBarItem = UITabBarItem(title: Self.tabs[index].title,
                                          image: UIImage(systemName: Self.symbols[index]), tag: index)
            return host
        }
        controller.selectedIndex = Self.tabs.firstIndex(of: selection) ?? 2
        controller.delegate = coordinator
        controller.view.tintColor = UIColor(JarvisTheme.reactor)
        coordinator.controller = controller
        let pan = UIPanGestureRecognizer(target: coordinator, action: #selector(Coordinator.pan(_:)))
        pan.delegate = coordinator
        pan.cancelsTouchesInView = false
        pan.maximumNumberOfTouches = 1
        controller.view.addGestureRecognizer(pan)
        return controller
    }

    func updateUIViewController(_ controller: UITabBarController, context: Context) {
        context.coordinator.parent = self
        for (index, page) in pages.enumerated() {
            (controller.viewControllers?[index] as? UIHostingController<AnyView>)?.rootView = page
        }
        controller.view.tintColor = UIColor(JarvisTheme.reactor)
        (controller as? PhoneTabBarController)?.reduceMotion = reduceMotion
        // A binding update must not fight an interactive transition in flight.
        if !context.coordinator.transitioning, let index = Self.tabs.firstIndex(of: selection),
           controller.selectedIndex != index {
            controller.selectedIndex = index
        }
    }

    final class Coordinator: NSObject, UITabBarControllerDelegate, UIGestureRecognizerDelegate {
        var parent: PhoneTabs
        weak var controller: UITabBarController?
        var transitioning = false
        private var interaction: UIPercentDrivenInteractiveTransition?
        private var origin = 0
        private var target = 0
        private var direction: CGFloat = 1

        init(_ parent: PhoneTabs) { self.parent = parent }

        func gestureRecognizerShouldBegin(_ recognizer: UIGestureRecognizer) -> Bool {
            guard let pan = recognizer as? UIPanGestureRecognizer, let controller,
                  !transitioning, !SwipeLock.active, controller.presentedViewController == nil else { return false }
            let velocity = pan.velocity(in: controller.view)
            let point = pan.location(in: controller.view)
            let next = controller.selectedIndex + (velocity.x < 0 ? 1 : -1)
            guard point.x > 24, !controller.tabBar.frame.contains(point),
                  abs(velocity.x) > 2.5 * abs(velocity.y),
                  Self.valid(next) else { return false }
            // Sliders and other native controls keep their own horizontal pan.
            var hit = controller.view.hitTest(point, with: nil)
            while let view = hit {
                if view is UISlider || view is UISwitch || view is UITextField || view is UITextView {
                    return false
                }
                if let scroll = view as? UIScrollView, scroll.isScrollEnabled,
                   scroll.contentSize.width > scroll.bounds.width + 1 { return false }
                hit = view.superview
            }
            return true
        }

        private static func valid(_ index: Int) -> Bool { PhoneTabs.tabs.indices.contains(index) }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
            // The begin test rejects vertical intent; scrolling and back
            // gestures can still recognize normally inside each page.
            true
        }

        @objc func pan(_ pan: UIPanGestureRecognizer) {
            guard let controller else { return }
            handlePan(state: pan.state, translation: pan.translation(in: controller.view).x,
                      velocity: pan.velocity(in: controller.view).x, width: controller.view.bounds.width)
        }

        func handlePan(state: UIGestureRecognizer.State, translation: CGFloat, velocity: CGFloat, width: CGFloat) {
            guard let controller else { return }
            let width = max(width, 1)
            switch state {
            case .began:
                origin = controller.selectedIndex
                direction = velocity < 0 ? 1 : -1
                target = origin + Int(direction)
                guard Self.valid(target) else { return }
                transitioning = true
                if !parent.reduceMotion {
                    let driver = UIPercentDrivenInteractiveTransition()
                    driver.completionCurve = .easeOut
                    interaction = driver
                    controller.selectedIndex = target
                }
            case .changed:
                interaction?.update(min(1, max(0, -translation * direction / width)))
            case .ended, .cancelled, .failed:
                guard transitioning else { return }
                let distance = -translation * direction
                let finish = state == .ended && SwipeCompletion.shouldFinish(
                    distance: distance, velocity: -velocity * direction, width: width)
                if let interaction {
                    if finish { interaction.finish() } else { interaction.cancel() }
                } else {
                    controller.selectedIndex = finish ? target : origin
                    parent.selection = PhoneTabs.tabs[controller.selectedIndex]
                    transitioning = false
                }
            default: break
            }
        }

        func tabBarController(_ tabBarController: UITabBarController, shouldSelect viewController: UIViewController) -> Bool {
            !transitioning
        }

        func tabBarController(_ tabBarController: UITabBarController, didSelect viewController: UIViewController) {
            if !transitioning { parent.selection = PhoneTabs.tabs[tabBarController.selectedIndex] }
        }

        func tabBarController(_ tabBarController: UITabBarController,
                              animationControllerForTransitionFrom fromVC: UIViewController,
                              to toVC: UIViewController) -> UIViewControllerAnimatedTransitioning? {
            guard let interaction else { return nil }
            return TabSlide(direction: direction) { [weak self, interaction] completed in
                guard let self else { return }
                // Retain the percent driver until UIKit finishes settling.
                _ = interaction
                self.interaction = nil
                self.transitioning = false
                self.controller?.selectedIndex = completed ? self.target : self.origin
                self.parent.selection = PhoneTabs.tabs[completed ? self.target : self.origin]
            }
        }

        func tabBarController(_ tabBarController: UITabBarController,
                              interactionControllerFor animationController: UIViewControllerAnimatedTransitioning)
            -> UIViewControllerInteractiveTransitioning? { interaction }
    }
}

/// The tab shell stays full height while each hosting controller handles its
/// own keyboard avoidance. Hide the tab bar rather than lifting it into Chat.
final class PhoneTabBarController: UITabBarController {
    var reduceMotion = false
    private(set) var keyboardVisible = false

    override func viewDidLoad() {
        super.viewDidLoad()
        NotificationCenter.default.addObserver(self, selector: #selector(keyboardChanged(_:)),
            name: UIResponder.keyboardWillChangeFrameNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(keyboardHidden),
            name: UIResponder.keyboardWillHideNotification, object: nil)
    }

    deinit { NotificationCenter.default.removeObserver(self) }

    @objc private func keyboardChanged(_ notification: Notification) {
        guard let window = view.window,
              let frame = notification.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect else { return }
        let local = view.convert(window.convert(frame, from: window.screen.coordinateSpace), from: window)
        setKeyboardVisible(view.bounds.intersection(local).height > view.safeAreaInsets.bottom)
    }

    @objc private func keyboardHidden() { setKeyboardVisible(false) }

    func setKeyboardVisible(_ visible: Bool) {
        guard visible != keyboardVisible else { return }
        keyboardVisible = visible
        if #available(iOS 18.0, *) {
            setTabBarHidden(visible, animated: !reduceMotion)
        } else {
            tabBar.isHidden = visible
        }
    }
}

/// Projected travel accepts a short flick, but a reversal cancels instead of
/// committing just because the finger once crossed the halfway point.
enum SwipeCompletion {
    static func shouldFinish(distance: CGFloat, velocity: CGFloat, width: CGFloat) -> Bool {
        distance > 0 && distance + velocity * 0.18 > width * 0.33
    }
}

private final class TabSlide: NSObject, UIViewControllerAnimatedTransitioning {
    let direction: CGFloat
    let completion: (Bool) -> Void
    init(direction: CGFloat, completion: @escaping (Bool) -> Void) {
        self.direction = direction
        self.completion = completion
    }

    func transitionDuration(using transitionContext: UIViewControllerContextTransitioning?) -> TimeInterval { 0.3 }

    func animateTransition(using context: UIViewControllerContextTransitioning) {
        guard let from = context.view(forKey: .from), let to = context.view(forKey: .to),
              let destination = context.viewController(forKey: .to) else {
            context.completeTransition(false)
            completion(false)
            return
        }
        let container = context.containerView
        to.frame = context.finalFrame(for: destination)
        let width = container.bounds.width
        to.transform = CGAffineTransform(translationX: direction * width, y: 0)
        container.addSubview(to)
        UIView.animate(withDuration: transitionDuration(using: context), delay: 0, options: .curveLinear) {
            from.transform = CGAffineTransform(translationX: -self.direction * width, y: 0)
            to.transform = .identity
        } completion: { _ in
            let completed = !context.transitionWasCancelled
            from.transform = .identity
            to.transform = .identity
            if !completed { to.removeFromSuperview() }
            context.completeTransition(completed)
            self.completion(completed)
        }
    }
}
