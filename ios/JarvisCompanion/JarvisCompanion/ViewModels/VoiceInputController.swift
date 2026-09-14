import AVFoundation
import Foundation
import Speech

/// Native, on-device-friendly dictation for the Chat composer. Recognition is
/// deliberately only an input aid: the resulting text still follows the exact
/// same draft/send path as typed text.
@MainActor
final class VoiceInputController: ObservableObject {
    @Published private(set) var isListening = false
    @Published private(set) var isStarting = false
    @Published var errorMessage: String?

    private let audioEngine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: Locale.current)
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var hasInputTap = false
    private var draftPrefix = ""
    private var generation = UUID()
    private var startTask: Task<Void, Never>?

    func toggle(existingText: String, onTranscript: @escaping (String) -> Void) {
        if isListening || isStarting {
            stop()
            return
        }
        isStarting = true
        let token = UUID()
        generation = token
        startTask = Task { await begin(existingText: existingText, token: token, onTranscript: onTranscript) }
    }

    func stop() {
        generation = UUID()
        startTask?.cancel()
        startTask = nil
        isStarting = false
        guard isListening || recognitionTask != nil || recognitionRequest != nil || hasInputTap else { return }
        audioEngine.stop()
        if hasInputTap {
            audioEngine.inputNode.removeTap(onBus: 0)
            hasInputTap = false
        }
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
        isListening = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func begin(existingText: String, token: UUID, onTranscript: @escaping (String) -> Void) async {
        defer { if generation == token { isStarting = false } }
        guard await permissionsGranted() else { return }
        guard !Task.isCancelled, generation == token else { return }
        guard let recognizer, recognizer.isAvailable else {
            errorMessage = "Speech recognition is not available right now."
            return
        }

        draftPrefix = existingText.trimmingCharacters(in: .whitespacesAndNewlines)
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
        recognitionRequest = request

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: .duckOthers)
            try session.setActive(true, options: .notifyOthersOnDeactivation)

            let input = audioEngine.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                stop()
                errorMessage = "No microphone is available. Try again when an audio input is connected."
                return
            }
            input.installTap(onBus: 0, bufferSize: 1_024, format: format) { [weak request] buffer, _ in
                request?.append(buffer)
            }
            hasInputTap = true
            audioEngine.prepare()
            try audioEngine.start()
            isListening = true

            recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
                Task { @MainActor in
                    guard let self, self.generation == token, self.isListening else { return }
                    if let result {
                        let spoken = result.bestTranscription.formattedString
                        onTranscript([self.draftPrefix, spoken]
                            .filter { !$0.isEmpty }
                            .joined(separator: self.draftPrefix.isEmpty ? "" : " "))
                        if result.isFinal { self.stop() }
                    } else if let error, self.isListening {
                        self.stop()
                        self.errorMessage = "Dictation stopped: \(error.localizedDescription)"
                    }
                }
            }
        } catch {
            stop()
            errorMessage = "I couldn’t start the microphone: \(error.localizedDescription)"
        }
    }

    private func permissionsGranted() async -> Bool {
        let speechAllowed: Bool
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized:
            speechAllowed = true
        case .notDetermined:
            speechAllowed = await withCheckedContinuation { continuation in
                SFSpeechRecognizer.requestAuthorization { status in
                    continuation.resume(returning: status == .authorized)
                }
            }
        default:
            speechAllowed = false
        }
        guard speechAllowed else {
            errorMessage = "Enable Speech Recognition for JARVIS in iOS Settings to dictate messages."
            return false
        }

        let microphoneAllowed = await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { allowed in
                continuation.resume(returning: allowed)
            }
        }
        if !microphoneAllowed {
            errorMessage = "Enable Microphone access for JARVIS in iOS Settings to dictate messages."
        }
        return microphoneAllowed
    }
}
