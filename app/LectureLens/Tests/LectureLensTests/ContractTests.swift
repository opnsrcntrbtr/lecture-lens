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
    #expect(ClassReminder.due(c, now: w.start.addingTimeInterval(30), liveRunning: false, sent: []) == nil)
    // late: first within two minutes, then once a minute for ten minutes (CT-1)
    var sent: Set<String> = [], fired: [Int] = []
    for sec in stride(from: 0, through: 900, by: 30) {
        let now = w.start.addingTimeInterval(TimeInterval(sec))
        if ClassReminder.due(c, now: now, liveRunning: false, sent: sent) == .late {
            sent.insert(ClassReminder.lateKey(c, now: now)); fired.append(sec)
        }
    }
    #expect(fired.first! <= 120 && fired.count == 10 && fired.last! <= 600 && fired[1] - fired[0] == 60)
    #expect(ClassReminder.due(c, now: w.start.addingTimeInterval(180), liveRunning: true, sent: []) == nil)
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

@Test func namesAuditAndCandidatesDecode() throws {
    let a = try JSONDecoder().decode(NamesAudit.self, from: Data(#"{"ok": true, "guard_version": "2", "files_with_names": 3, "removed_total": 9, "review_total": 4, "held_for_review": 1}"#.utf8))
    #expect(a.files_with_names == 3 && a.files_rewritten == nil)
    let rows = try JSONDecoder().decode([NameCandidate].self, from: Data(#"[{"token": "Zorvan", "total": 3, "groups": ["20300101-1000_Garden"], "layer": "roster-phonetic", "score": 0.82, "decision": "review", "held": false, "learner": null}, {"token": "Plaxo", "total": 9, "groups": ["a", "b"], "layer": "capitalised", "score": 0.0, "decision": "review"}]"#.utf8))
    #expect(rows[0].needsDecision && !rows[1].needsDecision)
    #expect(NamesReview.decideArgs(["Zorvan": "remove", "Plaxo": "keep"]) == ["names", "decide", "--json", "--remove", "Zorvan", "--keep", "Plaxo"])
}

@Test func decodesCitationsV3() throws {
    let json = #"{"ok": true, "t": "2030-01-01T05:05:00+00:00", "sources": [], "moments": [], "slide": null, "answer_id": "a1b2c3", "checker": {"name": "lexical-overlap", "version": "1", "threshold": 0.6, "calibrated": false}, "sentences": [{"i": 0, "text": "Raised beds drain faster.", "status": "related", "citations": [{"line_ids": ["20300101-0500.6bf7/t/4"], "t0": 340.0, "t_abs": "2030-01-01T05:05:40+00:00", "source": "transcript", "quote": "Raised beds drain faster, so water raised beds more often.", "score_lex": 3.2, "score_dense": null, "support_p": 0.75, "slide_thumb": null}]}, {"i": 1, "text": "Penguins migrate.", "status": "unsupported", "citations": []}]}"#
    let c = try JSONDecoder().decode(Citation.self, from: Data(json.utf8))
    #expect(c.answerId == "a1b2c3" && c.checker?.calibrated == false)
    #expect(c.sentences?.map(\.status) == ["related", "unsupported"])
    #expect(c.sentences?[0].citations[0].lineIds == ["20300101-0500.6bf7/t/4"])
    #expect(CitationStatus.label("supported").0 == "Supported" && CitationStatus.label("unsupported").0 == "No support found")
    let ev = Citation.Cited(lineIds: ["x/e/S1"], t0: nil, tAbs: nil, source: "evidence", quote: "q", supportP: nil, slideThumb: nil)
    #expect(CitationStatus.when(ev) == "source")
    #expect(Citation.args(t: "x", text: "q a", sources: [], day: nil, answer: "a").suffix(2) == ["--answer", "a"])
}

@Test func decodesSupportPairs() throws {
    let r = try JSONDecoder().decode(SupportPairs.self, from: Data(#"{"ok": true, "labelled": 3, "target": 100, "remaining": 2, "todo": [{"i": 0, "sentence": "Mulch keeps soil moist.", "passage": "Mulch keeps soil moist through dry weeks."}]}"#.utf8))
    #expect(r.labelled == 3 && r.todo?.first?.i == 0)
    #expect(SupportPairs.labelArgs(i: 4, supports: false) == ["checker", "label", "--json", "--i", "4", "--label", "0"])
}

@Test func liveDayPickerListsPastFolders() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("live-\(UUID().uuidString)")
    for n in ["20300101", "20300108", "20300115", "notes"] {
        try FileManager.default.createDirectory(at: root.appendingPathComponent(n), withIntermediateDirectories: true)
    }
    let today = try #require(LiveData.date("20300115"))
    #expect(LiveData.pastDays(root: root, today: today) == ["20300108", "20300101"])
    #expect(LiveData.dayLabel("20300108").hasSuffix("8 Jan"))
}

@Test func askResultCarriesRetrievalNote() throws {
    let r = try JSONDecoder().decode(AskResult.self, from: Data(#"{"ok": true, "question": "q", "answer": "a [1]", "sources": [], "model": "m", "prompt_version": "ask-v1", "latency_s": 1.2, "retrieval": "lecture index", "note": "screenpipe is not running; answered from built lectures only"}"#.utf8))
    #expect(r.retrieval == "lecture index" && r.note?.hasPrefix("screenpipe is not running") == true)
}

@Test func decodesCaptureHealthAndGates() throws {
    let h = try JSONDecoder().decode(CaptureHealth.self, from: Data(#"{"ok": true, "record": {"t": "2030-01-05T04:31:30+00:00", "capture_running": true, "audio_chunk_age_s": 12.0, "audio_level_db": -120.0, "frame_age_s": 4.0, "frames_expected": true, "status": "silent_audio"}, "alert": {"status": "silent_audio", "since": "2030-01-05T04:30:00+00:00", "lasted_s": 90, "message": "Audio is digital silence"}, "mode": "live", "in_class": true}"#.utf8))
    #expect(h.alert?.status == "silent_audio" && CaptureHealth.label("no_frames") == "no screen frames")
    #expect(CaptureHealth.detail(h.record).hasPrefix("silent audio · audio 12 s ago"))
    let r = try JSONDecoder().decode(AfterClassReport.self, from: Data(#"{"ok": true, "steps": [], "gates": [{"name": "names_left", "value": 0, "threshold": 0, "ok": true}, {"name": "title_from_candidates", "value": "Garden", "threshold": "calendar|portal|first slide", "ok": false}, {"name": "quiz_lint_pass_rate", "value": null, "threshold": 1.0, "ok": true}], "needs_review": true}"#.utf8))
    #expect(r.needs_review == true && r.gates?.count == 3 && r.gates?[1].value?.description == "Garden")
    #expect(r.gates?[0].value?.description == "0")
}

@Test func capstoneContracts() throws {
    let s = try JSONDecoder().decode(CapStatus.self, from: Data(#"{"ok": true, "locked": false, "artefacts": [{"artefact_id": "prd", "kind": "prd", "path": "/tmp/prd.md", "sha256": "ab", "version": 2, "updated": "2030-01-05T04:30:00+00:00"}], "findings": [{"finding_id": "f1", "artefact_id": "prd", "artefact_sha": "ab", "quote": "The agent should be fast.", "criterion_id": "requirements", "check_id": "acceptance_testable", "verdict": "fail", "issue": "A tester could not decide.", "question_to_learner": "What would a tester measure?", "hint_level": 0, "lecture_refs": [], "evidence_refs": [], "reviewer_view": "general", "confidence": 1.0, "stale": false}], "rubric": "public_composite"}"#.utf8))
    #expect(s.artefacts?.first?.version == 2 && s.findings?.first?.check_id == "acceptance_testable")
    let d = try JSONDecoder().decode(CapDrill.self, from: Data(#"{"ok": true, "seed": 3, "scenario_id": "sim-3", "format": "micro", "brief": "You are incident commander.", "injects": [{"inject_id": "i1", "t_offset_min": 0, "role": "support lead", "text": "Customers quote a policy you do not have."}]}"#.utf8))
    #expect(d.seed == 3 && d.injects?.count == 1)
    #expect(Capstone.ratingsArg(["time_to_declare": "S", "severity_call": "P"]) == "severity_call=P,time_to_declare=S")
    #expect(Capstone.decideArgs(inject: "i1", severity: "SEV1", containment: "pause", customer: "c", team: "i", regulator: "", golden: "no", rationale: "r").prefix(5) == ["capstone", "sim", "decide", "--inject", "i1"])
    #expect(Capstone.label("unit_econ_heavy_user") == "Unit Econ Heavy User")
}

@Test func questionLabelsContract() throws {
    let r = try JSONDecoder().decode(QuestionLabels.self, from: Data(#"{"ok": true, "day": "20300105", "total": 3, "labelled": 1, "todo": [{"i": 1, "q": "How often should raised beds be watered?", "answer": "", "round": 2, "type": "clarify", "t": "x"}], "would_ask_share": 1.0, "duplicate_share": 0.0, "answered_share": 0.0}"#.utf8))
    #expect(r.todo?.first?.i == 1 && r.labelled == 1)
    #expect(QuestionLabels.labelArgs(day: "20300105", i: 1, wouldAsk: true, duplicate: false, answered: true).suffix(6) == ["--would-ask", "y", "--duplicate", "n", "--answered", "y"])
}

@Test func judgeCardRowsAndBest() throws {
    let rows = try JSONDecoder().decode([JudgeCardRow].self, from: Data(#"[{"criterion": "Relevance", "rubric_version": "v1", "n_labels": 18, "valid_rate": 1.0, "tpr": 0.5, "tpr_ci": [0.3, 0.7], "tnr": 0.5, "tnr_ci": [0.2, 0.8], "kappa": 0.0, "raw_agreement": 0.5, "status": "exploratory"}, {"criterion": "Relevance", "rubric_version": "v2", "n_labels": 18, "valid_rate": 1.0, "tpr": 0.93, "tpr_ci": [0.7, 0.99], "tnr": 0.5, "tnr_ci": [0.2, 0.8], "kappa": 0.47, "raw_agreement": 0.83, "status": "exploratory"}]"#.utf8))
    #expect(JudgeCard.best(rows).map(\.rubric_version) == ["v2"])
    #expect(JudgeCard.rate("TPR", 0.93, [0.7, 0.99]) == "TPR 0.93 [0.70–0.99]" && JudgeCard.rate("TNR", nil, nil) == "TNR –")
}

@Test func curatedV2RowsDecodeAndLabel() throws {
    let d = try JSONDecoder().decode(Drafted.self, from: Data(#"{"t": "2030-01-05T05:00:00+00:00", "type": "challenge", "q": "Does mulch help in clay?", "answer": "", "sources": [], "curated": true, "round": 2, "part": "now", "pile": "ask", "requested_level": "analyse", "q_id": "r2q1"}"#.utf8))
    #expect(CuratedQuestions.prefix(d) == "challenge · analyse: ")
    var s = d; s.pile = "self_check"
    #expect(CuratedQuestions.prefix(s) == "self-check · challenge: ")
}

@Test func practiceAndPrequestionContracts() throws {
    let v = try JSONDecoder().decode(PracticeView.self, from: Data(#"{"ok": true, "session_id": "s1", "kind": "quiz", "done": false, "index": 0, "of": 2, "stem": "What should they change?", "options": ["a", "b", "c", "d"], "removed": null, "hint_level": 0, "answered": false, "closed_book": false}"#.utf8))
    #expect(v.key == nil && v.options?.count == 4)
    #expect(!Practice.canReveal("too short", skip: false) && Practice.canReveal("long enough attempt here", skip: false) && Practice.canReveal("", skip: true))
    let p = try JSONDecoder().decode(PracticeProgress.self, from: Data(#"{"ok": true, "closed_book_items": 4, "closed_book_accuracy": 0.75, "sure_and_wrong": 1, "skip_rate": 0.25, "assisted_reveals": 4}"#.utf8))
    #expect(Practice.progressText(p) == "Closed-book: 75% of 4 · 1 sure but wrong · skipped attempts 25%")
    let q = try JSONDecoder().decode(Prequestions.self, from: Data(#"{"ok": true, "prequestions": [{"pq_id": "pq1", "agenda_item": "Soil", "text": "Why mulch?", "guess": "shade", "guess_t": "x", "late": false, "resolved": {"status": "answered", "line_ids": ["a"], "quote": "Mulch shades soil.", "t": "2030-01-05T05:00:00+00:00"}, "got_it": null, "became_card": true}]}"#.utf8))
    #expect(q.prequestions?.first?.resolved?.status == "answered")
}

@Test func capstoneReadinessPanelProgressContracts() throws {
    let r = try JSONDecoder().decode(CapReadiness.self, from: Data(#"{"ok": true, "items": [{"item_id": "eval_golden_set", "gate": "evaluation_coverage", "question": "Q?", "rating": "demonstrated", "evidence": {"artefact_id": "prd", "quote": "a quote here now", "artefact_sha": "x"}}], "gates": {"rollback": {"items": 1, "demonstrated": 0, "partial": 0, "unrated": 1}}, "ready": false}"#.utf8))
    #expect(r.items?.first?.evidence?.quote == "a quote here now" && r.gates?["rollback"]?.unrated == 1)
    let p = try JSONDecoder().decode(CapPanel.self, from: Data(#"{"ok": true, "seed": 3, "session_type": "panel", "pressure": "neutral", "cold": false, "time_box_min": 25, "time_left_min": 24.5, "awaiting_answer": true, "done": false, "turns": [{"turn": 0, "speaker": "methodologist", "role": "faculty methodologist", "text": "How?", "is_followup": false, "criterion": "Measurable goals"}], "self_scored": false, "criteria": ["measurable_goals"], "score": [{"criterion_id": "response_to_questions", "checks": [{"check_id": "not_defensive", "verdict": "pass", "answer_quote": null}], "self_score": 3}], "metrics": {"questions": 1, "followups": 0, "stacked_question_rate": 0, "followup_grounded_share": null, "rubric_coverage": 0.11, "concessions_blocked": 0, "template_fallbacks": 0, "cold": false}}"#.utf8))
    #expect(p.turns?.count == 1 && p.score?.first?.self_score == 3)
    #expect(CapText.scoresArg(["b": 2, "a": 4]) == "a=4,b=2")
    let g = try JSONDecoder().decode(CapProgress.self, from: Data(#"{"ok": true, "checker_card": [{"criterion": "stop_condition", "n_labels": 2, "valid_share": 1.0, "valid_ci": [0.3, 1.0], "specific_share": 1.0, "tpr": null, "tpr_ci": null, "tnr": null, "tnr_ci": null, "bench_at": null, "judge_model": null, "status": "exploratory", "why": ["2 of 60 learner labels"]}], "conduct": [], "outcomes": {"cold_mocks": [{"session": "s", "checks_passed": 0.5}], "drills": [], "self_minus_critic": {"panel": 0.5, "drill": null, "note": "n"}, "unresolved_tigers": 1}, "telemetry": {"hints": 4, "attempted_first_share": 0.75, "hint_levels": {"0": 1}, "findings": 2, "artefact_changed_after_share": 0.5, "days_since_cold_mock": 2}}"#.utf8))
    #expect(CapText.progressLine(g) == "attempt before hint 75% of 4 hints · last cold mock 50% of checks · self minus critic +0.5 · 1 Tigers without a mitigation · 0 of 1 checkers past their accuracy bar")
}
