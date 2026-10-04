// swift-tools-version: 6.0
// Lecture Lens — native macOS front end for the Lecture Lens toolkit.
// Built with `swift build`; bundled into a .app by ../build_app.sh (see ADR-002).
import PackageDescription

let package = Package(
    name: "LectureLens",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(
            name: "LectureLens",
            path: "Sources/LectureLens"
        ),
        .testTarget(
            name: "LectureLensTests",
            dependencies: ["LectureLens"],
            path: "Tests/LectureLensTests"
        ),
    ]
)
