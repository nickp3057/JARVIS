import AVFoundation
import Foundation

/// Plays streamed mono PCM without waiting for an entire file or response.
@MainActor
final class VoiceAudioPlayer {
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let format = AVAudioFormat(standardFormatWithSampleRate: 22050, channels: 1)!
    private var generation = UUID()
    private var queuedFrames = 0
    private var receivedAll = false
    var onDrained: (() -> Void)?

    init() {
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
    }

    func append(_ data: Data) throws {
        guard !data.isEmpty, data.count % 2 == 0, data.count <= 128 * 1024 else {
            throw JarvisAPIError.malformedResponse("Invalid voice audio.")
        }
        let frames = data.count / 2
        guard queuedFrames + frames <= 22050 * 90 else {
            throw JarvisAPIError.malformedResponse("Voice playback fell behind.")
        }
        if !engine.isRunning {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .spokenAudio)
            try session.setActive(true)
            engine.prepare()
            try engine.start()
            player.play()
        }
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
              let output = buffer.floatChannelData?[0] else { return }
        buffer.frameLength = AVAudioFrameCount(frames)
        data.withUnsafeBytes { bytes in
            for i in 0..<frames {
                let sample = Int16(littleEndian: bytes.loadUnaligned(fromByteOffset: i * 2, as: Int16.self))
                output[i] = Float(sample) / 32768
            }
        }
        queuedFrames += frames
        let token = generation
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.generation == token else { return }
                self.queuedFrames = max(0, self.queuedFrames - frames)
                self.finishIfDrained()
            }
        }
    }

    func finish() { receivedAll = true; finishIfDrained() }

    private func finishIfDrained() {
        if receivedAll && queuedFrames == 0 {
            stop()
            onDrained?()
        }
    }

    func stop() {
        generation = UUID()
        player.stop()
        engine.stop()
        queuedFrames = 0
        receivedAll = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
