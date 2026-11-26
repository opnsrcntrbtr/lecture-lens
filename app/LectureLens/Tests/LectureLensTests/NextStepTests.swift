import Foundation
import Testing
@testable import LectureLens

@Test func nextStepsDecodeAndLabel() throws {
    let j = #"{"ok": true, "top": {"kind": "relearn", "title": "Relearn 4 item(s)", "detail": "d", "minutes": 2.0, "target": "mastery"}, "steps": [{"kind": "relearn", "title": "Relearn 4 item(s)", "detail": "d", "minutes": 2.0, "target": "mastery"}, {"kind": "tutor", "title": "Work through a miss", "detail": "d", "minutes": 5, "target": "tutor", "ref": "mastery:c:i"}]}"#
    let n = try JSONDecoder().decode(NextSteps.self, from: Data(j.utf8))
    #expect(NextStepText.button(n.top!) == nil && NextStepText.minutes(n.top!) == "about 2 min")
    #expect(n.steps[1].ref == "mastery:c:i" && NextStepText.button(n.steps[1]) == "Work it out")
    let done = NextSteps.Step(kind: "done", title: "All caught up", detail: "", minutes: 0, target: "none", ref: nil)
    #expect(NextStepText.button(done) == nil && NextStepText.minutes(done) == nil)
}
