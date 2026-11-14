import Foundation

/// `lecture curate --json`: one round of curated top questions for today's live class.
struct CurateResult: Decodable, Sendable {
    let ok: Bool
    let added: Int?
    let round: Int?
    let note: String?
    let error: String?
}

/// Curated top questions in rounds, and whether each was asked in the Q&A (same words).
enum CuratedQuestions {
    static let autoKey = "autoCurateLive"
    static let interval: TimeInterval = 25 * 60

    private static let stop: Set<String> = ["that", "this", "with", "from", "have", "what", "when", "which", "should",
                                            "would", "could", "about", "there", "their", "they", "then", "than", "into",
                                            "your", "will", "does", "also", "only", "more", "some", "such", "each"]

    static func words(_ s: String) -> Set<String> {
        Set(s.lowercased().split(whereSeparator: { !($0.isLetter || $0.isNumber || $0 == "-") })
            .map(String.init).filter { $0.count >= 4 && !stop.contains($0) })
    }

    static func jaccard(_ a: String, _ b: String) -> Double {
        let x = words(a), y = words(b)
        guard !x.isEmpty, !y.isEmpty else { return 0 }
        return Double(x.intersection(y).count) / Double(x.union(y).count)
    }

    /// The thread that asked this question, if one shares enough words (0.4, as curate.py).
    static func askedIn(_ q: String, threads: [QAThread]) -> QAThread? {
        guard let best = threads.max(by: { jaccard(q, $0.q) < jaccard(q, $1.q) }), jaccard(q, best.q) >= 0.4 else { return nil }
        return best
    }

    /// Rounds newest first, each with its rows in file order.
    static func rounds(_ questions: [Drafted]) -> [(round: Int, rows: [Drafted])] {
        let cur = questions.filter { $0.curated == true }
        let by = Dictionary(grouping: cur, by: { $0.round ?? 0 })
        return by.keys.sorted(by: >).map { ($0, by[$0] ?? []) }
    }

    static let retryAfterFailure: TimeInterval = 5 * 60

    /// Auto-curate is due: enabled, the follower is fresh, the last good round is old enough,
    /// and a failed round is not being retried too soon.
    static func due(enabled: Bool, fresh: Bool, last: Date?, retryAt: Date? = nil, now: Date = .now) -> Bool {
        enabled && fresh && (last.map { now.timeIntervalSince($0) >= interval } ?? true) && (retryAt.map { now >= $0 } ?? true)
    }
}
