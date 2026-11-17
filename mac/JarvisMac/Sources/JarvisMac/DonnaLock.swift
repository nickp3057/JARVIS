import AppKit
import LocalAuthentication
import SwiftUI

/// Touch ID (or the Mac's login password) before Donna opens. Her view, and so
/// her first request to Core, is only built once unlocked.
///
/// Relocks when you leave the Donna section (RootView rebuilds this view), when
/// the screen locks or the Mac sleeps, and after JARVIS has sat in the
/// background for `idleRelock`.
struct DonnaLock<Content: View>: View {
    @ViewBuilder var content: () -> Content
    @State private var unlocked = false
    @State private var checking = false
    @State private var failure: String?
    @State private var resignedAt: Date?

    private static var idleRelock: TimeInterval { 5 * 60 }

    var body: some View {
        Group {
            if unlocked { content() } else { lockScreen }
        }
        .task { await unlock() }
        .onReceive(NSWorkspace.shared.notificationCenter.publisher(for: NSWorkspace.willSleepNotification)) { _ in lock() }
        .onReceive(NSWorkspace.shared.notificationCenter.publisher(for: NSWorkspace.screensDidSleepNotification)) { _ in lock() }
        .onReceive(DistributedNotificationCenter.default().publisher(for: .init("com.apple.screenIsLocked"))) { _ in lock() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didResignActiveNotification)) { _ in
            // The Touch ID panel itself can take focus; don't count that.
            if !checking { resignedAt = Date() }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            guard let resignedAt else { return }
            self.resignedAt = nil
            if unlocked, Date().timeIntervalSince(resignedAt) > Self.idleRelock {
                lock()
                Task { await unlock() }
            }
        }
    }

    private func lock() {
        unlocked = false
        failure = nil
    }

    private var lockScreen: some View {
        ZStack {
            LinearGradient(colors: [DonnaTheme.backgroundTop, DonnaTheme.backgroundBottom],
                           startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()
            VStack(spacing: 16) {
                Image(systemName: "lock.fill")
                    .font(.system(size: 40))
                    .foregroundStyle(DonnaTheme.rose)
                Text("Donna is locked").font(.title3.weight(.semibold))
                if let failure {
                    Text(failure).font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center).frame(maxWidth: 320)
                }
                Button {
                    Task { await unlock() }
                } label: {
                    Label(Self.hasTouchID ? "Unlock with Touch ID" : "Unlock with Password",
                          systemImage: Self.hasTouchID ? "touchid" : "lock.open")
                }
                .buttonStyle(.borderedProminent)
                .tint(DonnaTheme.rose)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)
                .disabled(checking)
            }
        }
    }

    private func unlock() async {
        guard !unlocked, !checking else { return }
        checking = true
        defer { checking = false }
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            failure = error?.localizedDescription ?? "This Mac can't verify you right now."
            return
        }
        do {
            unlocked = try await context.evaluatePolicy(.deviceOwnerAuthentication,
                                                        localizedReason: "unlock Donna")
            failure = nil
        } catch let error as LAError where [.userCancel, .appCancel, .systemCancel].contains(error.code) {
            failure = nil
        } catch {
            failure = error.localizedDescription
        }
    }

    private static var hasTouchID: Bool {
        let context = LAContext()
        return context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: nil)
            && context.biometryType == .touchID
    }
}
