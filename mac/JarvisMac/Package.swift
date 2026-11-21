// swift-tools-version:6.0
import PackageDescription

// JARVIS for Mac: a native macOS client of JARVIS Core (app/server.py):
// overview, chat, home, media, alerts, skills & fixes, Donna, and a menu bar
// panel. Build and install with ./build.sh.
let package = Package(
    name: "JarvisMac",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(name: "JarvisMac", path: "Sources/JarvisMac")
    ],
    swiftLanguageModes: [.v5]
)
