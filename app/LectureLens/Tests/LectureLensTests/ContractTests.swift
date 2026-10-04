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
