import AVFoundation
import SwiftUI

// MARK: CC-7 voice mode for the panel (`lecture capstone voice ...`, capstone_voice.py)

/// `lecture capstone voice say|answer|log` replies (the panel view fields are decoded separately as CapPanel).
struct CapVoice: Decodable, Sendable {
    let ok: Bool; let error: String?; let audio: String?; let voice: String?; let transcript: String?
    let speech: Speech?; let timing: Timing?
    struct Speech: Decodable, Sendable { let words: Int; let wpm: Int?; let fillers: Int; let fillers_per_100: Double?; let long_pauses: Int }
    struct Timing: Decodable, Sendable { let transcribe_s: Double; let model_s: Double; let speak_s: Double; let processing_s: Double }

    /// One line of pace and fillers, labelled as information only.
    static func speechLine(_ s: Speech) -> String {
        let pace = s.wpm.map { "\($0) words a minute" } ?? "too short for a pace"
        let fill = s.fillers_per_100.map { String(format: "%.1f fillers per 100 words", $0) } ?? "no fillers counted"
        return "\(pace) · \(fill) · \(s.long_pauses) long pause\(s.long_pauses == 1 ? "" : "s") (information only)"
    }
}

/// Records one spoken answer as 16 kHz mono WAV. It stops on Stop, or about two seconds after
/// you stop talking once speech has been heard, or at three minutes. `endedAt` is when your
/// speech ended, the start of the latency the app reports.
@MainActor
final class VoiceRecorder: ObservableObject {
    @Published private(set) var recording = false
    @Published private(set) var level: Float = -160
    private(set) var endedAt: Date?
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("panel-answer.wav")
    private var rec: AVAudioRecorder?
    private var heardSpeech = false
    private var quietSince: Date?
    private var meter: Task<Void, Never>?

    nonisolated static let speechDB: Float = -35, quietDB: Float = -45, quietSeconds = 2.0

    /// nil when recording started, else the reason it could not.
    func start() async -> String? {
        guard await AVCaptureDevice.requestAccess(for: .audio) else {
            return "Microphone access is off: System Settings → Privacy & Security → Microphone → Lecture Lens."
        }
        let settings: [String: Any] = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000.0,
                                       AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false]
        do { rec = try AVAudioRecorder(url: url, settings: settings) } catch { return "Could not start recording: \(error.localizedDescription)" }
        rec?.isMeteringEnabled = true
        guard rec?.record(forDuration: 180) == true else { return "Could not start recording." }
        recording = true; heardSpeech = false; quietSince = nil; endedAt = nil
        meter = Task { @MainActor [weak self] in
            while let self, self.recording {
                self.tick()
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
        return nil
    }

    /// Pure rule for auto-stop, so it can be tested: speech heard, then quiet for `quietSeconds`.
    nonisolated static func shouldStop(heardSpeech: Bool, quietFor: TimeInterval?) -> Bool {
        heardSpeech && (quietFor ?? 0) >= quietSeconds
    }

    private func tick() {
        guard let rec, recording else { return }
        rec.updateMeters()
        let p = rec.averagePower(forChannel: 0)
        level = p
        if p > Self.speechDB { heardSpeech = true; quietSince = nil }
        else if heardSpeech && p < Self.quietDB && quietSince == nil { quietSince = Date() }
        if Self.shouldStop(heardSpeech: heardSpeech, quietFor: quietSince.map { Date().timeIntervalSince($0) }) {
            stop(endedAt: quietSince)
        } else if !rec.isRecording {
            stop()
        }
    }

    func stop(endedAt t: Date? = nil) {
        guard recording else { return }
        meter?.cancel(); meter = nil
        rec?.stop()
        endedAt = t ?? Date()
        recording = false
    }
}

/// Answer controls in voice mode: the waiting question is spoken in that reviewer's voice,
/// you answer out loud, the transcript goes through the same panel rules as a typed answer.
struct VoiceAnswerControls: View {
    let turnKey: Int
    let onPanel: (CapPanel) -> Void
    @StateObject private var rec = VoiceRecorder()
    @State private var player: AVAudioPlayer?
    @State private var busy = false
    @State private var last: CapVoice?
    @State private var err: String?
    @State private var spokenTurn = -1

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                if rec.recording {
                    Button("Stop", systemImage: "stop.circle.fill") { rec.stop() }
                        .accessibilityLabel("Stop recording your answer")
                    Text("Listening… stops after 2 s of quiet").font(.scaled(.caption)).foregroundStyle(.secondary)
                } else {
                    Button(busy ? "The panel is thinking…" : "Answer out loud", systemImage: "mic.circle") {
                        Task { err = await rec.start() }
                    }
                    .disabled(busy)
                    .help("Record your answer; it is transcribed on this Mac")
                    Button("Hear the question again", systemImage: "speaker.wave.2") { Task { await speakCurrent(force: true) } }
                        .disabled(busy)
                    if busy { ProgressView().controlSize(.small) }
                }
            }
            if let t = last?.transcript { Text("Heard: \(t)").font(.scaled(.caption)).foregroundStyle(.secondary).textSelection(.enabled) }
            if let s = last?.speech { Text(CapVoice.speechLine(s)).font(.scaled(.caption2)).foregroundStyle(.tertiary) }
            if let e = err ?? last?.error { Text(e).font(.scaled(.caption)).foregroundStyle(.orange) }
        }
        .task(id: turnKey) { await speakCurrent(force: false) }
        .onChange(of: rec.recording) { was, now in
            if was && !now { Task { await submit() } }
        }
    }

    private func speakCurrent(force: Bool) async {
        guard force || spokenTurn != turnKey else { return }
        spokenTurn = turnKey
        if let v = await Capstone.run(CapVoice.self, ["voice", "say"], timeout: 120), let a = v.audio { play(a, endedAt: nil) }
    }

    private func play(_ path: String, endedAt: Date?) {
        guard let pl = try? AVAudioPlayer(contentsOf: URL(fileURLWithPath: path)) else { return }
        player = pl
        pl.play()
        if let endedAt {
            let ms = Int(Date().timeIntervalSince(endedAt) * 1000)
            Task { _ = await Capstone.run(CapVoice.self, ["voice", "log", "--latency-ms", String(ms)]) }
        }
    }

    private func submit() async {
        guard let ended = rec.endedAt else { return }
        busy = true; defer { busy = false }
        guard let r = try? await ToolRunner.run("lecture", ["capstone", "voice", "answer", "--audio", rec.url.path], timeout: 300) else {
            err = "The voice step did not answer; try again."; return
        }
        let dec = JSONDecoder()
        last = try? dec.decode(CapVoice.self, from: r.stdout)
        err = last == nil ? "The voice step returned something the app could not read." : nil
        if let a = last?.audio {
            spokenTurn = turnKey + 2          // the reply below is the next question: don't speak it twice
            play(a, endedAt: ended)
        }
        if let pnl = try? dec.decode(CapPanel.self, from: r.stdout) { onPanel(pnl) }
    }
}
