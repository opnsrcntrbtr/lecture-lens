import AppKit
import AVFoundation
import Foundation

/// A health signal the menu bar and Capture screen show as a light.
struct Signal: Identifiable, Sendable {
    enum State: Sendable { case ok, warn, down, unknown }
    let id: String
    let label: String
    let state: State
    let detail: String
    let fix: String?
}

@MainActor
@Observable
final class AppModel {
    // capture
    var mode = ModeState(running: false, mode: "stopped", pinned: nil, autoswitch: false)
    var screenpipe: ScreenpipeHealth? = nil
    var omlx: OmlxStatus? = nil
    var doctor: DoctorResult? = nil
    var lastError: String? = nil
    var busyCapture = false
    // permissions as macOS applies them to screenpipe (see Permissions)
    var axTrusted = true
    var screenTrusted = true
    var micStatus: AVAuthorizationStatus = .authorized
    private(set) var drmPagesConfigured = false
    /// Set when Start was refused because protected video would go black.
    var permissionBlock = false
    // lectures
    var sessions: [Session] = []
    var loadingSessions = false
    // jobs / evals
    let jobs = JobQueue()
    var evalRuns: [EvalRun] = []

    private var timers: [Timer] = []

    // MARK: lifecycle
    func start() {
        guard timers.isEmpty else { return }
        Task { await refreshAll(probeVision: false) }
        timers = [
            Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
                Task { @MainActor in await self?.refreshFast() } },
            Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
                Task { @MainActor in await self?.refreshDoctor(probeVision: false) } },
        ]
    }

    func refreshAll(probeVision: Bool) async {
        await refreshFast()
        await refreshDoctor(probeVision: probeVision)
        await loadSessions()
        loadEvalRuns()
    }

    func refreshFast() async {
        if let m = try? await ToolRunner.json(ModeState.self, "sp-mode", ["--json"], timeout: 10) { mode = m }
        screenpipe = await Self.get(ScreenpipeHealth.self, "http://127.0.0.1:3030/health")
        omlx = await Self.get(OmlxStatus.self, "http://127.0.0.1:8001/api/status")
        let env = EnvFile()
        judgeModel = env.value("OMLX_JUDGE_MODEL")
        drmPagesConfigured = !(env.value("SP_DRM_URLS") ?? "").isEmpty
        axTrusted = Permissions.accessibility
        screenTrusted = Permissions.screenRecording
        micStatus = Permissions.microphone
        // A refused Start leaves its message behind; drop it once the cause is fixed.
        if lastError?.hasPrefix("Not started:") == true, axTrusted, micStatus == .authorized { lastError = nil }
    }

    func refreshDoctor(probeVision: Bool) async {
        do {
            doctor = try await ToolRunner.json(DoctorResult.self, "lecture",
                                               ["doctor", "--json"] + (probeVision ? [] : ["--quick"]), timeout: 120)
        } catch { lastError = error.localizedDescription }
    }

    private static func get<T: Decodable>(_ t: T.Type, _ url: String) async -> T? {
        var req = URLRequest(url: URL(string: url)!)
        req.timeoutInterval = 3
        guard let (data, _) = try? await URLSession.shared.data(for: req) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    // MARK: signals (what the lights show)
    var signals: [Signal] {
        func check(_ id: String) -> DoctorResult.Check? { doctor?.checks.first { $0.id == id } }
        var s: [Signal] = []
        s.append(Signal(id: "capture", label: "Capture",
                        state: mode.running ? .ok : .down,
                        detail: mode.running ? "running · \(mode.mode) mode\(mode.autoswitch ? " · auto-switch" : "")" : "stopped",
                        fix: mode.running ? nil : "Start capture"))
        let frames = check("frames")
        let frameLive = screenpipe?.frame_status.map { $0 == "ok" || $0 == "running" } ?? false
        if mode.running, screenpipe?.drm_content_paused == true {
            // Expected, not a fault: protected video would go black under screen capture.
            s.append(Signal(id: "screen", label: "Screen", state: .ok,
                            detail: "paused on protected video — audio still recording", fix: nil))
        } else {
            s.append(Signal(id: "screen", label: "Screen",
                            state: frameLive ? .ok : (frames?.ok == true ? .warn : (mode.running ? .down : .unknown)),
                            detail: frameLive ? "frames arriving" : (frames?.message ?? "no data yet"),
                            fix: frames?.fix))
        }
        // Accessibility: AXIsProcessTrusted() is live. Screen recording: the app's own
        // CGPreflightScreenCaptureAccess() is cached by macOS for the life of the process
        // (false until relaunch after granting), so screenpipe's live frames — or its DRM
        // pause, which only runs with capture working — take precedence over it.
        let screenEffective = screenTrusted || frameLive || screenpipe?.drm_content_paused == true
        if !axTrusted {
            s.append(Signal(id: "perm-ax", label: "Access", state: .down,
                            detail: "Accessibility not effective for Lecture Lens"
                                + (drmPagesConfigured ? " — lecture video would go black" : ""),
                            fix: "In Privacy & Security → Accessibility, remove Lecture Lens with (−), add it again with (+), then Start capture"))
        }
        if micStatus != .authorized {
            s.append(Signal(id: "perm-mic", label: "Access", state: .down,
                            detail: "Microphone not allowed — screenpipe won't start while audio is on (it only needs the permission; lecture audio comes from the app tap)",
                            fix: micStatus == .notDetermined ? "Start capture and click Allow"
                                : "In Privacy & Security → Microphone, remove Lecture Lens with (−), then Start capture and Allow"))
        }
        if mode.running && screenpipe == nil {
            s.append(Signal(id: "perm-wait", label: "Access", state: .warn,
                            detail: "screenpipe is running but not serving yet — usually waiting for a permission (see ~/.screenpipe/sp-start.log)",
                            fix: nil))
        } else if !screenEffective {
            let denied = screenpipe?.vision_reason == "permission_denied"
            s.append(mode.running
                ? Signal(id: "perm-screen", label: "Access", state: .down,
                         detail: denied
                            ? "Screen recording denied by macOS for this build (the grant in Settings belongs to an older signature) — macOS will keep showing its prompt"
                            : "Screen recording not effective — capture is running but no frames arrive",
                         fix: "Stop capture. In Terminal run: tccutil reset ScreenCapture io.github.opnsrcntrbtr.lecture-lens — or in Screen & System Audio Recording remove Lecture Lens (−) and add it back (+). Then Relaunch app and Start capture")
                : Signal(id: "perm-screen", label: "Access", state: .warn,
                         detail: "Screen recording not confirmed yet (macOS reports new grants to a running app only after relaunch)",
                         fix: "Start capture; allow if macOS asks. If this stays after capture runs, use Relaunch below"))
        }
        let tap = check("app_audio")
        s.append(Signal(id: "audio", label: "App audio",
                        state: tap?.ok == true ? .ok : (tap == nil ? .unknown : .down),
                        detail: tap?.message ?? "not checked", fix: tap?.fix))
        let vision = check("vision")
        s.append(Signal(id: "vision", label: "Vision",
                        state: vision?.ok == true ? (vision?.level == "warn" ? .warn : .ok) : (vision == nil ? .unknown : .down),
                        detail: vision?.message ?? "not checked", fix: vision?.fix))
        let loaded = omlx?.loaded_models ?? []
        s.append(Signal(id: "omlx", label: "oMLX",
                        state: omlx == nil ? .down : (loaded.isEmpty || judgeLoaded ? .warn : .ok),
                        detail: omlx == nil ? "not reachable on :8001"
                            : judgeLoaded ? "judge loaded — eval in progress, Ask and builds wait"
                            : (loaded.first ?? "no model loaded"),
                        fix: omlx == nil ? "Open oMLX" : nil))
        return s
    }

    /// True while an eval has swapped the judge into oMLX's single model slot.
    /// Anything that calls the generator now would force another swap (or a
    /// 507), so the UI holds model-calling actions until it's back.
    var judgeLoaded: Bool {
        guard let judge = judgeModel, !judge.isEmpty else { return false }
        return (omlx?.loaded_models ?? []).contains(judge)
    }
    private(set) var judgeModel: String? = nil

    var overall: Signal.State {
        let states = signals.map(\.state)
        if states.contains(.down) { return .down }
        if states.contains(.warn) || states.contains(.unknown) { return .warn }
        return .ok
    }

    // MARK: capture control — the app launches screenpipe, so macOS permission
    // prompts and grants belong to this app (ADR-002).
    static let axBlockedMessagePrefix = "Not started: Accessibility isn't effective for Lecture Lens,"

    /// Relaunch to refresh macOS's per-process cached screen-recording answer.
    func relaunch() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        p.arguments = ["-n", Bundle.main.bundleURL.path]
        try? p.run()
        NSApp.terminate(nil)
    }

    /// `force` skips the protected-video guard (the user chose "Start anyway").
    func startCapture(force: Bool = false) async {
        await refreshFast()
        // Without Accessibility screenpipe can't read Chrome's URL, never sees the
        // DRM player page, keeps capturing the screen, and the lecture goes black.
        if drmPagesConfigured && !axTrusted && !force {
            permissionBlock = true
            lastError = Self.axBlockedMessagePrefix + " so protected lecture video would go black. Fix it in the Capture window."
            return
        }
        // screenpipe waits (then gives up) when the microphone isn't authorized, so ask
        // here, where macOS can show the dialog for this app.
        if Permissions.microphone == .notDetermined { _ = await Permissions.requestMicrophone() }
        micStatus = Permissions.microphone
        if micStatus != .authorized && !force {
            lastError = "Not started: Microphone isn't allowed for Lecture Lens, and screenpipe won't start without it. See the Access row in the Capture window."
            return
        }
        permissionBlock = false
        lastError = nil
        busyCapture = true; defer { busyCapture = false }
        do {
            let r = try await ToolRunner.run("sp-start", [], timeout: 60)
            if !r.ok { lastError = r.stderr.isEmpty ? "sp-start failed" : r.stderr }
        } catch { lastError = error.localizedDescription }
        try? await Task.sleep(for: .seconds(3))
        await refreshFast()
    }

    func stopCapture() async {
        busyCapture = true; defer { busyCapture = false }
        _ = try? await ToolRunner.run("sp-stop", [], timeout: 60)
        await refreshFast()
    }

    func setMode(_ m: String) async {
        busyCapture = true; defer { busyCapture = false }
        _ = try? await ToolRunner.run("sp-mode", [m], timeout: 90)
        await refreshFast()
    }

    func openPrivacySettings() {
        let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!
        NSWorkspace.shared.open(url)
    }

    // MARK: lectures
    func loadSessions() async {
        loadingSessions = true; defer { loadingSessions = false }
        if let l = try? await ToolRunner.json(SessionList.self, "lecture", ["list", "--json", "--since", "90d"], timeout: 60) {
            sessions = l.sessions
        }
    }

    func build(_ s: Session) {
        guard let id = s.id else { return }
        jobs.enqueue("Build \(s.displayTitle)", Paths.tool("lecture"), ["build", id, "--since", "90d"]) { [weak self] _ in
            Task { await self?.loadSessions() }
        }
    }

    // MARK: evals
    struct EvalRun: Identifiable, Sendable {
        let id: String       // folder name
        let date: Date
        let summary: EvalSummary?
        let reportPath: URL?
    }

    func loadEvalRuns() {
        let fm = FileManager.default
        guard let dirs = try? fm.contentsOfDirectory(at: Paths.results, includingPropertiesForKeys: [.contentModificationDateKey]) else { return }
        evalRuns = dirs.compactMap { d in
            let sum = d.appendingPathComponent("summary.json")
            guard fm.fileExists(atPath: sum.path) else { return nil }
            let date = (try? d.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            let s = (try? Data(contentsOf: sum)).flatMap { try? JSONDecoder().decode(EvalSummary.self, from: $0) }
            let rep = d.appendingPathComponent("report.md")
            return EvalRun(id: d.lastPathComponent, date: date, summary: s,
                           reportPath: fm.fileExists(atPath: rep.path) ? rep : nil)
        }.sorted { $0.id > $1.id }
    }

    func runVisionEval() {
        jobs.enqueue("Vision eval (clean slides)", Paths.study.appendingPathComponent("eval/run_eval.py"),
                     ["--configs", "think_off"]) { [weak self] _ in self?.loadEvalRuns() }
    }

    func runDeepEval() {
        jobs.enqueue("DeepEval study suite (swaps models)", Paths.study.appendingPathComponent("tests/evals/run.sh"),
                     []) { [weak self] _ in self?.loadEvalRuns() }
    }

    func runJudgeBenchmark() {
        jobs.enqueue("Judge benchmark: Laya vs oMLX", Paths.study.appendingPathComponent("tests/evals/run_judge_bench.sh"),
                     []) { [weak self] _ in self?.loadEvalRuns() }
    }
}
