import Foundation

/// Runs toolkit commands. Arguments are passed as an argv array — never through
/// `sh -c` — so user text (a question, a domain) can't be interpreted by a shell.
struct ToolResult: Sendable {
    let status: Int32
    let stdout: Data
    let stderr: String
    var ok: Bool { status == 0 }
}

enum ToolError: LocalizedError {
    case launch(String)
    case decode(String, String)
    var errorDescription: String? {
        switch self {
        case .launch(let m): return "Could not start tool: \(m)"
        case .decode(let cmd, let raw): return "\(cmd) returned output the app couldn't read: \(raw.prefix(300))"
        }
    }
}

enum ToolRunner {
    /// Run to completion and capture output. For short commands (doctor, list, ask).
    static func run(_ tool: String, _ args: [String], timeout: TimeInterval = 600) async throws -> ToolResult {
        try await withCheckedThrowingContinuation { cont in
            let p = Process()
            p.executableURL = Paths.tool(tool)
            p.arguments = args
            p.currentDirectoryURL = Paths.study
            p.environment = Paths.toolEnvironment
            let out = Pipe(), err = Pipe()
            p.standardOutput = out
            p.standardError = err
            // Drain both pipes concurrently so a chatty tool can't fill a buffer and block.
            let outBox = DataBox(), errBox = DataBox()
            out.fileHandleForReading.readabilityHandler = { h in outBox.append(h.availableData) }
            err.fileHandleForReading.readabilityHandler = { h in errBox.append(h.availableData) }
            p.terminationHandler = { proc in
                out.fileHandleForReading.readabilityHandler = nil
                err.fileHandleForReading.readabilityHandler = nil
                outBox.append(out.fileHandleForReading.readDataToEndOfFile())
                errBox.append(err.fileHandleForReading.readDataToEndOfFile())
                cont.resume(returning: ToolResult(status: proc.terminationStatus, stdout: outBox.data,
                                                  stderr: String(decoding: errBox.data, as: UTF8.self)))
            }
            do { try p.run() } catch { cont.resume(throwing: ToolError.launch("\(tool): \(error.localizedDescription)")); return }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { if p.isRunning { p.terminate() } }
        }
    }

    /// Run and decode the tool's one-JSON-document stdout (the --json contract).
    static func json<T: Decodable>(_ type: T.Type, _ tool: String, _ args: [String], timeout: TimeInterval = 600) async throws -> T {
        let r = try await run(tool, args, timeout: timeout)
        do {
            return try JSONDecoder().decode(T.self, from: r.stdout)
        } catch {
            let raw = String(decoding: r.stdout, as: UTF8.self)
            throw ToolError.decode("\(tool) \(args.joined(separator: " "))", raw.isEmpty ? r.stderr : raw)
        }
    }

    /// Launch and stream lines (for long jobs). Returns the exit status.
    static func stream(_ executable: URL, _ args: [String], onLine: @escaping @Sendable (String) -> Void) async -> Int32 {
        await withCheckedContinuation { cont in
            let p = Process()
            p.executableURL = executable
            p.arguments = args
            p.currentDirectoryURL = executable.deletingLastPathComponent()
            p.environment = Paths.toolEnvironment
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe
            let buffer = LineBuffer(onLine: onLine)
            pipe.fileHandleForReading.readabilityHandler = { h in buffer.feed(h.availableData) }
            p.terminationHandler = { proc in
                pipe.fileHandleForReading.readabilityHandler = nil
                buffer.feed(pipe.fileHandleForReading.readDataToEndOfFile())
                buffer.flush()
                cont.resume(returning: proc.terminationStatus)
            }
            do { try p.run() } catch { onLine("✗ could not start: \(error.localizedDescription)"); cont.resume(returning: -1) }
        }
    }
}

final class DataBox: @unchecked Sendable {
    private let lock = NSLock()
    private(set) var data = Data()
    func append(_ d: Data) { lock.lock(); data.append(d); lock.unlock() }
}

final class LineBuffer: @unchecked Sendable {
    private let lock = NSLock()
    private var pending = ""
    private let onLine: @Sendable (String) -> Void
    init(onLine: @escaping @Sendable (String) -> Void) { self.onLine = onLine }
    func feed(_ d: Data) {
        guard !d.isEmpty else { return }
        lock.lock()
        pending += String(decoding: d, as: UTF8.self).replacingOccurrences(of: "\r", with: "\n")
        var lines = pending.components(separatedBy: "\n")
        pending = lines.removeLast()
        lock.unlock()
        for l in lines where !l.trimmingCharacters(in: .whitespaces).isEmpty { onLine(l) }
    }
    func flush() {
        lock.lock(); let rest = pending; pending = ""; lock.unlock()
        if !rest.isEmpty { onLine(rest) }
    }
}
