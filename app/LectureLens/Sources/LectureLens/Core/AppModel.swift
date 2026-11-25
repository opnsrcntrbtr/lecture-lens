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
    /// Sidebar selection; also set by the Go menu (⌘1…⌘7).
    var screen: Screen? = .capture
    /// A missed mastery-check item to work through in the guided tutor (`mastery:<check>:<item>`).
    var pendingGuidedItem: String? = nil

    func tutorOnItem(_ ref: String) {
        pendingGuidedItem = ref
        screen = .ask
    }
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
    // courses: one shown at a time; switching reloads the lectures and every screen
    var courseList: CourseList?
    var course: CourseList.Course?
    // lectures
    var sessions: [Session] = []
    /// Why the lecture list could not be read (shown instead of an empty library).
    var sessionsError: String?
    var nextClass: NextClass?
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
                Task { @MainActor in await self?.refreshDoctor(probeVision: false); self?.checkAutoCurate() } },
            Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
                Task { @MainActor in
                    self?.checkAutoImport(); self?.checkClassStart(); self?.checkClassEnded(); self?.checkCaptureHealth() } },
        ]
    }

    // MARK: curated top questions (curate.py)
    var curateRunning = false
    var curateNote: String?
    private var lastCurate: Date?
    private var curateRetryAt: Date?
    private var endNoticeSent: Set<String> = []
    var autoCurate: Bool = UserDefaults.standard.bool(forKey: CuratedQuestions.autoKey) {
        didSet { UserDefaults.standard.set(autoCurate, forKey: CuratedQuestions.autoKey) }
    }

    /// One round now. Waits for an eval's judge to unload (it holds the local model).
    func curateNow(background: Bool = false) {
        guard !curateRunning else { return }
        guard !judgeLoaded else { curateNote = "judge loaded: try after the eval"; return }
        curateRunning = true; curateNote = nil
        Task {
            defer { curateRunning = false }
            var good = false
            do {
                let args = ["curate", "--json"] + (background ? ["--background"] : [])
                let r = try await ToolRunner.json(CurateResult.self, "lecture", args, timeout: 300)
                if !r.ok { curateNote = r.error ?? "failed" }
                else if let n = r.round, let a = r.added { curateNote = "round \(n): \(a) new"; good = true }
                else { curateNote = r.note ?? "nothing new"; good = true }
            } catch { curateNote = "failed: \(error)".prefix(120).description }
            // a good round restarts the 25 minutes; a failed one is retried after 5
            if good { lastCurate = .now; curateRetryAt = nil } else { curateRetryAt = .now.addingTimeInterval(CuratedQuestions.retryAfterFailure) }
        }
    }

    /// Auto-curate every 25 minutes while the follower is running (Settings on the Live tab).
    func checkAutoCurate(now: Date = .now) {
        guard autoCurate, !curateRunning else { return }
        Task {
            let fresh = await Task.detached { LiveData.fresh(LiveData.state()) }.value
            if CuratedQuestions.due(enabled: autoCurate, fresh: fresh, last: lastCurate, retryAt: curateRetryAt, now: now) {
                curateNow(background: true)
            }
        }
    }

    /// The follower decided the class is over (state "ended") while capture still runs:
    /// say so once a day. Capture is only ever stopped by the learner.
    func checkClassEnded() {
        guard mode.running else { return }
        Task {
            let s = await Task.detached { LiveData.state() }.value
            guard s?.status == "ended", let at = s?.ended_at, !endNoticeSent.contains(String(at.prefix(10))) else { return }
            endNoticeSent.insert(String(at.prefix(10)))
            ClassReminder.notify(id: "class-ended-\(at.prefix(10))", title: "Class seems to have ended",
                                 body: "Stop capture, then run After class on the Lectures screen.")
        }
    }

    /// After class in one job: ingest, build, attendee-name check, qa.md (`lecture after`).
    func afterClass(_ s: Session) {
        guard let id = s.id else { return }
        jobs.enqueue("After class \(s.displayTitle)", Paths.tool("lecture"), ["after", id, "--since", "90d", "--json"]) { [weak self] _ in
            Task { await self?.loadSessions() }
        }
    }

    /// Class-start reminders sent this run (ClassReminder.key).
    private var remindersSent: Set<String> = []

    /// Set when the next class is near but capture is pinned to Portal (it would miss the meeting).
    var classModeWarning: String? { ClassMode.warning(next: nextClass, pinned: mode.pinned, now: .now) }

    /// A notification when today's class starts without Live capture (ClassReminder), and one an
    /// hour before it when capture is pinned to Portal.
    func checkClassStart(now: Date = .now) {
        guard let c = nextClass else { return }
        if let w = ClassMode.warning(next: c, pinned: mode.pinned, now: now), !remindersSent.contains("pin-" + c.date),
           let start = ClassReminder.window(c)?.start, now >= start.addingTimeInterval(-3600) {
            remindersSent.insert("pin-" + c.date)
            ClassReminder.notify(id: "pin-" + c.date, title: "Capture is pinned to Portal", body: w)
        }
        guard let stage = ClassReminder.due(c, now: now, liveRunning: mode.running, sent: remindersSent) else { return }
        remindersSent.insert(stage == .late ? ClassReminder.lateKey(c, now: now) : ClassReminder.key(c, stage))
        ClassReminder.post(c, stage, now: now)
    }

    /// The newest capture-health probe (`lecture health`, CT-1) and the alert last notified.
    var captureHealth: CaptureHealth?
    private var healthNotified: String?

    /// Every 30 s while capture runs or a class is on: probe, show the chip, notify once per
    /// failure. Nothing is restarted here: the learner decides.
    func checkCaptureHealth(now: Date = .now) {
        let classOn = nextClass.flatMap(ClassReminder.window).map {
            now >= $0.start.addingTimeInterval(-900) && now <= $0.end.addingTimeInterval(3600) } ?? false
        guard mode.running || classOn else { captureHealth = nil; return }
        Task {
            guard let h = try? await ToolRunner.json(CaptureHealth.self, "lecture", ["health", "--json"], timeout: 40) else { return }
            captureHealth = h
            if let a = h.alert, healthNotified != a.since {
                healthNotified = a.since
                ClassReminder.notify(id: "capture-health", title: "Capture problem: \(CaptureHealth.label(a.status))", body: a.message)
            } else if h.alert == nil {
                healthNotified = nil
            }
        }
    }

    /// Courses from the registry; keeps the stored choice while it still exists.
    func loadCourses() async {
        guard let l = try? await ToolRunner.json(CourseList.self, "lecture", ["courses", "--json"], timeout: 30) else { return }
        courseList = l
        let pick = CourseStore.resolve(l, stored: CourseStore.id)
        if pick?.id != course?.id || pick?.root != CourseStore.root { CourseStore.select(pick) }
        course = pick
    }

    /// Show another course: its lectures, study aids, review and Ask.
    func selectCourse(_ c: CourseList.Course) {
        guard c.id != course?.id else { return }
        CourseStore.select(c)
        course = c
        sessions = []; nextClass = nil
        Task { await loadSessions() }
    }

    /// Fold lectures into a destination lecture (background job; it rebuilds notes and cards).
    /// Undo the latest consolidation into a lecture (`lecture unmerge-lecture`).
    func unconsolidate(_ s: Session) {
        jobs.enqueue("Undo consolidation of \(s.displayTitle)", Paths.tool("lecture"),
                     ["unmerge-lecture", CourseForm.lectureArg(s)]) { [weak self] _ in
            Task { await self?.loadSessions() }
        }
    }

    func consolidate(into dest: Session, from sources: [Session]) {
        guard !sources.isEmpty else { return }
        jobs.enqueue("Consolidate into \(dest.displayTitle)", Paths.tool("lecture"),
                     ["merge-lectures", CourseForm.lectureArg(dest), "--from"] + sources.map(CourseForm.lectureArg)) { [weak self] _ in
            Task { await self?.loadSessions() }
        }
    }

    func refreshAll(probeVision: Bool) async {
        await loadCourses()
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
                        detail: ModeText.healthDetail(mode),
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
                            // screenpipe reports frames it captured, not frames it kept: windows outside
                            // Zoom and the allowed sites are dropped by design, so say so
                            detail: frameLive ? "watching · saves frames from Zoom and allowed sites only" : (frames?.message ?? "no data yet"),
                            fix: frames?.fix))
        }
        if let h = captureHealth, h.record.capture_running {
            let a = h.alert
            s.append(Signal(id: "health", label: "Health",
                            state: a == nil ? (h.record.status == "ok" ? .ok : .warn) : .down,
                            detail: a?.message ?? CaptureHealth.detail(h.record), fix: nil))
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
        do {
            let r = try await ToolRunner.run("sp-stop", [], timeout: 60)
            if !r.ok { lastError = "Stop failed: " + ErrorText.tail(r.stderr) }
        } catch { lastError = "Stop failed: " + ErrorText.short(error) }
        await refreshFast()
    }

    func setMode(_ m: String) async {
        busyCapture = true; defer { busyCapture = false }
        do {
            let r = try await ToolRunner.run("sp-mode", [m], timeout: 90)
            if !r.ok { lastError = "Mode change failed: " + ErrorText.tail(r.stderr) }
        } catch { lastError = "Mode change failed: " + ErrorText.short(error) }
        await refreshFast()
    }

    func openPrivacySettings() {
        let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!
        NSWorkspace.shared.open(url)
    }

    // MARK: lectures
    /// `lecture library-check`: copies of a class, wrong dates, shared ids (shown above the lecture list).
    var libraryCheck: LibraryCheck?

    func loadSessions() async {
        loadingSessions = true; defer { loadingSessions = false }
        do {
            sessions = try await ToolRunner.json(SessionList.self, "lecture", ["list", "--json", "--since", "90d"], timeout: 60).sessions
            sessionsError = nil
        } catch { sessionsError = ErrorText.short(error) }
        nextClass = try? await ToolRunner.json(NextClass.self, "lecture", ClassMode.liveCourseArgs(courseList) + ["next", "--json"], timeout: 30)
        libraryCheck = try? await ToolRunner.json(LibraryCheck.self, "lecture", ["library-check", "--json"], timeout: 30)
    }

    func build(_ s: Session) {
        guard let id = s.id else { return }
        jobs.enqueue("Build \(s.displayTitle)", Paths.tool("lecture"), ["build", id, "--since", "90d"]) { [weak self] _ in
            Task { await self?.loadSessions() }
        }
    }

    /// Last automatic import, shown on the Lectures screen.
    var autoImportNote: String? = nil

    /// Runs `lecture ingest` when Zoom's files land beside today's built class (Settings > After class).
    func checkAutoImport() {
        guard AutoImport.enabled, !jobs.busy else { return }
        let f = DateFormatter(); f.dateFormat = "yyyyMMdd"
        guard let p = AutoImport.pending(day: f.string(from: Date()), commit: true) else { return }
        autoImportNote = "Importing \(p.files.map(\.lastPathComponent).joined(separator: ", "))"
        ingest(p.lecture.lastPathComponent)
    }

    /// After class: judge the drafted questions and follow-ups (prunes the Live tab and qa.md).
    /// Skipped while capture runs; the script also waits for oMLX to be idle and resumes if stopped.
    func scoreLiveIfNeeded(_ folderName: String) {
        let day = String(folderName.prefix(8))
        let live = Paths.lectures.appendingPathComponent("live/\(day)")
        guard !mode.running, FileManager.default.fileExists(atPath: live.appendingPathComponent("questions.jsonl").path) else { return }
        jobs.enqueue("Judge live drafts (~1–2 h)", Paths.study.appendingPathComponent("tests/evals/run_live_scores.sh"), [live.path]) { _ in }
    }

    /// New holdout rows (added by ingest) are judged with the old and candidate rules, majority of 3,
    /// about an hour on the judge model. Skipped while capture runs; the script also waits for oMLX to be idle.
    func scoreHoldoutIfNeeded() {
        guard !mode.running,
              let sheet = try? CalibrationSheet.load("holdout.csv"),
              sheet.rows.contains(where: { ($0.cells["judge_v1x3_pass"] ?? "").isEmpty }) else { return }
        jobs.enqueue("Holdout scoring (judge, ~1 h)", Paths.study.appendingPathComponent("tests/evals/run_holdout_check.sh"), []) { _ in }
    }

    /// After class: transcript merge + WER, Q&A import (anonymised), prospective-answer scores.
    /// Browser mode: after Lecture Lens Studio ran a lecture, write its vault note and manifest.
    func finishStudio(_ folderName: String) {
        jobs.enqueue("Finish (Studio)", Paths.tool("lecture"), ["finish", folderName]) { [weak self] _ in
            Task { await self?.loadSessions() }
        }
    }

    /// CT-6: `lecture gapfill` for one gap from a recording file; existing lines never change.
    func gapfill(_ folderName: String, audio: String, from: String, to: String, done: @escaping @MainActor () -> Void) {
        jobs.enqueue("Fill gap from recording", Paths.tool("lecture"),
                     ["gapfill", folderName, "--audio", audio, "--from", from, "--to", to]) { _ in done() }
    }

    /// Restart audio capture only, once recorded audio is saved (`lecture audio-restart`).
    func restartAudio() {
        jobs.enqueue("Restart audio", Paths.tool("lecture"), ["audio-restart"]) { [weak self] _ in
            Task { await self?.refreshFast() }
            self?.checkCaptureHealth()
        }
    }

    func ingest(_ folderName: String) {
        jobs.enqueue("Import Zoom files", Paths.tool("lecture"), ["ingest", folderName]) { [weak self] _ in
            Task { await self?.loadSessions() }
            self?.scoreLiveIfNeeded(folderName)
            self?.scoreHoldoutIfNeeded()
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

/// Short, readable error text for banners (the full output stays in job logs).
enum ErrorText {
    static func short(_ e: Error, limit: Int = 220) -> String {
        tail((e as? LocalizedError)?.errorDescription ?? e.localizedDescription, limit: limit)
    }

    /// The last meaningful line(s) of a tool's stderr: tracebacks end with the actual error.
    static func tail(_ s: String, limit: Int = 220) -> String {
        let lines = s.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        let last: String = lines.last ?? "no details"
        return last.count > limit ? String(last.prefix(limit)) + "…" : last
    }
}

/// The live class and the capture mode: which course's calendar says when the class is, and a
/// warning when a pinned Portal mode would miss it.
enum ClassMode {
    /// `--course <default course>` when courses are kept apart, so `next` reads the live calendar
    /// whichever course is shown.
    static func liveCourseArgs(_ list: CourseList?) -> [String] {
        guard let l = list, l.implicit != true, !l.default.isEmpty else { return [] }
        return ["--course", l.default]
    }

    /// A warning from 18 hours before the class until its end when capture is pinned to Portal.
    static func warning(next: NextClass?, pinned: String?, now: Date) -> String? {
        guard let c = next, (pinned ?? "") == "portal", let w = ClassReminder.window(c) else { return nil }
        guard now >= w.start.addingTimeInterval(-18 * 3600), now <= w.end else { return nil }
        let f = DateFormatter(); f.dateFormat = "EEE HH:mm"
        f.timeZone = c.tz.flatMap(TimeZone.init(identifier:)) ?? .current
        return "The class on \(f.string(from: w.start)) is a meeting, but capture is pinned to Portal: the meeting would not be recorded and the live follower would not start. Switch to Auto."
    }
}
