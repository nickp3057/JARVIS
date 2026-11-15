import SwiftUI

/// Teach JARVIS my voice, from the iPad. Room settings › Mic and speaker has
/// "From this iPad" (the iPad's own mic, e.g. the nightstand mini) and "From
/// the desk" (the Surface's mic). Core (`POST /api/voice/enroll`) then listens
/// on that mic with no wake word, and every iPad shows the line to read next
/// (`/api/voice/stream`'s `enroll`, see Core's `brain/speaker_id.py`) until
/// the print is saved. JARVIS counts each one out loud.
struct VoiceEnrollState: Decodable, Equatable {
    let needed: Int
    let taken: Int
    let line: String
    let mic: String?

    /// "the desk", "this iPad"-style label for whose print it makes.
    var where_: String {
        switch mic {
        case "desk": return "the desk mic"
        case "bed": return "the nightstand mini"
        case "door": return "the door iPad"
        case let other?: return other
        case nil: return "this mic"
        }
    }
}

@MainActor
final class VoiceEnrollment: ObservableObject {
    static let shared = VoiceEnrollment()

    @Published private(set) var state: VoiceEnrollState?
    /// The last thing JARVIS said back ("Got it. 3 more."), shown under the line.
    @Published private(set) var lastReply = ""

    /// Called for every room-voice state (JarvisAPIClient.streamVoice).
    func follow(enroll: VoiceEnrollState?, reply: String) {
        if enroll != state {
            withAnimation(.easeOut(duration: 0.25)) { state = enroll }
        }
        if enroll != nil, !reply.isEmpty { lastReply = reply }
        if enroll == nil { lastReply = "" }
    }

    func start(settings: AppSettings, mic: String) async -> String? {
        do {
            let reply = try await JarvisAPIClient.make(from: settings).voiceEnroll(action: "start", mic: mic)
            if reply.ok, let enroll = reply.enroll {
                follow(enroll: enroll, reply: "")
                return nil
            }
            return reply.detail ?? "JARVIS couldn't start."
        } catch {
            return error.localizedDescription
        }
    }

    func cancel(settings: AppSettings) {
        withAnimation(.easeOut(duration: 0.25)) { state = nil }
        Task { _ = try? await JarvisAPIClient.make(from: settings).voiceEnroll(action: "cancel", mic: "desk") }
    }
}

/// Room settings rows: start teaching from this iPad's mic or the desk's.
struct VoiceEnrollButtons: View {
    let settings: AppSettings
    /// This iPad's satellite name (`PadRole.lens`: bed or door).
    let padMic: String
    let listening: Bool

    @ObservedObject private var enrollment = VoiceEnrollment.shared
    @Environment(\.dismiss) private var dismiss
    @State private var busy = false
    @State private var problem: String?

    var body: some View {
        LabeledContent("Teach JARVIS my voice") {
            HStack(spacing: 10) {
                if listening {
                    Button("From this iPad") { start(padMic) }
                }
                Button("From the desk") { start("desk") }
            }
            .buttonStyle(.bordered)
            .disabled(busy || enrollment.state != nil)
        }
        if let problem {
            Text(problem).font(.footnote).foregroundStyle(JarvisTheme.amber)
        }
    }

    private func start(_ mic: String) {
        busy = true
        problem = nil
        Task {
            problem = await enrollment.start(settings: settings, mic: mic)
            busy = false
            if problem == nil { dismiss() }  // the lines show over the room screen
        }
    }
}

/// The lines to read, over the room screen while enrolling.
struct VoiceEnrollCard: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject private var enrollment = VoiceEnrollment.shared

    var body: some View {
        if let state = enrollment.state {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    Label("Teaching JARVIS your voice", systemImage: "waveform")
                        .font(.headline)
                        .foregroundStyle(JarvisTheme.cyan)
                    Spacer()
                    Text("\(min(state.taken + 1, state.needed)) of \(state.needed)")
                        .font(.headline.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                Text("Read this out loud, in your normal voice, from where you usually talk to \(state.where_):")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Text(state.line)
                    .font(.system(size: 34, weight: .semibold, design: .rounded))
                    .foregroundStyle(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .id(state.taken)
                    .transition(.opacity)
                ProgressView(value: Double(state.taken), total: Double(max(1, state.needed)))
                    .tint(JarvisTheme.cyan)
                HStack {
                    Text(enrollment.lastReply.isEmpty ? "No need to say \u{201C}Hey JARVIS\u{201D}." : enrollment.lastReply)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Cancel") { enrollment.cancel(settings: settings) }
                        .buttonStyle(.bordered)
                }
            }
            .padding(24)
            .frame(maxWidth: 680)
            .background(JarvisTheme.panelRaised, in: RoundedRectangle(cornerRadius: 22))
            .overlay(RoundedRectangle(cornerRadius: 22).stroke(JarvisTheme.border))
            .padding(24)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color.black.opacity(0.35).ignoresSafeArea())
            .transition(.opacity)
        }
    }
}
