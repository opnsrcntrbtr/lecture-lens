import Foundation
import Testing
@testable import LectureLens

// Contract tests: the app must decode what the toolkit's --json modes print,
// including when optional keys are missing (02-system-design.md §3.1).

@Test func decodesDoctor() throws {
    let json = #"{"ok": false, "checks": [{"id": "frames", "ok": false, "level": "error", "message": "0 frames", "fix": "Allow Screen Recording"}, {"id": "vision", "ok": true, "level": "ok", "message": "reads slides", "fix": null}]}"#
    let d = try JSONDecoder().decode(DoctorResult.self, from: Data(json.utf8))
    #expect(d.checks.count == 2)
    #expect(d.checks[0].fix == "Allow Screen Recording")
    #expect(d.checks[1].fix == nil)
}

@Test func decodesStoppedMode() throws {
    let m = try JSONDecoder().decode(ModeState.self, from: Data(#"{"running":false,"mode":"stopped","pinned":"","autoswitch":false}"#.utf8))
    #expect(!m.running && m.mode == "stopped")
}

@Test func decodesAskErrorWithoutAnswer() throws {
    let r = try JSONDecoder().decode(AskResult.self, from: Data(#"{"ok": false, "error": "screenpipe is not reachable"}"#.utf8))
    #expect(!r.ok && r.answer == nil && r.sources == nil)
}

@Test func decodesAskWithSources() throws {
    let json = #"{"ok":true,"question":"q","answer":"a [1]","queries":["q"],"sources":[{"n":1,"ts":"2026-09-28T10:00:00Z","src":"audio","where":"Zoom","text":"t"}],"model":"m","prompt_version":"ask-v1","latency_s":2.5}"#
    let r = try JSONDecoder().decode(AskResult.self, from: Data(json.utf8))
    #expect(r.sources?.first?.where == "Zoom")
    #expect(r.prompt_version == "ask-v1")
}

@Test func decodesAskCitationChecks() throws {
    let json = #"{"ok":true,"answer":"a [7]","sources":[],"citation_warnings":["cites source(s) 7 but only 1 were given"],"citation_coverage":0.5}"#
    let r = try JSONDecoder().decode(AskResult.self, from: Data(json.utf8))
    #expect(r.citation_warnings?.count == 1)
    #expect(r.citation_coverage == 0.5)
}

@Test func decodesSessionsWithMissingFields() throws {
    let json = #"{"sessions":[{"id":"a1","title":"Week 3","built":true,"folder":"/x/a1_Week-3"},{"built":false,"folder":"/x/b"}]}"#
    let l = try JSONDecoder().decode(SessionList.self, from: Data(json.utf8))
    #expect(l.sessions[1].key == "/x/b")
    #expect(l.sessions[1].displayTitle == "Untitled lecture")
}

@Test func decodesBothSummaryShapes() throws {
    let deep = #"{"suite":"deepeval-study","generator":"g","judge":"j","metrics":{"ask:Faithfulness":{"mean":0.91,"pass":30,"n":36,"threshold":0.8}}}"#
    let s1 = try JSONDecoder().decode(EvalSummary.self, from: Data(deep.utf8))
    #expect(s1.metrics?["ask:Faithfulness"]?.pass == 30)
    #expect(s1.metrics?["ask:Faithfulness"]?.ci95 == nil)
    let withCI = #"{"metrics":{"ask:Faithfulness":{"mean":0.91,"pass":5,"n":6,"ci95":[0.436,0.97]}}}"#
    #expect(try JSONDecoder().decode(EvalSummary.self, from: Data(withCI.utf8)).metrics?["ask:Faithfulness"]?.ci95?.first == 0.436)
    let vision = #"{"model":"m","by_config":{"think_off":{"title_exact":{"pass":30,"n":36},"latency_p50":4.2,"note":"x"}}}"#
    let s2 = try JSONDecoder().decode(EvalSummary.self, from: Data(vision.utf8))
    guard case .agg(let p, let n, _) = s2.by_config?["think_off"]?["title_exact"] else { Issue.record("not agg"); return }
    #expect(p == 30 && n == 36)
    guard case .other = s2.by_config?["think_off"]?["note"] else { Issue.record("string should be .other"); return }
}

@Test func envFilePreservesCommentsAndQuotes() throws {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("env-\(UUID().uuidString)")
    try "# comment\nOMLX_MODEL=old\nSECRET=keep\n".write(to: url, atomically: true, encoding: .utf8)
    var e = EnvFile(url: url)
    e.set("OMLX_MODEL", "new")
    e.set("SP_URL_ALLOW", "a.com, b.com")
    try e.save()
    let text = try String(contentsOf: url, encoding: .utf8)
    #expect(text.contains("# comment"))
    #expect(text.contains("OMLX_MODEL=new"))
    #expect(text.contains("SECRET=keep"))
    #expect(text.contains("SP_URL_ALLOW=\"a.com, b.com\""))
    #expect(EnvFile(url: url).value("SP_URL_ALLOW") == "a.com, b.com")
}

@MainActor @Test func slidesMarkdownParses() throws {   // SlidesPane is a view: main-actor isolated
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent("lec-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    try """
    # Slides & visuals — T

    ## 1. at 00:01:00

    ![[01_000100.jpg]]

    A funnel diagram.

    > On-screen text: Funnel
    ## 2. at 00:05:30

    ![[02_000530.jpg]]

    """.write(to: dir.appendingPathComponent("slides.md"), atomically: true, encoding: .utf8)
    let s = SlidesPane.load(dir)
    #expect(s.count == 2)
    #expect(s[0].at == "00:01:00" && s[0].text.contains("funnel diagram"))
    #expect(s[1].image.lastPathComponent == "02_000530.jpg")
}


@Test func classReminderStages() throws {
    let json = #"{"week": 9, "title": "Watering the garden", "date": "2030-01-01", "start": "10:00", "end": "13:00", "tz": "Asia/Kolkata"}"#
    let c = try JSONDecoder().decode(NextClass.self, from: Data(json.utf8))
    let w = try #require(ClassReminder.window(c))
    #expect(ClassReminder.due(c, now: w.start.addingTimeInterval(-600), liveRunning: false, sent: []) == nil)
    #expect(ClassReminder.due(c, now: w.start.addingTimeInterval(-120), liveRunning: false, sent: []) == .soon)
    #expect(ClassReminder.due(c, now: w.start.addingTimeInterval(60), liveRunning: false, sent: []) == nil)
    #expect(ClassReminder.due(c, now: w.start.addingTimeInterval(600), liveRunning: false, sent: []) == .late)
    #expect(ClassReminder.due(c, now: w.start.addingTimeInterval(600), liveRunning: true, sent: []) == nil)
    let sent: Set<String> = [ClassReminder.key(c, .late)]
    #expect(ClassReminder.due(c, now: w.start.addingTimeInterval(900), liveRunning: false, sent: sent) == nil)
    #expect(ClassReminder.due(c, now: w.end.addingTimeInterval(60), liveRunning: false, sent: []) == nil)
}

@Test func decodesCitation() throws {
    let json = #"{"ok": true, "t": "2030-01-01T05:05:00+00:00", "sources": [{"id": "S1", "title": "Garden guide", "fact": "Mulch holds water.", "url": "https://example.org/g", "date": null}], "moments": [{"t": "2030-01-01T05:04:00+00:00", "text": "Raised beds drain faster.", "shared": ["beds", "raised"]}], "slide": {"t": "2030-01-01T05:02:00+00:00", "title": "Watering raised beds"}}"#
    let c = try JSONDecoder().decode(Citation.self, from: Data(json.utf8))
    #expect(c.sources?.first?.url == "https://example.org/g")
    #expect(c.moments?.first?.shared == ["beds", "raised"])
    #expect(c.slide?.title == "Watering raised beds")
    let bare = try JSONDecoder().decode(Citation.self, from: Data(#"{"ok": false, "error": "unreadable time: x"}"#.utf8))
    #expect(bare.sources == nil && bare.error != nil)
}

@Test func citationArgsAreAnArgv() {
    let a = Citation.args(t: "11:12 AM", text: "Why mulch? Holds water.", sources: ["S1", "S2"], day: "20300101")
    #expect(a == ["cite", "--json", "--t", "11:12 AM", "--text", "Why mulch? Holds water.", "--sources", "S1,S2", "--day", "20300101"])
    #expect(!Citation.args(t: "x", text: "y", sources: [], day: nil).contains("--sources"))
}

@Test func askCitationSentences() {
    let s = CitationText.sentences("Mulch holds water [1]. Beds drain fast [12]. Both help [2, 1].", citing: 1)
    #expect(s == ["Mulch holds water [1]", "Both help [2, 1]"])
}

@Test func curatedRoundsAndAsked() throws {
    let lines = [
        #"{"t": "2030-01-01T05:00:00+00:00", "type": "clarify", "q": "Generated?", "answer": "a", "sources": []}"#,
        #"{"t": "2030-01-01T05:10:00+00:00", "type": "clarify", "q": "How often should raised beds be watered in summer?", "answer": "a", "sources": ["S1"], "curated": true, "round": 1, "part": "now"}"#,
        #"{"t": "2030-01-01T05:35:00+00:00", "type": "practical", "q": "Which sensor tells us the soil is dry?", "answer": "b", "sources": [], "curated": true, "round": 2, "part": "later"}"#,
    ]
    let qs = try lines.map { try JSONDecoder().decode(Drafted.self, from: Data($0.utf8)) }
    let r = CuratedQuestions.rounds(qs)
    #expect(r.map(\.round) == [2, 1] && r[1].rows.first?.part == "now")
    let thread = try JSONDecoder().decode(QAThread.self, from: Data(#"{"id": "a", "by": "You", "time": "11:12 AM", "q": "How often should raised beds be watered in the summer heat?", "answers": []}"#.utf8))
    #expect(CuratedQuestions.askedIn(qs[1].q, threads: [thread])?.time == "11:12 AM")
    #expect(CuratedQuestions.askedIn(qs[2].q, threads: [thread]) == nil)
    let now = Date()
    #expect(CuratedQuestions.due(enabled: true, fresh: true, last: nil, now: now))
    #expect(!CuratedQuestions.due(enabled: true, fresh: true, last: now.addingTimeInterval(-600), now: now))
    #expect(!CuratedQuestions.due(enabled: true, fresh: false, last: nil, now: now))
}

@Test func decodesAfterClassAndRetryRule() throws {
    let json = #"{"ok": false, "lecture": "x", "at": "2030-01-01T09:00:00+00:00", "steps": [{"name": "build", "ok": true, "note": "done"}, {"name": "names", "ok": false, "note": "2 line(s) still name an attendee in notes.md"}]}"#
    let r = try JSONDecoder().decode(AfterClassReport.self, from: Data(json.utf8))
    #expect(r.steps.map(\.ok) == [true, false] && r.error == nil)
    let now = Date()
    #expect(!CuratedQuestions.due(enabled: true, fresh: true, last: nil, retryAt: now.addingTimeInterval(60), now: now))
    #expect(CuratedQuestions.due(enabled: true, fresh: true, last: nil, retryAt: now.addingTimeInterval(-1), now: now))
    let c = try JSONDecoder().decode(NextClass.self, from: Data(#"{"week": 1, "title": "t", "date": "2030-01-01", "start": "10:00", "end": "13:00", "tz": "Europe/London"}"#.utf8))
    let w = try #require(ClassReminder.window(c))
    #expect(Calendar(identifier: .gregorian).dateComponents(in: TimeZone(identifier: "Europe/London")!, from: w.start).hour == 10)
}
