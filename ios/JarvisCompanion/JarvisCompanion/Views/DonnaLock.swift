import LocalAuthentication
import SwiftUI

/// Face ID (or the device passcode) before Donna opens. The hidden door only
/// hides her; this is the lock. Donna's view, and so her first request to
/// Core, is only built once the owner has unlocked.
///
/// Relocks whenever the app goes to the background, and covers her while the
/// app is inactive so the app switcher's snapshot never shows the chat.
struct DonnaLock<Content: View>: View {
    @ViewBuilder var content: () -> Content
    @ObservedObject private var presenter = DonnaPresenter.shared
    @Environment(\.scenePhase) private var scenePhase
    @State private var unlocked = false
    @State private var checking = false
    @State private var backgrounded = false
    @State private var failure: String?

    var body: some View {
        ZStack {
            if unlocked {
                content()
                if scenePhase != .active { cover }
            } else {
                lockScreen
            }
        }
        .task { await unlock() }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background:
                unlocked = false
                backgrounded = true
            case .active where backgrounded:
                // Only re-prompt after a real trip to the background; the Face ID
                // sheet itself flips the app inactive and back, which would loop.
                backgrounded = false
                Task { await unlock() }
            default:
                break
            }
        }
    }

    private var background: some View {
        LinearGradient(colors: [DonnaTheme.backgroundTop, DonnaTheme.backgroundBottom],
                       startPoint: .top, endPoint: .bottom)
            .ignoresSafeArea()
    }

    private var cover: some View {
        ZStack {
            background
            Image(systemName: "lock.fill").font(.largeTitle).foregroundStyle(DonnaTheme.rose)
        }
    }

    private var lockScreen: some View {
        ZStack {
            background
            VStack(spacing: 18) {
                Image(systemName: "lock.fill")
                    .font(.system(size: 44))
                    .foregroundStyle(DonnaTheme.rose)
                Text("Donna is locked").font(.title3.weight(.semibold))
                if let failure {
                    Text(failure).font(.footnote).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center).padding(.horizontal, 32)
                }
                Button {
                    Task { await unlock() }
                } label: {
                    Label(Self.unlockTitle, systemImage: Self.biometrySymbol)
                        .padding(.horizontal, 8)
                }
                .buttonStyle(.borderedProminent)
                .disabled(checking)
                Button("Leave") {
                    withAnimation(.easeInOut(duration: 0.5)) { presenter.showing = false }
                }
                .foregroundStyle(.secondary)
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
            failure = "Set a passcode on this device to use Donna."
            return
        }
        do {
            unlocked = try await context.evaluatePolicy(.deviceOwnerAuthentication,
                                                        localizedReason: "Unlock Donna")
            failure = nil
        } catch let error as LAError where [.userCancel, .appCancel, .systemCancel].contains(error.code) {
            failure = nil
        } catch {
            failure = error.localizedDescription
        }
    }

    private static var biometry: LABiometryType {
        let context = LAContext()
        _ = context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: nil)
        return context.biometryType
    }

    private static var unlockTitle: String {
        switch biometry {
        case .faceID: return "Unlock with Face ID"
        case .touchID: return "Unlock with Touch ID"
        case .opticID: return "Unlock with Optic ID"
        default: return "Unlock with Passcode"
        }
    }

    private static var biometrySymbol: String {
        switch biometry {
        case .faceID: return "faceid"
        case .touchID: return "touchid"
        case .opticID: return "opticid"
        default: return "lock.open"
        }
    }
}
