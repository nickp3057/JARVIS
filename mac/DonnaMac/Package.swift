// swift-tools-version:6.0
import PackageDescription

// Donna for Mac: a native window onto Donna (brain/donna.py) through Core's
// /api/donna endpoints. Build and install with ./build.sh.
let package = Package(
    name: "DonnaMac",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(name: "DonnaMac", path: "Sources/DonnaMac")
    ],
    swiftLanguageModes: [.v5]
)
