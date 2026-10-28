import SwiftUI

/// The Home, Hub and Media tabs: glanceable cards over one shared
/// HubViewModel. Each card loads independently, so one dead service never
/// blanks the rest.
struct HubView: View {
    @EnvironmentObject private var settings: AppSettings
    @ObservedObject var model: HubViewModel
    var onAsk: ((String) -> Void)? = nil
    /// Home's chips jump to another tab (Hub for alerts; Media's Services or
    /// drive card); RootView switches tabs.
    var onNavigate: ((HubFocus) -> Void)? = nil
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var isVisible = false
    @State private var showingSettings = false
    @State private var showingMediaRequest = false
    @State private var diagnosisTarget: DiagnosisTarget?
    /// The Media card just jumped to, outlined briefly so the eye lands on it.
    @State private var highlightedCard: String?
    @State private var todoSheet: TodoSheet.Mode?

    struct DiagnosisTarget: Identifiable {
        let title: String
        let kind: String
        var id: String { kind + title }
    }
    @State private var holdsPolling = false
    @Environment(\.openURL) private var openURL
    private let focus: HubFocus

    /// Home is JARVIS himself: the reactor, a quick look, the next
    /// assessment and the to-do list. Hub is the house and the day in full
    /// (alerts, repairs, Now, maintenance, switches). Media carries the
    /// machinery: requests, the drive, transfers, queues, links, services.
    enum HubFocus { case home, hub, media }

    init(model: HubViewModel, onAsk: ((String) -> Void)? = nil,
         onNavigate: ((HubFocus) -> Void)? = nil, focus: HubFocus = .home) {
        self.model = model
        self.onAsk = onAsk
        self.onNavigate = onNavigate
        self.focus = focus
    }

    private var columns: [GridItem] {
        Array(repeating: GridItem(.flexible(), spacing: 10), count: typeSize.isAccessibilitySize ? 1 : 2)
    }

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 16) {
                        switch focus {
                        case .home:
                            hero
                            homeTiles
                            if let portfolio = model.portfolio.value, portfolio.configured {
                                portfolioTile(portfolio)
                            }
                        case .hub:
                            if !attentionAlerts.isEmpty { needsAttentionCard }
                            if !visibleRepairs.isEmpty { repairsCard }
                            nowCard
                            if model.maintenance.value != nil { maintenanceCard }
                            quickActionsCard
                        case .media:
                            requestMediaButton
                            storageCard.highlight(highlightedCard == "storage").id("storage")
                            torrentsCard
                            mediaCard
                            linksCard
                            servicesCard.highlight(highlightedCard == "services").id("services")
                        }
                    }
                    .frame(maxWidth: 760)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 28)
                    .frame(maxWidth: .infinity)
                }
                .onAppear { jump(proxy) }
                .onChange(of: model.mediaScrollTarget) { jump(proxy) }
            }
            .refreshable { await model.refreshAll() }
            .background { JarvisBackdrop() }
            .navigationTitle(focus == .media ? "Media" : focus == .hub ? "Hub" : "Home")
            .toolbar(focus == .home ? .hidden : .automatic, for: .navigationBar)
            .navigationDestination(for: MaintenanceRoute.self) { route in
                switch route {
                case .all: MaintenanceListView(model: model)
                case .task(let id): MaintenanceDetailView(model: model, taskID: id)
                }
            }
            .sheet(isPresented: $showingSettings) { SettingsView() }
            .sheet(item: $todoSheet) { TodoSheet(model: model, startAdding: $0 == .add) }
            .sheet(item: $diagnosisTarget) { target in
                MediaDiagnosisView(title: target.title, kind: target.kind,
                                   makeClient: { try JarvisAPIClient.make(from: settings) })
            }
            .sheet(isPresented: $showingMediaRequest) {
                MediaRequestView(makeClient: { try JarvisAPIClient.make(from: settings) })
            }
            .overlay {
                if !settings.isConfigured {
                    ContentUnavailableView {
                        Label("Your command center awaits", systemImage: "network")
                    } description: {
                        Text("Connect to JARVIS to see your day, home, and media in one place.")
                    } actions: {
                        Button("Connect JARVIS") { showingSettings = true }
                            .buttonStyle(.borderedProminent)
                    }
                    .padding(24)
                    .background(.ultraThinMaterial, in: .rect(cornerRadius: 24))
                    .padding(24)
                }
            }
            .alert(
                "Action failed",
                isPresented: Binding(
                    get: { model.actionError != nil },
                    set: { if !$0 { model.actionError = nil } }
                )
            ) {
                Button("OK") { model.actionError = nil }
            } message: {
                Text(model.actionError ?? "")
            }
        }
        .task {
            isVisible = true
            guard !Task.isCancelled, isVisible, !holdsPolling else { return }
            holdsPolling = true
            model.retainPolling(start: scenePhase == .active)
        }
        .onChange(of: settings.isConfigured) { _, configured in
            if configured, isVisible { model.startPolling() }
            else { model.stopPolling() }
        }
        .onDisappear {
            isVisible = false
            if holdsPolling {
                holdsPolling = false
                model.releasePolling()
            }
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                if isVisible { model.startPolling() }
            case .inactive, .background: model.stopPolling()
            @unknown default: break
            }
        }
    }

    /// The Media tab scrolls to the card a Home chip asked for, then
    /// outlines it for a moment.
    private func jump(_ proxy: ScrollViewProxy) {
        guard focus == .media, let target = model.mediaScrollTarget else { return }
        model.mediaScrollTarget = nil
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(150))
            withAnimation(.easeInOut(duration: 0.45)) { proxy.scrollTo(target, anchor: .top) }
            withAnimation(.easeOut(duration: 0.25)) { highlightedCard = target }
            try? await Task.sleep(for: .seconds(1.4))
            withAnimation(.easeOut(duration: 0.6)) { highlightedCard = nil }
        }
    }

    /// The reactor is the centrepiece; tapping it starts a briefing.
    private var hero: some View {
        VStack(spacing: 16) {
            Button { onAsk?("Give me a briefing on my day and anything that needs my attention.") } label: {
                JarvisCoreMark(size: typeSize.isAccessibilitySize ? 150 : 220)
            }
            .buttonStyle(CockpitButtonStyle())
            .disabled(onAsk == nil)
            .accessibilityLabel("Brief me")
            .accessibilityHint("Starts a briefing with JARVIS")
            .padding(.top, 6)

            VStack(spacing: 6) {
                Text(Self.greeting(at: .now))
                    .font(.largeTitle.weight(.semibold))
                    .multilineTextAlignment(.center)
                Text(Date.now, format: .dateTime.weekday(.wide).month(.wide).day())
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(JarvisTheme.cyan)
            }

            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { systemsChip; storageChip }
                VStack(spacing: 8) { systemsChip; storageChip }
            }
            if attentionCount > 0 { attentionChip }
            if onAsk != nil {
                Text("Tap the reactor for a briefing")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, 8)
    }

    static func greeting(at date: Date, calendar: Calendar = .current) -> String {
        switch calendar.component(.hour, from: date) {
        case 5..<12: return "Good morning, sir."
        case 12..<17: return "Good afternoon, sir."
        case 17..<22: return "Good evening, sir."
        default: return "Burning the midnight oil, sir?"
        }
    }

    private var systemsChip: some View {
        let services = model.services.value ?? []
        let down = services.filter { !$0.up }.count
        let text: String
        let tint: Color
        if services.isEmpty {
            (text, tint) = ("Checking systems…", Color.secondary)
        } else if down == 0 {
            (text, tint) = ("All \(services.count) systems nominal", JarvisTheme.success)
        } else {
            (text, tint) = ("\(down) of \(services.count) systems down", JarvisTheme.critical)
        }
        return heroChip(text, symbol: down == 0 ? "checkmark.shield" : "exclamationmark.shield", tint: tint) {
            openMedia("services")
        }
    }

    @ViewBuilder
    private var storageChip: some View {
        if let storage = model.storage.value, storage.error.isEmpty {
            heroChip("\(Self.bytes(storage.freeBytes)) free", symbol: "externaldrive",
                     tint: storage.usedFraction > 0.9 ? JarvisTheme.amber : JarvisTheme.cyan) {
                openMedia("storage")
            }
        }
    }

    private func heroChip(_ text: String, symbol: String, tint: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(text, systemImage: symbol)
                .font(.footnote.weight(.semibold).monospacedDigit())
                .foregroundStyle(tint)
                .padding(.horizontal, 13)
                .padding(.vertical, 8)
                .background(tint.opacity(0.11), in: .capsule)
                .overlay(Capsule().strokeBorder(tint.opacity(0.28)))
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(onNavigate == nil)
    }

    /// Alerts and fixes awaiting approval, shown on Home as one chip.
    private var attentionCount: Int {
        attentionAlerts.count + visibleRepairs.filter(\.isPending).count
    }

    private var attentionChip: some View {
        let critical = attentionAlerts.contains { $0.severity == "critical" }
        return heroChip(attentionCount == 1 ? "1 thing needs you" : "\(attentionCount) things need you",
                        symbol: "exclamationmark.triangle",
                        tint: critical ? JarvisTheme.critical : JarvisTheme.amber) {
            onNavigate?(.hub)
        }
    }

    private var offlineServiceCount: Int {
        model.services.value?.filter { !$0.up }.count ?? 0
    }

    private func openMedia(_ card: String) {
        model.mediaScrollTarget = card
        onNavigate?(.media)
    }

    // MARK: - Home: next up and to-dos

    /// The soonest assessment day, every assessment on it.
    private var nextAssessments: [AcademicEvent] {
        let events = model.now.value?.academicEvents ?? []
        guard let soonest = events.map(\.daysLeft).min() else { return [] }
        return events.filter { $0.daysLeft == soonest }
    }

    /// Next up and the to-do count, side by side (stacked at accessibility
    /// sizes).
    private var homeTiles: some View {
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(spacing: 12))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
        return layout {
            nextUpTile
            todoTile
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    private var nextUpTile: some View {
        let events = nextAssessments
        return VStack(alignment: .leading, spacing: 6) {
            tileHeading("Next up", symbol: "graduationcap")
            if let first = events.first {
                Text(first.title)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(2)
                Text(Self.sentenceCase(Self.academicCountdown(first.daysLeft))
                     + (events.count > 1 ? " · +\(events.count - 1) more" : ""))
                    .font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(first.daysLeft <= 1 ? JarvisTheme.amber : .secondary)
            } else {
                Text(model.now.value == nil ? "…" : "Nothing due")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .homeTile()
        .accessibilityElement(children: .combine)
    }

    private var todoTile: some View {
        let count = TodoSheet.items(model).count
        return Button { todoSheet = .list } label: {
            VStack(alignment: .leading, spacing: 2) {
                tileHeading("To do", symbol: "checklist")
                Text("\(count)")
                    .font(.system(size: 34, weight: .semibold, design: .rounded).monospacedDigit())
                    .foregroundStyle(count == 0 ? .secondary : .primary)
                    .contentTransition(.numericText())
                Spacer(minLength: 0)
            }
            .homeTile()
        }
        .buttonStyle(CockpitButtonStyle())
        .accessibilityLabel("To do, \(count)")
        .overlay(alignment: .topTrailing) {
            Button { todoSheet = .add } label: {
                Image(systemName: "plus")
                    .font(.footnote.weight(.bold))
                    .foregroundStyle(JarvisTheme.cyan)
                    .frame(width: 30, height: 30)
                    .background(JarvisTheme.cyan.opacity(0.14), in: .circle)
                    .frame(width: 44, height: 44)
            }
            .padding(4)
            .accessibilityLabel("Add a to-do")
        }
        .animation(.snappy, value: count)
    }

    // MARK: - Home: Schwab portfolio (read-only, live)

    private func portfolioTile(_ p: Portfolio) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                tileHeading("Portfolio", symbol: "chart.line.uptrend.xyaxis")
                Spacer()
                if p.streaming == true {
                    Label("Live", systemImage: "dot.radiowaves.left.and.right")
                        .font(.caption2.weight(.semibold)).foregroundStyle(JarvisTheme.success)
                }
            }
            if !p.linked || p.loginDueSoon {
                Text(p.linked ? "Schwab's weekly login runs out soon." : "Schwab needs you to log in.")
                    .font(.subheadline.weight(.semibold))
                Button("Log in to Schwab") { schwabLogin() }
                    .font(.footnote.weight(.semibold)).foregroundStyle(JarvisTheme.cyan)
            }
            if let value = p.value, let today = p.todayChange {
                Text(value, format: .currency(code: "USD"))
                    .font(.system(size: 30, weight: .semibold, design: .rounded).monospacedDigit())
                    .contentTransition(.numericText(value: value))
                    .animation(.snappy, value: value)
                HStack(spacing: 14) {
                    change(today, pct: p.todayPct, label: "today")
                    if let yesterday = p.yesterdayChange { change(yesterday, pct: nil, label: "yesterday") }
                }
                if let cash = p.cash {
                    Text("\(cash.formatted(.currency(code: "USD"))) cash")
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            } else if p.linked {
                Text(p.error?.isEmpty == false ? p.error! : "Fetching your accounts…")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .homeTile()
        .fixedSize(horizontal: false, vertical: true)
    }

    private func change(_ amount: Double, pct: Double?, label: String) -> some View {
        let up = amount >= 0
        let pctText = pct.map { String(format: " (%+.2f%%)", $0) } ?? ""
        return Text("\(up ? "▲" : "▼") \(abs(amount).formatted(.currency(code: "USD")))\(pctText) \(label)")
            .font(.caption.weight(.semibold).monospacedDigit())
            .foregroundStyle(up ? JarvisTheme.success : JarvisTheme.critical)
            .accessibilityLabel("\(up ? "Up" : "Down") \(abs(amount).formatted(.currency(code: "USD"))) \(label)")
    }

    private func schwabLogin() {
        Task { if let url = await model.schwabLoginURL() { openURL(url) } }
    }

    private func tileHeading(_ title: String, symbol: String) -> some View {
        Label(title, systemImage: symbol)
            .font(.caption.weight(.semibold))
            .foregroundStyle(JarvisTheme.cyan)
            .padding(.bottom, 2)
    }

    private static func sentenceCase(_ text: String) -> String {
        text.prefix(1).uppercased() + text.dropFirst().lowercased()
    }

    /// The next few assessments, two weeks out at most; the rest can wait.
    private var upcomingAssessments: [AcademicEvent] {
        Array((model.now.value?.academicEvents ?? []).filter { $0.daysLeft <= 14 }.prefix(7))
    }

    private var nowCard: some View {
        let assessments = upcomingAssessments
        return CockpitPanel("Now", symbol: "clock.badge", trailing: {
            if let snapshot = model.now.value {
                StatusPill(
                    text: model.nowSnapshotIsStale ? "Stale" : "Live",
                    color: model.nowSnapshotIsStale ? JarvisTheme.amber : JarvisTheme.success
                )
                .accessibilityLabel(
                    model.nowSnapshotIsStale
                        ? "Last updated \(snapshot.generatedDate.formatted())"
                        : "Live snapshot"
                )
            }
        }) {
            if let error = model.now.failureText {
                if model.now.value != nil {
                    Label("Showing the last snapshot · \(error)", systemImage: "clock.arrow.circlepath")
                        .font(.caption)
                        .foregroundStyle(JarvisTheme.amber)
                } else {
                    errorRow(error)
                }
            }
            if let snapshot = model.now.value {
                VStack(spacing: 0) {
                    if let event = snapshot.nextEvent {
                        nowRow(symbol: "calendar", tint: JarvisTheme.blue) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(event.title).font(.subheadline.weight(.medium)).lineLimit(1)
                                HStack(spacing: 5) {
                                    if event.allDay {
                                        Text("All day")
                                    } else if let startAt = event.startAt {
                                        Text(Date(timeIntervalSince1970: startAt), format: .dateTime.weekday(.abbreviated).hour().minute())
                                    }
                                    if !event.location.isEmpty { Text("· \(event.location)").lineLimit(1) }
                                }
                                .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        divider
                    } else if !snapshot.calendarError.isEmpty {
                        Label(snapshot.calendarError, systemImage: "calendar.badge.exclamationmark")
                            .font(.caption).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.vertical, 8)
                        if !assessments.isEmpty || !snapshot.timers.isEmpty || !snapshot.reminders.isEmpty { divider }
                    }

                    if !assessments.isEmpty {
                        feedHeading("UPCOMING ASSESSMENTS",
                                    detail: settings.studyAlertsEnabled ? "COUNTDOWN" : "ALERTS OFF")
                        ForEach(Array(assessments.enumerated()), id: \.element.id) { index, event in
                            nowRow(symbol: "graduationcap.fill", tint: JarvisTheme.blue) {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(event.title).font(.subheadline.weight(.medium)).lineLimit(2)
                                    Text(Self.academicCountdown(event.daysLeft))
                                        .font(.caption.monospacedDigit().weight(.semibold))
                                        .foregroundStyle(event.daysLeft <= 1 ? JarvisTheme.amber : .secondary)
                                }
                            }
                            if index < assessments.count - 1 { divider }
                        }
                        if !snapshot.timers.isEmpty || !snapshot.reminders.isEmpty { divider }
                    }

                    ForEach(Array(snapshot.timers.enumerated()), id: \.element.id) { index, timer in
                        nowRow(
                            symbol: timer.state == "alarming" ? "alarm.waves.left.and.right.fill" : "timer",
                            tint: timer.state == "alarming" ? JarvisTheme.critical : JarvisTheme.cyan
                        ) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(timer.label).font(.subheadline.weight(.medium)).lineLimit(1)
                                TimelineView(.periodic(from: .now, by: 1)) { context in
                                    Text(timer.state == "alarming" ? "TIME’S UP" : Self.duration(
                                        timer.state == "running" && !model.nowSnapshotIsStale
                                            ? max(0, Int(timer.expiresAt - context.date.timeIntervalSince1970))
                                            : timer.remainingSeconds, state: timer.state))
                                    .font(.caption.monospacedDigit().weight(.medium))
                                    .foregroundStyle(timer.state == "alarming" ? JarvisTheme.critical : .secondary)
                                }
                            }
                        }
                        if index < snapshot.timers.count - 1 || !snapshot.reminders.isEmpty { divider }
                    }

                    ForEach(Array(snapshot.reminders.enumerated()), id: \.element.id) { index, reminder in
                        nowRow(symbol: reminder.isDue ? "exclamationmark.circle.fill" : "checklist",
                               tint: reminder.isDue ? JarvisTheme.amber : JarvisTheme.success) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(reminder.text).font(.subheadline.weight(.medium)).lineLimit(2)
                                if let dueAt = reminder.dueAt {
                                    Text(Date(timeIntervalSince1970: dueAt), format: .dateTime.weekday(.abbreviated).hour().minute())
                                        .font(.caption).foregroundStyle(reminder.isDue ? JarvisTheme.amber : .secondary)
                                } else {
                                    Text("Open reminder").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        if index < snapshot.reminders.count - 1 { divider }
                    }

                    if snapshot.nextEvent == nil && assessments.isEmpty
                        && snapshot.timers.isEmpty && snapshot.reminders.isEmpty && snapshot.calendarError.isEmpty {
                        quietState("Nothing needs your attention.", symbol: "checkmark.circle")
                    }
                }
                Text("Updated \(snapshot.generatedDate, style: .relative)")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            } else if model.now.isLoading {
                loadingRow
            }
        }
    }

    private func feedHeading(_ title: String, detail: String? = nil) -> some View {
        HStack {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Spacer()
            if let detail, !typeSize.isAccessibilitySize {
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(JarvisTheme.cyan)
            }
        }
        .padding(.top, 9)
    }

    /// One line: how many are due and what's next. Tap in to manage.
    private var maintenanceCard: some View {
        let tasks = model.maintenance.value ?? []
        let attention = tasks.filter(\.needsAttention).count
        return NavigationLink(value: MaintenanceRoute.all) {
            CockpitPanel("Maintenance", symbol: "hammer", trailing: {
                if attention > 0 {
                    StatusPill(text: "\(attention) due", color: tasks.contains(where: \.isOverdue) ? JarvisTheme.critical : JarvisTheme.amber)
                }
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.tertiary)
            }) {
                if let error = model.maintenance.failureText {
                    errorRow(error)
                } else if let next = tasks.first {
                    HStack {
                        Text("Next: \(next.name)").lineLimit(1)
                        Spacer()
                        Text(next.countdown)
                            .font(.footnote.monospacedDigit().weight(.semibold))
                            .foregroundStyle(next.tint)
                    }
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                } else {
                    Text("Nothing tracked yet.").font(.footnote).foregroundStyle(.secondary)
                }
            }
            .foregroundStyle(.primary)
        }
        .buttonStyle(CockpitButtonStyle())
    }

    private func nowRow<Content: View>(
        symbol: String,
        tint: Color,
        @ViewBuilder content: () -> Content
    ) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(tint)
                .frame(width: 34, height: 34)
                .background(tint.opacity(0.11), in: .circle)
            content()
            Spacer(minLength: 0)
        }
        .padding(.vertical, 9)
    }

    private var storageCard: some View {
        CockpitPanel("Media drive", symbol: "externaldrive.fill", trailing: {
            if let storage = model.storage.value, storage.error.isEmpty {
                StatusPill(text: "\(Int((1 - storage.usedFraction) * 100))% free", color: JarvisTheme.cyan)
            }
        }) {
            if let error = model.storage.failureText { errorRow(error) }
            if let storage = model.storage.value, storage.error.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(Self.bytes(storage.freeBytes))
                            .font(.title2.monospacedDigit().weight(.semibold))
                        Text("available").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Text(storage.label)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    ProgressView(value: min(max(storage.usedFraction, 0), 1))
                        .tint(storage.usedFraction > 0.9 ? JarvisTheme.amber : JarvisTheme.cyan)
                    HStack {
                        Text("\(Self.bytes(storage.usedBytes)) used")
                        Spacer()
                        Text("\(Self.bytes(storage.totalBytes)) total")
                    }
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            } else if model.storage.isLoading {
                loadingRow
            }
        }
    }

    /// Warnings and criticals from the last day. An older server without
    /// /api/alerts/recent simply never shows this card.
    private var attentionAlerts: [JarvisAlert] {
        Array((model.alerts.value ?? []).filter { $0.severity != "info" }.prefix(5))
    }

    private var needsAttentionCard: some View {
        CockpitPanel("Needs attention", symbol: "exclamationmark.triangle", trailing: {
            StatusPill(text: "\(attentionAlerts.count)", color: JarvisTheme.amber)
        }) {
            VStack(spacing: 0) {
                ForEach(Array(attentionAlerts.enumerated()), id: \.element.id) { index, alert in
                    Button { openIncident(alert) } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Circle()
                                    .fill(alert.severity == "critical" ? JarvisTheme.critical : JarvisTheme.amber)
                                    .frame(width: 8, height: 8)
                                Text(alert.title).font(.subheadline.weight(.medium))
                                Spacer()
                                if model.reportingAlertID == alert.id {
                                    ProgressView().controlSize(.small)
                                } else {
                                    Text(alert.date, style: .relative)
                                        .font(.caption2.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                }
                                if onAsk != nil {
                                    Image(systemName: "chevron.right")
                                        .font(.caption2.weight(.semibold))
                                        .foregroundStyle(.tertiary)
                                }
                            }
                            if !alert.message.isEmpty {
                                Text(alert.message).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                            }
                        }
                        .padding(.vertical, 8)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .disabled(onAsk == nil || model.reportingAlertID != nil)
                    .accessibilityHint(onAsk == nil ? "" : "Opens Chat with a diagnostic report")
                    if index < attentionAlerts.count - 1 { divider }
                }
            }
        }
    }

    /// Pending fixes first, then the last few outcomes.
    private var visibleRepairs: [RepairProposal] {
        let all = model.repairs.value ?? []
        return Array((all.filter(\.isPending) + all.filter { !$0.isPending && $0.status != "rejected" }).prefix(4))
    }

    private var repairsCard: some View {
        let pending = visibleRepairs.filter(\.isPending).count
        return CockpitPanel("Repairs", symbol: "wrench.and.screwdriver", trailing: {
            if pending > 0 { StatusPill(text: "\(pending) to approve", color: JarvisTheme.amber) }
        }) {
            VStack(spacing: 0) {
                ForEach(Array(visibleRepairs.enumerated()), id: \.element.id) { index, repair in
                    repairRow(repair)
                    if index < visibleRepairs.count - 1 { divider }
                }
            }
        }
    }

    private func repairRow(_ repair: RepairProposal) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(repair.label).font(.subheadline.weight(.medium))
            if repair.isPending {
                if !repair.rationale.isEmpty {
                    Text(repair.rationale).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                }
                HStack {
                    Button("Dismiss", role: .cancel) { model.resolveRepair(repair, approve: false) }
                        .buttonStyle(.bordered)
                    Spacer()
                    if model.busyRepairs.contains(repair.id) { ProgressView().controlSize(.small) }
                    Button("Approve") { model.resolveRepair(repair, approve: true) }
                        .buttonStyle(.borderedProminent)
                        .accessibilityHint("Runs this fix on your server now")
                }
                .controlSize(.small)
                .disabled(model.busyRepairs.contains(repair.id))
            } else {
                Label(repair.status == "running" ? "Running…" : (repair.result.isEmpty ? repair.status.capitalized : repair.result),
                      systemImage: repair.status == "applied" ? "checkmark.circle" : repair.status == "running" ? "clock" : "exclamationmark.circle")
                    .font(.caption)
                    .foregroundStyle(repair.status == "applied" ? JarvisTheme.success : repair.status == "running" ? Color.secondary : JarvisTheme.amber)
                    .lineLimit(3)
            }
        }
        .padding(.vertical, 8)
    }

    /// Tap-to-chat: JARVIS gathers logs and recent changes for the alert,
    /// and the report's prompt lands in Chat's composer for review.
    private func openIncident(_ alert: JarvisAlert) {
        guard let onAsk else { return }
        Task {
            if let prompt = await model.incidentPrompt(for: alert) { onAsk(prompt) }
        }
    }

    private var servicesCard: some View {
        CockpitPanel("Services", symbol: "dot.radiowaves.left.and.right", trailing: {
            if let services = model.services.value, !services.isEmpty {
                StatusPill(text: offlineServiceCount == 0 ? "Nominal" : "\(offlineServiceCount) down",
                           color: offlineServiceCount == 0 ? JarvisTheme.success : JarvisTheme.critical)
            }
        }) {
            if let error = model.services.failureText { errorRow(error) }
            if let services = model.services.value {
                VStack(spacing: 0) {
                    ForEach(Array(services.enumerated()), id: \.element.id) { index, service in
                        HStack(spacing: 11) {
                            Circle()
                                .fill(service.up ? JarvisTheme.success : JarvisTheme.critical)
                                .frame(width: 8, height: 8)
                                .shadow(color: (service.up ? JarvisTheme.success : JarvisTheme.critical).opacity(0.55), radius: 4)
                            Text(service.name).font(.subheadline.weight(.medium))
                            Spacer()
                            Text(serviceDetail(service))
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 10)
                        .accessibilityLabel("\(service.name), \(service.up ? "up" : "down")")
                        if index < services.count - 1 { divider }
                    }
                }
            } else if model.services.isLoading { loadingRow }
        }
    }

    private func serviceDetail(_ service: ServiceStatus) -> String {
        if let latency = service.latencyMS { return "\(latency) ms" }
        return service.error.contains("not configured") ? "Not set" : "Down"
    }

    private var torrentsCard: some View {
        CockpitPanel("Transfers", symbol: "arrow.down.circle", trailing: {
            if let status = model.torrents.value, status.error.isEmpty {
                Text("↓ \(Self.rate(status.dlSpeed))")
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(JarvisTheme.cyan)
            }
        }) {
            if let error = model.torrents.failureText { errorRow(error) }
            if let status = model.torrents.value {
                if status.torrents.isEmpty && status.error.isEmpty {
                    quietState("No active or paused downloads.", symbol: "checkmark.circle")
                }
                VStack(spacing: 0) {
                    ForEach(Array(status.torrents.enumerated()), id: \.element.id) { index, torrent in
                        torrentRow(torrent)
                        if index < status.torrents.count - 1 { divider }
                    }
                }
            } else if model.torrents.isLoading { loadingRow }
        }
    }

    private func torrentRow(_ torrent: TorrentTransfer) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(torrent.name).font(.subheadline.weight(.medium)).lineLimit(1)
                    Text(torrent.isPaused ? "Paused" : torrent.state.capitalized)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(torrent.isPaused ? JarvisTheme.amber : JarvisTheme.cyan)
                }
                Spacer()
                if let hash = torrent.hash {
                    Button {
                        model.torrentAction(torrent, action: torrent.isPaused ? "resume" : "pause")
                    } label: {
                        Image(systemName: torrent.isPaused ? "play.fill" : "pause.fill")
                            .font(.caption.weight(.bold))
                            .frame(width: 44, height: 44)
                            .background(JarvisTheme.cyan.opacity(0.11), in: .circle)
                    }
                    .disabled(model.busyTorrents.contains(hash) || model.torrents.failureText != nil)
                    .accessibilityLabel(torrent.isPaused ? "Resume \(torrent.name)" : "Pause \(torrent.name)")
                }
            }
            ProgressView(value: min(max(torrent.progress, 0), 1))
                .tint(torrent.isPaused ? JarvisTheme.amber : JarvisTheme.cyan)
            HStack {
                Text("\(Int(min(max(torrent.progress, 0), 1) * 100))%")
                // qBittorrent reports 0 until the torrent's metadata arrives.
                if torrent.size > 0 { Text("of \(Self.bytes(Int64(torrent.size)))") }
                Text("· \(torrent.isPaused ? "Waiting" : Self.rate(torrent.dlSpeed))")
                Spacer()
                Text(Self.eta(torrent.eta))
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 11)
    }

    /// Opens the chat-free search -> pick -> confirm request flow.
    private var requestMediaButton: some View {
        Button { showingMediaRequest = true } label: {
            Label("Request a movie or show", systemImage: "plus.magnifyingglass")
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
        }
        .buttonStyle(.borderedProminent)
        .tint(JarvisTheme.blue)
        .disabled(!settings.isConfigured)
    }

    private var mediaCard: some View {
        CockpitPanel("Media", symbol: "play.rectangle.on.rectangle") {
            if let error = model.media.failureText { errorRow(error) }
            if let media = model.media.value {
                mediaService("Radarr", symbol: "film", section: media.radarr)
                divider
                mediaService("Sonarr", symbol: "tv", section: media.sonarr)
            } else if model.media.isLoading { loadingRow }
        }
    }

    @ViewBuilder
    private func mediaService(_ name: String, symbol: String, section: ServarrSection) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(name, systemImage: symbol).font(.subheadline.weight(.semibold))
                Spacer()
                Text("\(section.queue.count) QUEUED")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if !section.error.isEmpty {
                errorRow(section.error)
            } else {
                ForEach(section.queue) { item in
                    VStack(alignment: .leading, spacing: 5) {
                        HStack {
                            Text(item.title).font(.subheadline).lineLimit(1)
                            Spacer(minLength: 6)
                            Button("Why?") {
                                // Sonarr rows read "Series — Episode"; the
                                // doctor traces the series.
                                let base = item.title.components(separatedBy: " — ").first ?? item.title
                                diagnosisTarget = DiagnosisTarget(title: base, kind: name == "Radarr" ? "movie" : "show")
                            }
                            .font(.caption.weight(.semibold))
                            .buttonStyle(.bordered)
                            .controlSize(.mini)
                        }
                        ProgressView(value: min(max(item.progress, 0), 1)).tint(JarvisTheme.blue)
                        HStack {
                            Text(item.status)
                            if item.size > 0 { Text("· \(Self.bytes(Int64(item.size)))") }
                            Spacer()
                            Text(item.timeLeft)
                        }
                        .font(.caption).foregroundStyle(.secondary)
                    }
                }
                DisclosureGroup("Recent additions (\(section.recent.count))") {
                    if section.recent.isEmpty { Text("Nothing recent.").foregroundStyle(.secondary) }
                    ForEach(section.recent) { item in
                        HStack {
                            Text(item.title).lineLimit(1)
                            Spacer()
                            if item.year > 0 { Text(String(item.year)).foregroundStyle(.secondary) }
                        }
                        .padding(.vertical, 3)
                    }
                }
                .font(.footnote)
                .tint(JarvisTheme.cyan)
            }
        }
        .padding(.vertical, 4)
    }

    /// Small tap-to-toggle tiles, three across. Lit means on.
    private var quickActionsCard: some View {
        CockpitPanel("Home", symbol: "house.lodge") {
            if let error = model.entities.failureText { errorRow(error) }
            if let entities = model.entities.value {
                if entities.isEmpty && model.entities.serverError.isEmpty {
                    quietState("No quick actions configured.", symbol: "slider.horizontal.3")
                }
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8),
                                          count: typeSize.isAccessibilitySize ? 2 : 3), spacing: 8) {
                    ForEach(entities) { entityTile($0) }
                }
            } else if model.entities.isLoading { loadingRow }
        }
    }

    private func entityTile(_ entity: HomeEntity) -> some View {
        let pending = model.pendingEntityStates[entity.entityID]
        let on = pending ?? entity.isOn
        let tint = on ? JarvisTheme.amber : Color.secondary
        return Button { model.toggle(entity, to: !on) } label: {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Image(systemName: Self.homeIcon(for: entity.domain))
                        .font(.subheadline)
                        .foregroundStyle(tint)
                    Spacer(minLength: 0)
                    if pending != nil { ProgressView().controlSize(.mini) }
                }
                Text(entity.friendlyName)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.primary)
                    .lineLimit(2, reservesSpace: true)
                    .multilineTextAlignment(.leading)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background((on ? JarvisTheme.amber.opacity(0.14) : JarvisTheme.cyan.opacity(0.05)), in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(on ? JarvisTheme.amber.opacity(0.4) : JarvisTheme.border))
        }
        .buttonStyle(CockpitButtonStyle())
        .disabled(!entity.isToggleable || pending != nil || model.entities.failureText != nil)
        .animation(.easeOut(duration: 0.2), value: on)
        .accessibilityLabel(entity.friendlyName)
        .accessibilityValue(entity.state.capitalized)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private var linksCard: some View {
        CockpitPanel("Launch", symbol: "arrow.up.right.square", trailing: {
            if model.links.value?.contains(where: { !$0.remoteURL.isEmpty }) == true, let home = model.onHomeNetwork {
                StatusPill(text: home ? "Home network" : "Via Tailscale", color: home ? JarvisTheme.success : JarvisTheme.cyan)
            }
        }) {
            if let error = model.links.failureText { errorRow(error) }
            if let links = model.links.value {
                if links.isEmpty { quietState("No service links configured.", symbol: "link") }
                LazyVGrid(columns: columns, spacing: 10) {
                    ForEach(links) { link in
                        if let url = link.destination(onHomeNetwork: model.onHomeNetwork) {
                            Link(destination: url) {
                                HStack(spacing: 9) {
                                    Image(systemName: Self.icon(for: link.iconHint))
                                        .foregroundStyle(JarvisTheme.cyan)
                                    Text(link.name).font(.footnote.weight(.medium)).lineLimit(1)
                                    Spacer(minLength: 0)
                                }
                                .padding(11)
                                .background(JarvisTheme.cyan.opacity(0.07), in: .rect(cornerRadius: 12))
                                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(JarvisTheme.border))
                            }
                        }
                    }
                }
            } else if model.links.isLoading { loadingRow }
        }
    }

    private var divider: some View {
        Divider().overlay(JarvisTheme.border.opacity(0.65))
    }

    private var loadingRow: some View {
        HStack(spacing: 10) {
            ProgressView().tint(JarvisTheme.cyan)
            Text("Updating…").font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 16)
    }

    private func errorRow(_ text: String) -> some View {
        Label(text, systemImage: "exclamationmark.triangle.fill")
            .font(.footnote)
            .foregroundStyle(JarvisTheme.amber)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func quietState(_ text: String, symbol: String) -> some View {
        Label(text, systemImage: symbol)
            .font(.footnote)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 8)
    }

    private static func homeIcon(for domain: String) -> String {
        switch domain {
        case "light": return "lightbulb.fill"
        case "switch": return "switch.2"
        case "fan": return "fan.fill"
        default: return "power"
        }
    }

    private static func icon(for hint: String) -> String {
        switch hint {
        case "home": return "house"
        case "play": return "play.rectangle"
        case "tv": return "tv"
        case "movie": return "film"
        case "download": return "arrow.down.circle"
        default: return "link"
        }
    }

    private static func rate(_ bytesPerSecond: Int) -> String {
        guard bytesPerSecond > 0 else { return "0 KB/s" }
        return "\(ByteCountFormatter.string(fromByteCount: Int64(bytesPerSecond), countStyle: .binary))/s"
    }

    private static func bytes(_ value: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: value, countStyle: .binary)
    }

    private static func academicCountdown(_ days: Int) -> String {
        switch days {
        case ...0: return "TODAY"
        case 1: return "TOMORROW"
        default: return "IN \(days) DAYS"
        }
    }

    private static func eta(_ seconds: Int) -> String {
        guard seconds > 0, seconds < 8_640_000 else { return "" }
        if seconds < 60 { return "<1m left" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(minutes)m left" }
        let hours = minutes / 60
        if hours < 48 { return "\(hours)h \(minutes % 60)m left" }
        return "\(hours / 24)d left"
    }

    private static func duration(_ seconds: Int, state: String) -> String {
        let prefix = state == "paused" ? "Paused · " : ""
        if seconds < 60 { return "\(prefix)\(seconds)s remaining" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(prefix)\(minutes)m remaining" }
        let hours = minutes / 60
        return "\(prefix)\(hours)h \(minutes % 60)m remaining"
    }
}

private extension View {
    /// The half-width Home tiles: a smaller CockpitPanel.
    func homeTile() -> some View {
        padding(14)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .frame(minHeight: 96)
            .background(JarvisTheme.panelGradient, in: .rect(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(JarvisTheme.border))
    }

    /// A brief accent outline around a card Home jumped to.
    func highlight(_ on: Bool) -> some View {
        overlay {
            RoundedRectangle(cornerRadius: 20)
                .strokeBorder(JarvisTheme.cyan, lineWidth: 2)
                .shadow(color: JarvisTheme.cyan.opacity(0.6), radius: 8)
                .opacity(on ? 1 : 0)
                .allowsHitTesting(false)
        }
    }
}
