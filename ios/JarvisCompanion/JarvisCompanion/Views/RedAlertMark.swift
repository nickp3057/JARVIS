import SwiftUI

/// Barn Door Protocol's JARVIS, drawn like the TV's red-alert screen
/// (tv/launcher/src/com/tvhome/RedAlertView.java) so the iPad's orb and the
/// TV match: heavy armour turning one way, a racing targeting dial, a ring
/// of teeth closing the other, two lock-on arcs, crosshair notches and a
/// round lens that blinks once every 30 s. Nothing pulses or flashes.
/// `JarvisCoreMark` shows it whenever the palette is `barnDoor`.
struct RedAlertMark: View {
    var size: CGFloat
    var moving = true
    /// The two reds the TV uses; the camera's green-watch orb passes greens.
    var base = Color(red: 1.0, green: 0.18, blue: 0.16)   // TV theme.reactor
    var hot = Color(red: 1.0, green: 72 / 255, blue: 56 / 255)

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !moving)) { context in
            Canvas { ctx, canvas in
                draw(&ctx, canvas, t: moving ? context.date.timeIntervalSinceReferenceDate : 0)
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    private func draw(_ ctx: inout GraphicsContext, _ canvas: CGSize, t: Double) {
        let c = CGPoint(x: canvas.width / 2, y: canvas.height / 2)
        let r = min(canvas.width, canvas.height) * 0.42
        let red = base

        // A steady red glow behind it.
        ctx.fill(Path(ellipseIn: CGRect(x: 0, y: 0, width: canvas.width, height: canvas.height)),
                 with: .radialGradient(Gradient(colors: [red.opacity(0.47), red.opacity(0)]),
                                       center: c, startRadius: 0, endRadius: canvas.width / 2))

        func circle(_ radius: CGFloat) -> Path {
            Path(ellipseIn: CGRect(x: c.x - radius, y: c.y - radius, width: radius * 2, height: radius * 2))
        }
        func spun(_ degrees: Double, _ body: (inout GraphicsContext) -> Void) {
            var g = ctx
            g.translateBy(x: c.x, y: c.y)
            g.rotate(by: .degrees(degrees))
            g.translateBy(x: -c.x, y: -c.y)
            body(&g)
        }
        func turn(_ period: Double) -> Double { (t / period).truncatingRemainder(dividingBy: 1) * 360 }

        // Armour: twelve heavy segments, turning slowly (30 s).
        let seg = 2 * .pi * r / 12
        spun(turn(30)) { g in
            g.stroke(circle(r), with: .color(red.opacity(0.82)),
                     style: StrokeStyle(lineWidth: r * 0.07, dash: [seg * 0.62, seg * 0.38]))
        }

        // Targeting dial: ninety ticks racing the other way (6 s).
        let rd = r * 0.88, gap = 2 * .pi * rd / 90, tick = max(1, size / 200)
        spun(-turn(6)) { g in
            g.stroke(circle(rd), with: .color(hot.opacity(0.59)),
                     style: StrokeStyle(lineWidth: r * 0.05, dash: [tick, gap - tick]))
        }

        // Teeth: 24 fangs pointing in, closing slowly (45 s).
        spun(-turn(45)) { g in
            var teeth = Path()
            for i in 0..<24 {
                let angle = Double(i) / 24 * 2 * .pi
                let base = r * 0.78, tip = r * (i % 2 == 0 ? 0.62 : 0.68), half = r * 0.045
                func at(_ x: CGFloat, _ y: CGFloat) -> CGPoint {  // (x across, y outward from centre) rotated
                    CGPoint(x: c.x + x * cos(angle) + y * sin(angle), y: c.y + x * sin(angle) - y * cos(angle))
                }
                teeth.move(to: at(-half, base))
                teeth.addLine(to: at(half, base))
                teeth.addLine(to: at(0, tip))
                teeth.closeSubpath()
            }
            g.fill(teeth, with: .color(red.opacity(0.9)))
        }

        // Two lock-on arcs sweeping opposite ways.
        func arc(_ radius: CGFloat, from degrees: Double, sweep: Double) -> Path {
            var p = Path()
            p.addArc(center: c, radius: radius, startAngle: .degrees(degrees),
                     endAngle: .degrees(degrees + sweep * 360), clockwise: false)
            return p
        }
        ctx.stroke(arc(r * 0.56, from: turn(2.4), sweep: 0.3), with: .color(hot),
                   style: StrokeStyle(lineWidth: r * 0.025, lineCap: .round))
        ctx.stroke(arc(r * 0.50, from: -turn(1.7), sweep: 0.2), with: .color(red.opacity(0.78)),
                   style: StrokeStyle(lineWidth: r * 0.025, lineCap: .round))

        // Crosshair notches at the four points.
        var notches = Path()
        for (dx, dy) in [(0.0, -1.0), (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)] {
            notches.move(to: CGPoint(x: c.x + dx * r * 1.07, y: c.y + dy * r * 1.07))
            notches.addLine(to: CGPoint(x: c.x + dx * r * 0.95, y: c.y + dy * r * 0.95))
        }
        ctx.stroke(notches, with: .color(hot), lineWidth: r * 0.02)

        // The lens: dark rim, red glow deepening to a white-hot point.
        let lr = r * 0.30
        ctx.fill(circle(lr), with: .color(Color(red: 0.04, green: 0, blue: 0)))
        ctx.fill(circle(lr * 0.86), with: .radialGradient(
            Gradient(stops: [.init(color: Color(red: 1, green: 0.94, blue: 0.91), location: 0),
                             .init(color: hot, location: 0.12),
                             .init(color: red.opacity(0.9), location: 0.5),
                             .init(color: Color(red: 0.1, green: 0, blue: 0), location: 1)]),
            center: c, startRadius: 0, endRadius: lr * 0.86))
        ctx.stroke(circle(lr), with: .color(hot.opacity(0.86)), lineWidth: r * 0.018)
        ctx.stroke(circle(lr * 0.86), with: .color(red.opacity(0.55)), lineWidth: 1)

        // The blink: lids close from top and bottom and open again, 0.45 s, every 30 s.
        let phase = t.truncatingRemainder(dividingBy: 30)
        if phase > 29.55 {
            let shut = sin(.pi * (phase - 29.55) / 0.45)
            var g = ctx
            g.clip(to: circle(lr * 1.02))
            let lid = lr * 1.1 * shut
            let lids = Path(CGRect(x: c.x - lr * 1.1, y: c.y - lr * 1.1, width: lr * 2.2, height: lid))
                .union(Path(CGRect(x: c.x - lr * 1.1, y: c.y + lr * 1.1 - lid, width: lr * 2.2, height: lid)))
            g.fill(lids, with: .color(Color(red: 0.027, green: 0, blue: 0)))
        }
    }
}
