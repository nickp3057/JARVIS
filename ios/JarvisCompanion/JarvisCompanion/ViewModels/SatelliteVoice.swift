import AVFoundation
import Foundation
import SwiftUI

/// This iPad as JARVIS's ears and voice in the room (a "satellite" of the
/// desk mic and speaker on the Surface). While the app is on screen it
/// streams the mic to Core as 16 kHz mono PCM over
/// `WS /api/voice/satellite?name=<bed|door>`; Core's room voice does all the
/// listening (wake word, voice ID, transcript) and, when this iPad heard
/// "Hey JARVIS" best, sends the reply's audio back to play here.
/// See Core's `speech/satellites.py` for the wire format.
///
/// Speaker: Core plays everything on the desk speaker and every iPad at
/// once, at the desk's loudness (`play {gain}`); Room settings › Match the
/// desk trims it by ear (keep the iPad's own volume up). No iOS voice
/// processing: it reshuffled the audio as soon as a reply played and
/// dropped the connection, and Core doesn't listen for the wake word while
/// JARVIS talks anyway (barge-in aside).
///
/// On by default on the nightstand iPad, off on the door's
/// (Room settings › JARVIS listens here). iOS only lets an app use the mic
/// while it's in front, which the wall/nightstand iPads always are.
@MainActor
final class SatelliteVoice: ObservableObject {
    static let shared = SatelliteVoice()

    enum Status: Equatable {
        case off, connecting, live, roomVoiceOff, noMic, failed(String)

        var text: String {
            switch self {
            case .off: return "Off"
            case .connecting: return "Connecting…"
            case .live: return "Listening for \u{201C}Hey JARVIS\u{201D}"
            case .roomVoiceOff: return "The room voice is off on the Surface (ROOM_VOICE)"
            case .noMic: return "Microphone access is off (Settings › Jarvis)"
            case .failed(let why): return "Reconnecting… (\(why))"
            }
        }
    }

    @Published private(set) var status: Status = .off

    static let settingKey = "room.satellite"
    /// Room settings › Match the desk: the iPad's loudness relative to the desk (0.5-2).
    static let trimKey = "room.satelliteTrim"

    /// The stored choice: "" = by role (on for the nightstand), "on", "off".
    static func enabled(stored: String, role: PadRole) -> Bool {
        switch stored {
        case "on": return true
        case "off": return false
        default: return role == .nightstand
        }
    }

    private var loop: Task<Void, Never>?
    private var socket: URLSessionWebSocketTask?
    private var audio: SatelliteAudio?

    /// Start (or keep) streaming as `name`. Safe to call repeatedly.
    func start(settings: AppSettings, name: String) {
        guard loop == nil, settings.isConfigured, !RootView.isMac else { return }
        loop = Task { [weak self] in await self?.run(settings: settings, name: name) }
    }

    func stop() {
        loop?.cancel()
        loop = nil
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
        audio?.stop()
        audio = nil
        status = .off
    }

    private func run(settings: AppSettings, name: String) async {
        var backoff: Double = 2
        while !Task.isCancelled {
            guard await Self.micAllowed() else {
                status = .noMic
                try? await Task.sleep(for: .seconds(30))
                continue
            }
            status = .connecting
            var wait = backoff
            do {
                try await session(settings: settings, name: name)
                backoff = 2
                wait = 2
            } catch SatelliteError.roomVoiceOff {
                status = .roomVoiceOff
                wait = 60
            } catch {
                if Task.isCancelled { break }
                status = .failed(error.localizedDescription)
                backoff = min(30, backoff * 2)
                // Tell Core why, so its log says more than "disconnected".
                socket?.send(.string(#"{"type":"bye","reason":"\#(Self.reason(error))"}"#)) { _ in }
                try? await Task.sleep(for: .milliseconds(200))
            }
            audio?.stop()
            audio = nil
            socket?.cancel(with: .goingAway, reason: nil)
            socket = nil
            try? await Task.sleep(for: .seconds(wait))
        }
    }

    private enum SatelliteError: Error { case roomVoiceOff, audioStopped }

    private static func reason(_ error: Error) -> String {
        let text = error is SatelliteError ? "audio stopped (\(error))" : error.localizedDescription
        return text.replacingOccurrences(of: "\"", with: "'").replacingOccurrences(of: "\\", with: "/")
    }

    /// One connection: open the socket, start the audio, then follow Core's
    /// messages until the socket drops.
    private func session(settings: AppSettings, name: String) async throws {
        let client = try JarvisAPIClient.make(from: settings)
        let ws = try client.satelliteSocket(name: name)
        socket = ws
        ws.resume()

        // Wait for Core's hello (or "off") before turning the mic on.
        guard case .string(let first) = try await ws.receive() else { throw SatelliteError.audioStopped }
        let hello = Self.decode(first)
        if hello["type"] as? String == "off" { throw SatelliteError.roomVoiceOff }

        let audio = SatelliteAudio()
        self.audio = audio
        try audio.start(onMic: { data in
            ws.send(.data(data)) { _ in }  // a failed send shows up as a failed receive below
        })
        status = .live

        while !Task.isCancelled {
            let message = try await ws.receive()
            switch message {
            case .data(let pcm):
                audio.enqueue(pcm)
            case .string(let text):
                let json = Self.decode(text)
                switch json["type"] as? String {
                case "play":
                    let trim = UserDefaults.standard.object(forKey: Self.trimKey) as? Double ?? 1
                    audio.begin(rate: (json["rate"] as? Double) ?? 22050,
                                gain: Float(((json["gain"] as? Double) ?? 1) * trim))
                case "stop":
                    audio.flush()
                case "off":
                    throw SatelliteError.roomVoiceOff
                default:
                    break  // hello, end, ping
                }
            @unknown default:
                break
            }
            if audio.failed { throw SatelliteError.audioStopped }
        }
    }

    private static func decode(_ text: String) -> [String: Any] {
        (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any] ?? [:]
    }

    private static func micAllowed() async -> Bool {
        switch AVAudioApplication.shared.recordPermission {
        case .granted: return true
        case .denied: return false
        default: return await AVAudioApplication.requestRecordPermission()
        }
    }
}

/// The audio side: the mic (voice-processed, so the iPad's own speaker is
/// cancelled out) converted to 16 kHz mono int16, and a player node for the
/// replies Core sends (mono int16 at the `play` rate). Runs off the main
/// actor; AVAudioEngine's taps call back on their own thread.
final class SatelliteAudio: @unchecked Sendable {
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let playFormat = AVAudioFormat(standardFormatWithSampleRate: 22050, channels: 1)!
    private let micFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
    private var converter: AVAudioConverter?
    private var playRate: Double = 22050
    private var observers: [NSObjectProtocol] = []
    private let lock = NSLock()
    private var _failed = false

    /// The engine stopped and couldn't be restarted in place: the connection
    /// restarts and brings the audio back up.
    var failed: Bool { lock.withLock { _failed } }

    func start(onMic: @escaping @Sendable (Data) -> Void) throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .mixWithOthers])
        try session.setActive(true)

        let input = engine.inputNode
        let inFormat = input.outputFormat(forBus: 0)
        converter = AVAudioConverter(from: inFormat, to: micFormat)

        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: playFormat)

        input.installTap(onBus: 0, bufferSize: 2048, format: inFormat) { [weak self] buffer, _ in
            guard let self, let data = self.convert(buffer) else { return }
            onMic(data)
        }
        engine.prepare()
        try engine.start()
        player.play()

        // iOS reshuffles audio now and then (voice processing settling in,
        // the camera starting, a route change, a call). Bring the engine back
        // up in place; only if that fails does the connection restart.
        let center = NotificationCenter.default
        observers.append(center.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine,
                                            queue: .main) { [weak self] _ in self?.resume() })
        observers.append(center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil,
                                            queue: .main) { [weak self] note in
            let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
            if raw == AVAudioSession.InterruptionType.ended.rawValue { self?.resume() }
        })
    }

    /// Restart a stopped engine (after a configuration change or an
    /// interruption ends); the connection restarts only if this fails.
    private func resume() {
        guard !engine.isRunning else {
            if !player.isPlaying { player.play() }
            return
        }
        do {
            try AVAudioSession.sharedInstance().setActive(true)
            engine.prepare()
            try engine.start()
            player.play()
        } catch {
            markFailed()
        }
    }

    func stop() {
        observers.forEach(NotificationCenter.default.removeObserver)
        observers.removeAll()
        engine.inputNode.removeTap(onBus: 0)
        player.stop()
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func markFailed() { lock.withLock { _failed = true } }

    /// A new clip: its sample rate and loudness (quieter in quiet hours).
    func begin(rate: Double, gain: Float) {
        playRate = rate
        player.volume = max(0, min(1, gain))
        if !player.isPlaying { player.play() }
    }

    /// Cut off what's playing or queued (barge-in, a tap to hush).
    func flush() {
        player.stop()
        player.play()
    }

    /// Reply audio: mono little-endian int16 at `playRate`, played in order.
    func enqueue(_ data: Data) {
        let samples = data.count / 2
        guard samples > 0 else { return }
        var floats = [Float](repeating: 0, count: samples)
        data.withUnsafeBytes { raw in
            let ints = raw.bindMemory(to: Int16.self)
            for i in 0..<samples { floats[i] = Float(Int16(littleEndian: ints[i])) / 32768 }
        }
        if playRate != playFormat.sampleRate { floats = Self.resample(floats, from: playRate, to: playFormat.sampleRate) }
        guard let buffer = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: AVAudioFrameCount(floats.count)) else { return }
        buffer.frameLength = AVAudioFrameCount(floats.count)
        floats.withUnsafeBufferPointer { buffer.floatChannelData![0].update(from: $0.baseAddress!, count: floats.count) }
        player.scheduleBuffer(buffer)
    }

    private func convert(_ buffer: AVAudioPCMBuffer) -> Data? {
        guard let converter else { return nil }
        let ratio = micFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: micFormat, frameCapacity: capacity) else { return nil }
        var consumed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if consumed {
                status.pointee = .noDataNow
                return nil
            }
            consumed = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, out.frameLength > 0, let ints = out.int16ChannelData else { return nil }
        return Data(bytes: ints[0], count: Int(out.frameLength) * 2)
    }

    /// Linear resampling, for a voice at another rate than 22.05 kHz.
    static func resample(_ input: [Float], from: Double, to: Double) -> [Float] {
        guard from > 0, to > 0, from != to, input.count > 1 else { return input }
        let count = Int(Double(input.count) * to / from)
        let step = from / to
        return (0..<count).map { i in
            let position = Double(i) * step
            let index = Int(position)
            let next = min(index + 1, input.count - 1)
            let fraction = Float(position - Double(index))
            return input[index] * (1 - fraction) + input[next] * fraction
        }
    }
}
