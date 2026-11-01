import SwiftUI

/// The Obsidian vault as a map (`GET /api/knowledge/graph`): folders, notes,
/// the Profile's topics and every [[wikilink]], titles only.
struct KnowledgeGraph: Decodable {
    struct Node: Decodable {
        let id: String
        let title: String
        let kind: String   // root | folder | note | topic
        let weight: Int
    }
    struct Edge: Decodable {
        let from: String
        let to: String
        let kind: String   // tree | link | topic
    }
    let nodes: [Node]
    let edges: [Edge]
}

/// What JARVIS knows, drawn like Obsidian's graph view: it grows out from the
/// middle and settles (a small force layout, run live for a couple of
/// seconds), then holds still. Pinch and drag to look around; tap a note to
/// see its links, tap its name to open it.
struct MemoryMapView: View {
    @EnvironmentObject private var settings: AppSettings
    @Environment(\.dismiss) private var dismiss
    /// Opens a note in the vault browser (the map closes first).
    var onOpen: (String) -> Void = { _ in }

    @State private var graph: KnowledgeGraph?
    @State private var error: String?
    @State private var layout = ForceLayout()
    @State private var tick = 0
    @State private var selected: Int?
    @State private var zoom: CGFloat = 1
    @State private var pinch: CGFloat = 1
    @State private var pan = CGSize.zero
    @State private var drag = CGSize.zero

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                ZStack {
                    if let graph {
                        Canvas { canvas, size in
                            _ = tick
                            draw(graph, in: &canvas, size: size)
                        }
                        .contentShape(Rectangle())
                        .gesture(SpatialTapGesture().onEnded { tap in select(at: tap.location, size: geo.size) })
                        .simultaneousGesture(MagnifyGesture().onChanged { pinch = $0.magnification }
                            .onEnded { _ in zoom = min(4, max(0.4, zoom * pinch)); pinch = 1 })
                        .simultaneousGesture(DragGesture().onChanged { drag = $0.translation }
                            .onEnded { _ in pan.width += drag.width; pan.height += drag.height; drag = .zero })
                    } else if let error {
                        ContentUnavailableView("Couldn't load the map", systemImage: "exclamationmark.triangle",
                                               description: Text(error))
                    } else {
                        ProgressView()
                    }
                }
                .overlay(alignment: .bottom) { caption }
            }
            .background { JarvisBackdrop() }
            .navigationTitle("Memory map")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
                ToolbarItem(placement: .topBarLeading) {
                    Button { withAnimation(.easeInOut(duration: 0.3)) { zoom = 1; pan = .zero } } label: {
                        Image(systemName: "scope")
                    }
                    .accessibilityLabel("Recenter")
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        do {
            let loaded = try await JarvisAPIClient.make(from: settings).knowledgeGraph()
            graph = loaded
            layout = ForceLayout(loaded)
            // Settle live: ~2.5 s of the layout finding its shape, then still.
            for _ in 0..<75 {
                guard !Task.isCancelled else { return }
                layout.step(times: 4)
                tick &+= 1
                try? await Task.sleep(nanoseconds: 33_000_000)
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    // MARK: Drawing

    private var scale: CGFloat { zoom * pinch }

    private func screen(_ p: SIMD2<Double>, _ size: CGSize) -> CGPoint {
        let unit = min(size.width, size.height) * 0.45 * scale
        return CGPoint(x: size.width / 2 + p.x * unit + pan.width + drag.width,
                       y: size.height / 2 + p.y * unit + pan.height + drag.height)
    }

    private func radius(_ node: KnowledgeGraph.Node) -> CGFloat {
        switch node.kind {
        case "root": return 14
        case "folder": return 9
        case "topic": return 4 + CGFloat(min(node.weight, 20)) * 0.6
        default: return 3 + CGFloat(min(node.weight, 12)) * 0.5
        }
    }

    private func color(_ node: KnowledgeGraph.Node) -> Color {
        switch node.kind {
        case "root": return JarvisTheme.reactor
        case "folder": return JarvisTheme.blue
        case "topic": return Color(red: 1.0, green: 0.68, blue: 0.22)  // the neural core's gold
        default: return JarvisTheme.cyan
        }
    }

    private func draw(_ graph: KnowledgeGraph, in canvas: inout GraphicsContext, size: CGSize) {
        let points = layout.positions.map { screen($0, size) }
        let neighbours = selected.map { layout.neighbours[$0] } ?? []
        var tree = Path(), links = Path(), focus = Path()
        for (a, b, kind) in layout.edges {
            var segment = Path()
            segment.move(to: points[a])
            segment.addLine(to: points[b])
            if a == selected || b == selected {
                focus.addPath(segment)
            } else if kind == "link" {
                links.addPath(segment)
            } else {
                tree.addPath(segment)
            }
        }
        let dimmed = selected == nil ? 1.0 : 0.4
        canvas.stroke(tree, with: .color(JarvisTheme.reactor.opacity(0.18 * dimmed)), lineWidth: 1)
        canvas.stroke(links, with: .color(JarvisTheme.cyan.opacity(0.55 * dimmed)), lineWidth: 1.4)
        canvas.stroke(focus, with: .color(.white.opacity(0.8)), lineWidth: 1.8)

        for (i, node) in graph.nodes.enumerated() {
            let r = radius(node) * min(1.6, max(0.8, scale))
            let p = points[i]
            let lit = i == selected || neighbours.contains(i)
            let alpha = selected == nil || lit ? 1.0 : 0.35
            let c = color(node)
            canvas.fill(Path(ellipseIn: CGRect(x: p.x - r * 2.4, y: p.y - r * 2.4, width: r * 4.8, height: r * 4.8)),
                        with: .radialGradient(Gradient(colors: [c.opacity(0.35 * alpha), .clear]), center: p, startRadius: 0, endRadius: r * 2.4))
            canvas.fill(Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: r * 2, height: r * 2)), with: .color(c.opacity(alpha)))
            // Names: hubs and topics always; notes once zoomed in, or when they're in focus.
            if node.kind != "note" || scale > 1.6 || lit || graph.nodes.count <= 60 {
                let title = node.kind == "root" ? "JARVIS" : node.title
                canvas.draw(Text(title).font(.caption2.weight(node.kind == "note" ? .regular : .semibold))
                                .foregroundStyle(.white.opacity(0.85 * alpha)),
                            at: CGPoint(x: p.x, y: p.y + r + 8))
            }
        }
    }

    private func select(at location: CGPoint, size: CGSize) {
        let points = layout.positions.map { screen($0, size) }
        let nearest = points.indices.min { hypot(points[$0].x - location.x, points[$0].y - location.y)
            < hypot(points[$1].x - location.x, points[$1].y - location.y) }
        if let nearest, hypot(points[nearest].x - location.x, points[nearest].y - location.y) < 28 {
            selected = nearest == selected ? nil : nearest
        } else {
            selected = nil
        }
    }

    @ViewBuilder private var caption: some View {
        if let graph, let selected, graph.nodes.indices.contains(selected) {
            let node = graph.nodes[selected]
            // A folder (Journal...) lists its notes, newest first; each one opens.
            let notes = node.kind == "folder"
                ? layout.neighbours[selected].map { graph.nodes[$0] }.filter { $0.kind == "note" }.sorted { $0.title > $1.title }
                : []
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(node.kind == "root" ? "JARVIS's memory" : node.title).font(.headline)
                        Text(describe(node, links: layout.neighbours[selected].count))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    if node.kind == "note" {
                        Button("Open") { dismiss(); onOpen(node.id) }
                            .buttonStyle(.borderedProminent).tint(JarvisTheme.reactor)
                    }
                }
                if !notes.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(notes, id: \.id) { note in
                                Button(note.title) { dismiss(); onOpen(note.id) }
                                    .buttonStyle(.bordered).tint(JarvisTheme.cyan)
                            }
                        }
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: 560)
            .jarvisSurface(in: .rect(cornerRadius: 20))
            .padding(20)
        } else if let graph {
            let notes = graph.nodes.filter { $0.kind == "note" }.count
            let links = graph.edges.filter { $0.kind == "link" }.count
            Text("\(notes) notes · \(links) links · tap a dot")
                .font(.caption).foregroundStyle(.secondary)
                .padding(.bottom, 20)
        }
    }

    private func describe(_ node: KnowledgeGraph.Node, links: Int) -> String {
        switch node.kind {
        case "topic": return "\(node.weight) fact\(node.weight == 1 ? "" : "s") in the profile"
        case "folder": return "Folder · \(links) connections"
        case "root": return "Everything JARVIS may read"
        default: return "\(links) connection\(links == 1 ? "" : "s")"
        }
    }
}

/// A small Fruchterman–Reingold layout: everything repels, edges pull, a
/// gentle gravity keeps it centred, and the step shrinks as it cools. Plenty
/// for a few hundred notes.
struct ForceLayout {
    private(set) var positions: [SIMD2<Double>] = []
    private(set) var edges: [(Int, Int, String)] = []
    private(set) var neighbours: [[Int]] = []
    private var heat = 0.12

    init() {}

    init(_ graph: KnowledgeGraph) {
        var index: [String: Int] = [:]
        for (i, node) in graph.nodes.enumerated() { index[node.id] = i }
        edges = graph.edges.compactMap { e in
            guard let a = index[e.from], let b = index[e.to], a != b else { return nil }
            return (a, b, e.kind)
        }
        neighbours = Array(repeating: [], count: graph.nodes.count)
        for (a, b, _) in edges {
            neighbours[a].append(b)
            neighbours[b].append(a)
        }
        // Start in a tight spiral round the middle, so it visibly blooms outward.
        let golden = Double.pi * (3 - 5.0.squareRoot())
        positions = graph.nodes.indices.map { i in
            let r = 0.03 * Double(i).squareRoot()
            return SIMD2(cos(Double(i) * golden) * r, sin(Double(i) * golden) * r)
        }
    }

    mutating func step(times: Int) {
        let n = positions.count
        guard n > 1 else { return }
        let k = 1.6 / Double(n).squareRoot()   // ideal spacing in the unit layout
        for _ in 0..<times {
            var force = [SIMD2<Double>](repeating: .zero, count: n)
            for i in 0..<n {
                for j in (i + 1)..<n {
                    var d = positions[i] - positions[j]
                    var dist = (d.x * d.x + d.y * d.y).squareRoot()
                    if dist < 1e-4 { d = SIMD2(0.001 * Double(i % 7 + 1), 0.001); dist = 0.001 }
                    let push = d / dist * (k * k / dist)
                    force[i] += push
                    force[j] -= push
                }
            }
            for (a, b, kind) in edges {
                let d = positions[a] - positions[b]
                let dist = max(1e-4, (d.x * d.x + d.y * d.y).squareRoot())
                let pull = d / dist * (dist * dist / k) * (kind == "link" ? 1.2 : 1)
                force[a] -= pull
                force[b] += pull
            }
            for i in 0..<n {
                force[i] -= positions[i] * 0.6   // gravity
                let f = force[i]
                let len = max(1e-6, (f.x * f.x + f.y * f.y).squareRoot())
                positions[i] += f / len * min(len, heat)
            }
            heat = max(0.004, heat * 0.975)
        }
        // Fit the unit square, so the whole map starts on screen.
        let extent = positions.reduce(0.0) { max($0, abs($1.x), abs($1.y)) }
        if extent > 1 { positions = positions.map { $0 / extent } }
    }
}
