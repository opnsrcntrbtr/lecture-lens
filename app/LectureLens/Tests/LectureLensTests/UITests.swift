import Foundation
import Testing
@testable import LectureLens

@Test func csvRoundTripsQuotesCommasAndNewlines() {
    let rows = [["id", "input", "note"], ["a1", "Is \"X\", or Y?\nSecond line", ""], ["b2", "plain", "ok"]]
    let parsed = CSV.parse(CSV.write(rows))
    #expect(parsed == rows)
}

@Test func calibrationReportDecodes() throws {
    let json = #"{"labelled": 2, "of": 50, "overall": {"n": 2, "agreement": 0.5, "kappa": 0.0, "false_pass_rate": 1.0, "false_fail_rate": 0.0}, "by_metric": {"Faithfulness": {"n": 2, "agreement": 0.5, "kappa": null, "false_pass_rate": null, "false_fail_rate": 0.0}}}"#
    let r = try JSONDecoder().decode(CalibrationReport.self, from: Data(json.utf8))
    #expect(r.labelled == 2 && r.by_metric["Faithfulness"]?.kappa == nil)
}

@Test func textScaleStepsAndFonts() {
    #expect(TextScale.next(1.0) == 1.15)
    #expect(TextScale.previous(1.0) == 0.85)
    #expect(TextScale.next(2.0) == 2.0)
    #expect(TextScale.base(.body) == 13)
}
