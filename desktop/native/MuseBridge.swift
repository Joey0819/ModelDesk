import AppKit
import ApplicationServices
import Foundation

// A narrow adapter for the user's signed-in Muse window. No cookies, tokens,
// private endpoints, or other applications are accessed.
let bundleID = "com.meta.endo"
let command = CommandLine.arguments.dropFirst().first ?? "probe"
let input: [String: Any] = command == "probe" ? [:] : ((try? JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile())) as? [String: Any] ?? [:])
func emit(_ object: [String: Any]) -> Never {
    let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10])); exit(0)
}
func fail(_ code: String, uncertain: Bool = false) -> Never { emit(["ok": false, "error": code, "uncertain": uncertain, "sent": uncertain ? NSNull() : false, "request_id": input["request_id"] ?? NSNull()]) }
func attr(_ element: AXUIElement, _ key: String) -> CFTypeRef? {
    var value: CFTypeRef?; guard AXUIElementCopyAttributeValue(element, key as CFString, &value) == .success else { return nil }; return value
}
func string(_ element: AXUIElement, _ key: String) -> String { attr(element, key) as? String ?? "" }
func children(_ element: AXUIElement) -> [AXUIElement] { attr(element, kAXChildrenAttribute) as? [AXUIElement] ?? [] }
func nodes(_ root: AXUIElement) -> [AXUIElement] {
    var result: [AXUIElement] = []; var pending = [root]
    while let item = pending.popLast(), result.count < 6000 { result.append(item); pending.append(contentsOf: children(item).reversed()) }
    return result
}
func label(_ element: AXUIElement) -> String {
    var values: [String] = []
    for key in [kAXTitleAttribute, kAXDescriptionAttribute, kAXHelpAttribute] { let value = string(element, key); if !value.isEmpty && !values.contains(value) { values.append(value) } }
    return values.joined(separator: " ")
}
func rect(_ element: AXUIElement) -> CGRect? {
    guard let p = attr(element, kAXPositionAttribute), let s = attr(element, kAXSizeAttribute), CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero; var size = CGSize.zero
    guard AXValueGetValue(unsafeBitCast(p, to: AXValue.self), .cgPoint, &point), AXValueGetValue(unsafeBitCast(s, to: AXValue.self), .cgSize, &size) else { return nil }
    return CGRect(origin: point, size: size)
}
func settle(_ seconds: Double = 0.2) { RunLoop.current.run(until: Date().addingTimeInterval(seconds)) }
let installed = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID)
let permission = AXIsProcessTrusted()
guard let appURL = installed else { emit(["ok": true, "installed": false, "permission": permission]) }
let version = Bundle(url: appURL)?.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
guard permission else { emit(["ok": false, "installed": true, "permission": false, "version": version, "error": "accessibility_permission_required"]) }
guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first else { emit(["ok": false, "installed": true, "permission": true, "version": version, "error": "muse_not_running"]) }
let application = AXUIElementCreateApplication(app.processIdentifier)
AXUIElementSetMessagingTimeout(application, 2)
func snapshot() -> [AXUIElement] { nodes(application) }
func threadURL(_ tree: [AXUIElement]) -> String {
    for element in tree where string(element, kAXRoleAttribute) == "AXWebArea" {
        let raw = attr(element, kAXURLAttribute)
        let value = (raw as? URL)?.absoluteString ?? (raw as? String ?? "")
        if let url = URL(string: value), url.host == "muse.ai", url.path.hasPrefix("/thread/") { return url.absoluteString }
    }
    return ""
}
func newChatButton(_ tree: [AXUIElement]) -> AXUIElement? {
    tree.first { string($0, kAXRoleAttribute) == kAXButtonRole && ["New side chat", "New chat"].contains(label($0)) }
}
func chatMenuButton(_ tree: [AXUIElement]) -> AXUIElement? {
    tree.first { string($0, kAXRoleAttribute) == kAXPopUpButtonRole && label($0) == "Chats Open chat and side chats" }
}
func backToMainButton(_ tree: [AXUIElement]) -> AXUIElement? {
    tree.first { string($0, kAXRoleAttribute) == kAXButtonRole && label($0) == "Back to main chat" }
}
func emptyNewThread(_ tree: [AXUIElement]) -> Bool {
    threadURL(tree) == "https://muse.ai/thread/new" && chatRoot(tree).map { textIn($0).isEmpty } == true
}
func composer(_ tree: [AXUIElement]) -> AXUIElement? {
    tree.first { string($0, kAXRoleAttribute) == kAXButtonRole && label($0).contains("Attach file") && label($0).contains("Send") }
        ?? tree.first { [kAXTextAreaRole, kAXTextFieldRole].contains(string($0, kAXRoleAttribute)) && (label($0).localizedCaseInsensitiveContains("ask") || label($0).localizedCaseInsensitiveContains("message") || label($0).contains("输入") || label($0).contains("提问")) }
}
func press(_ element: AXUIElement) -> Bool { AXUIElementPerformAction(element, kAXPressAction as CFString) == .success }
func foreground() -> Bool { NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier }
func activate() -> Bool { app.activate(options: []); settle(0.3); return foreground() }
func click(_ point: CGPoint) -> Bool {
    guard foreground(), mainWindowFocused() else { return false }
    CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    settle(); return foreground()
}
func key(_ code: CGKeyCode, flags: CGEventFlags = []) -> Bool {
    guard foreground(), mainWindowFocused() else { return false }
    let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true)
    let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)
    down?.flags = flags; up?.flags = flags; down?.postToPid(app.processIdentifier); up?.postToPid(app.processIdentifier); settle(); return foreground()
}
func panelKey(_ code: CGKeyCode, flags: CGEventFlags = []) -> Bool {
    guard foreground(), snapshot().contains(where: { ["save-panel", "GoToWindow"].contains(string($0, kAXIdentifierAttribute)) }) else { return false }
    let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true)
    let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)
    // The native save panel can be hosted by AppKit's XPC service. Posting to
    // the Muse PID misses that key window; use normal focused-window routing
    // only while the verified Muse save/Go To modal is present and foreground.
    down?.flags = flags; up?.flags = flags; down?.post(tap: .cghidEventTap); up?.post(tap: .cghidEventTap)
    settle(); return foreground()
}
func validWindow(_ window: AXUIElement) -> Bool {
    let identifier = string(window, kAXIdentifierAttribute)
    let role = string(window, kAXRoleAttribute)
    guard role == kAXWindowRole || identifier == "hatch-full" else { return false }
    if !identifier.isEmpty && identifier != "hatch-full" && !identifier.hasPrefix("hatch") { return false }
    return !nodes(window).contains { string($0, kAXRoleAttribute) == kAXSheetRole }
}
func mainWindowFocused() -> Bool {
    if let value = attr(application, kAXFocusedWindowAttribute), CFGetTypeID(value) == AXUIElementGetTypeID() {
        let window = unsafeBitCast(value, to: AXUIElement.self)
        if validWindow(window) { return true }
    }
    if let value = attr(application, kAXMainWindowAttribute), CFGetTypeID(value) == AXUIElementGetTypeID() {
        let window = unsafeBitCast(value, to: AXUIElement.self)
        if validWindow(window) { return true }
    }
    if let windows = attr(application, kAXWindowsAttribute) as? [AXUIElement] {
        return windows.contains { validWindow($0) }
    }
    return false
}

func textIn(_ root: AXUIElement) -> String {
    nodes(root).filter { string($0, kAXRoleAttribute) == kAXStaticTextRole }.map { string($0, kAXValueAttribute) }.filter { !$0.isEmpty }.joined(separator: "\n")
}
func chatRoot(_ tree: [AXUIElement]) -> AXUIElement? { tree.first { label($0) == "Chat messages" } }
func resourcesIn(_ root: AXUIElement) -> [[String: String]] {
    var resources: [[String: String]] = []; var seen = Set<String>()
    // Read actual AXURL values only. Labels, prose, thumbnails without a URL,
    // and guessed Muse endpoints do not establish downloadable media.
    for element in nodes(root) where ["AXLink", "AXImage"].contains(string(element, kAXRoleAttribute)) {
        let raw = attr(element, kAXURLAttribute)
        let value = (raw as? URL)?.absoluteString ?? (raw as? String ?? "")
        guard value.count <= 8192, let url = URL(string: value), url.scheme == "https",
              url.host != nil, url.user == nil, url.password == nil, url.fragment == nil,
              url.port == nil || url.port == 443, !value.contains(where: { $0.isWhitespace }), !seen.contains(value) else { continue }
        let resourceLabel = label(element)
        let decoration = resourceLabel.lowercased()
        let decorationPaths = Set(["avatar", "avatars", "favicon", "icons", "logos"])
        guard !decoration.contains("avatar"), !decoration.contains("profile picture"), !resourceLabel.contains("头像"),
              !url.pathComponents.contains(where: { decorationPaths.contains($0.lowercased()) }) else { continue }
        seen.insert(value)
        resources.append(["url": value, "label": String(resourceLabel.prefix(200))])
        if resources.count >= 12 { break }
    }
    return resources
}
func isUserMessage(_ element: AXUIElement, _ text: String) -> Bool {
    let textPrefixes = ["You:", "User message:", "你：", "用户消息："]
    if textPrefixes.contains(where: { text.hasPrefix($0) || label(element).hasPrefix($0) }) { return true }
    return nodes(element).contains { ["Copy message", "Edit message", "Edit prompt", "复制消息", "编辑消息"].contains(label($0)) }
}
func isBusy(_ tree: [AXUIElement]) -> Bool {
    tree.contains { element in
        guard string(element, kAXRoleAttribute) == kAXButtonRole else { return false }
        let value = label(element)
        return ["Stop", "Stop response", "Stop generating", "停止", "停止生成"].contains(value) || (value.contains("Attach file") && value.contains("Stop"))
    }
}
func hasApproval(_ tree: [AXUIElement]) -> Bool {
    tree.contains { ["Allow once", "Always allow", "Deny", "允许一次", "拒绝"].contains(label($0)) }
}
func validThread(_ value: String) -> Bool {
    guard let url = URL(string: value), url.scheme == "https", url.host == "muse.ai", url.port == nil || url.port == 443, url.query == nil, url.fragment == nil, url.user == nil, url.password == nil else { return false }
    let parts = url.path.split(separator: "/")
    return parts.count == 2 && parts[0] == "thread" && UUID(uuidString: String(parts[1])) != nil
}
// Clipboard access is in memory only and restored after our own writes. Never
// expose existing clipboard or unrelated drafts in JSON, logs, or model input.
final class TemporaryClipboard {
    let board = NSPasteboard.general
    let original: [[NSPasteboard.PasteboardType: Data]]
    var owned = Set<String>()
    init() { original = board.pasteboardItems?.map { item in Dictionary(uniqueKeysWithValues: item.types.compactMap { type in item.data(forType: type).map { (type, $0) } }) } ?? [] }
    func put(_ text: String) { owned.insert(text); board.clearContents(); board.setString(text, forType: .string) }
    func read() -> String { board.string(forType: .string) ?? "" }
    func restore() {
        guard owned.contains(read()) else { return }
        board.clearContents()
        let items = original.map { values -> NSPasteboardItem in let item = NSPasteboardItem(); for (type, data) in values { item.setData(data, forType: type) }; return item }
        if !items.isEmpty { board.writeObjects(items) }
    }
}

func stagingDirectory() -> URL? {
    guard let value = input["download_directory"] as? String, value.hasPrefix("/"),
          let request = input["request_id"] as? String else { return nil }
    let directory = URL(fileURLWithPath: value, isDirectory: true).standardizedFileURL
    guard directory.lastPathComponent == request.lowercased(), directory.deletingLastPathComponent().lastPathComponent == "muse-native-staging",
          directory.resolvingSymlinksInPath().path == directory.path,
          let details = try? FileManager.default.attributesOfItem(atPath: directory.path),
          details[.type] as? FileAttributeType == .typeDirectory,
          (details[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
          ((details[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o077 == 0 else { return nil }
    return directory
}

var downloadDiagnostic = "native_attachment_download_unconfirmed"
func downloadFailed(_ code: String) -> [String: String]? { downloadDiagnostic = code; return nil }
func downloadAttachment(_ button: AXUIElement, filename expectedFilename: String, directory: URL, expected: String) -> [String: String]? {
    guard threadURL(snapshot()) == expected else { return downloadFailed("native_download_thread_changed") }
    guard activate() else { return downloadFailed("native_download_activation_unconfirmed") }
    guard mainWindowFocused() else { return downloadFailed("native_download_focus_unconfirmed") }
    let receiptURL = directory.appendingPathComponent(".download-attempt.json")
    func matchingAttempt(_ saveName: String) -> Bool {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: receiptURL.path),
              attributes[.type] as? FileAttributeType == .typeRegular,
              ((attributes[.size] as? NSNumber)?.intValue ?? 5000) <= 4096,
              let raw = try? Data(contentsOf: receiptURL),
              let record = (try? JSONSerialization.jsonObject(with: raw)) as? [String: Any],
              record["request_id"] as? String == input["request_id"] as? String,
              record["thread_url"] as? String == expected, record["filename"] as? String == expectedFilename,
              let time = record["created_at"] as? Double else { return false }
        let age = Date().timeIntervalSince1970 - time
        return age >= -60 && age <= 1800 && (saveName == expectedFilename || saveName == record["staging_filename"] as? String)
    }
    var panel = snapshot().first { string($0, kAXIdentifierAttribute) == "save-panel" }
    if let existing = panel {
        // Resume only a panel for this exact, observed current-task artifact.
        // A changed filename or any unrelated user save draft is untouched.
        guard let field = nodes(existing).first(where: { string($0, kAXIdentifierAttribute) == "saveAsNameTextField" }),
              !expectedFilename.isEmpty, matchingAttempt(string(field, kAXValueAttribute)) else { return downloadFailed("native_download_existing_save_panel") }
    } else {
        let receipt: [String: Any] = ["request_id": input["request_id"] ?? "", "thread_url": expected,
                                     "filename": expectedFilename, "created_at": Date().timeIntervalSince1970]
        guard let raw = try? JSONSerialization.data(withJSONObject: receipt),
              (try? raw.write(to: receiptURL, options: [.atomic])) != nil else { return downloadFailed("native_download_receipt_unavailable") }
        // Electron can report an AXPress error after opening the native panel.
        // Observe that panel instead of treating an action return as delivery.
        _ = press(button)
        for _ in 0..<60 {
            panel = snapshot().first { string($0, kAXIdentifierAttribute) == "save-panel" }
            if panel != nil { break }; settle(0.1)
        }
    }
    if panel == nil, threadURL(snapshot()) == expected, let buttonRect = rect(button) {
        // Some Electron attachment buttons expose AXPress without performing
        // the download. One click on this exact observed button is permitted
        // only after confirming no save panel appeared from AXPress.
        _ = click(CGPoint(x: buttonRect.midX, y: buttonRect.midY))
        for _ in 0..<20 {
            panel = snapshot().first { string($0, kAXIdentifierAttribute) == "save-panel" }
            if panel != nil { break }; settle(0.1)
        }
    }
    guard let save = panel else { return downloadFailed("native_download_save_panel_unavailable") }
    settle(0.3)
    func cancelSave() {
        if snapshot().contains(where: { string($0, kAXIdentifierAttribute) == "GoToWindow" }) { _ = panelKey(53) }
        if let active = snapshot().first(where: { string($0, kAXIdentifierAttribute) == "save-panel" }),
           let cancel = nodes(active).first(where: { string($0, kAXIdentifierAttribute) == "CancelButton" }) { _ = press(cancel) }
    }
    guard let originalName = nodes(save).first(where: { string($0, kAXIdentifierAttribute) == "saveAsNameTextField" }) else { cancelSave(); return downloadFailed("native_download_name_unavailable") }
    let suffix = URL(fileURLWithPath: string(originalName, kAXValueAttribute)).pathExtension.lowercased()
    guard ["mp4", "webm", "png", "jpg", "jpeg", "webp"].contains(suffix),
          panelKey(5, flags: [.maskCommand, .maskShift]) else { cancelSave(); return downloadFailed("native_download_type_unsupported") }
    var pathField: AXUIElement?
    for _ in 0..<12 {
        pathField = snapshot().first { string($0, kAXIdentifierAttribute) == "PathTextField" }
        if pathField != nil { break }; settle(0.1)
    }
    guard let path = pathField else { cancelSave(); return downloadFailed("native_download_goto_panel_unavailable") }
    _ = AXUIElementSetAttributeValue(path, kAXValueAttribute as CFString, directory.path as CFString)
    guard string(path, kAXValueAttribute) == directory.path else { cancelSave(); return downloadFailed("native_download_path_value_unconfirmed") }
    guard panelKey(36) else { cancelSave(); return downloadFailed("native_download_path_commit_unconfirmed") }
    settle(0.3)
    let current = snapshot()
    guard let activeSave = current.first(where: { string($0, kAXIdentifierAttribute) == "save-panel" }),
          !current.contains(where: { string($0, kAXIdentifierAttribute) == "PathTextField" }),
          let name = nodes(activeSave).first(where: { string($0, kAXIdentifierAttribute) == "saveAsNameTextField" }),
          let location = nodes(activeSave).first(where: { string($0, kAXIdentifierAttribute) == "where popup" }),
          string(location, kAXValueAttribute) == directory.lastPathComponent else { cancelSave(); return downloadFailed("native_download_destination_unconfirmed") }
    let filename = UUID().uuidString.lowercased() + "." + suffix
    let destination = directory.appendingPathComponent(filename)
    guard !FileManager.default.fileExists(atPath: destination.path) else { cancelSave(); return downloadFailed("native_download_destination_exists") }
    let savingReceipt: [String: Any] = ["request_id": input["request_id"] ?? "", "thread_url": expected,
                                      "filename": expectedFilename, "staging_filename": filename, "created_at": Date().timeIntervalSince1970]
    guard let receiptBytes = try? JSONSerialization.data(withJSONObject: savingReceipt),
          (try? receiptBytes.write(to: receiptURL, options: [.atomic])) != nil else { cancelSave(); return downloadFailed("native_download_receipt_unavailable") }
    // AXSetValue updates the visible filename without notifying AppKit's save
    // validation, leaving Save disabled. Edit the verified field normally.
    _ = AXUIElementSetAttributeValue(name, kAXFocusedAttribute as CFString, kCFBooleanTrue)
    let clipboard = TemporaryClipboard()
    clipboard.put(filename)
    let entered = panelKey(0, flags: .maskCommand) && panelKey(9, flags: .maskCommand)
    clipboard.restore()
    guard entered, string(name, kAXValueAttribute) == filename,
          let saveButton = nodes(activeSave).first(where: { string($0, kAXIdentifierAttribute) == "OKButton" }),
          attr(saveButton, kAXEnabledAttribute) as? Bool == true else { cancelSave(); return downloadFailed("native_download_save_unconfirmed") }
    _ = press(saveButton)
    settle(0.3)
    if let stillOpen = snapshot().first(where: { string($0, kAXIdentifierAttribute) == "save-panel" }),
       let stillName = nodes(stillOpen).first(where: { string($0, kAXIdentifierAttribute) == "saveAsNameTextField" }),
       string(stillName, kAXValueAttribute) == filename { _ = panelKey(36) }
    var previous: UInt64 = 0; var stable = 0
    for _ in 0..<60 {
        guard threadURL(snapshot()) == expected else { return downloadFailed("native_download_thread_changed") }
        if let details = try? FileManager.default.attributesOfItem(atPath: destination.path),
           details[.type] as? FileAttributeType == .typeRegular,
           let size = (details[.size] as? NSNumber)?.uint64Value, size > 0, size <= 150_000_000 {
            stable = size == previous ? stable + 1 : 0; previous = size
            if stable >= 3 {
                try? FileManager.default.removeItem(at: receiptURL)
                return ["name": filename, "label": String(label(button).prefix(200))]
            }
        }
        settle(0.1)
    }
    cancelSave(); return downloadFailed("native_download_file_unconfirmed")
}

let initial = snapshot()
if command == "probe" {
    let ready = (emptyNewThread(initial) || newChatButton(initial) != nil || chatMenuButton(initial) != nil || backToMainButton(initial) != nil) && mainWindowFocused()
    emit(["ok": true, "installed": true, "permission": true, "version": version,
          "composer_ready": ready])
}
guard let requestID = input["request_id"] as? String, UUID(uuidString: requestID) != nil else { fail("invalid_request") }
let requestTag = "[MODELDESK_REQUEST:\(requestID)]"
let closingTag = "[MODELDESK_DONE:\(requestID)]"
if command == "submit" {
    guard let prompt = input["prompt"] as? String, !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, prompt.count <= 24000, input["thread_url"] == nil || input["thread_url"] is NSNull else { fail("invalid_request") }
    guard activate(), mainWindowFocused() else { fail("window_focus_lost") }
    var activeTree = snapshot()
    guard !isBusy(activeTree), !hasApproval(activeTree) else { fail("muse_busy_or_waiting_user") }
    if !emptyNewThread(activeTree) {
        if newChatButton(activeTree) == nil, chatMenuButton(activeTree) == nil, let back = backToMainButton(activeTree) {
            guard press(back) else { fail("new_chat_unavailable") }
            settle(0.3); activeTree = snapshot()
        }
        if newChatButton(activeTree) == nil, let menu = chatMenuButton(activeTree) {
            guard press(menu) else { fail("new_chat_unavailable") }
            settle(0.3); activeTree = snapshot()
        }
        guard let newChat = newChatButton(activeTree), press(newChat) else { fail("new_chat_unavailable") }
        settle(0.6)
    }
    let fresh = snapshot()
    guard threadURL(fresh) == "https://muse.ai/thread/new", let messages = chatRoot(fresh), textIn(messages).isEmpty, let box = composer(fresh).flatMap(rect) else { fail("new_chat_not_confirmed") }
    guard click(CGPoint(x: box.minX + box.width * 0.35, y: box.midY)) else { fail("window_focus_lost") }
    let clipboard = TemporaryClipboard()
    let sentinel = "MODELDESK_EMPTY_" + UUID().uuidString
    clipboard.put(sentinel)
    guard key(0, flags: .maskCommand), key(8, flags: .maskCommand) else { clipboard.restore(); fail("window_focus_lost") }
    let existing = clipboard.read()
    guard existing == sentinel || existing.isEmpty else { clipboard.owned.insert(existing); clipboard.restore(); fail("composer_has_draft") }
    let payload = requestTag + "\n" + prompt
    clipboard.put(payload)
    guard key(9, flags: .maskCommand), key(0, flags: .maskCommand), key(8, flags: .maskCommand) else { clipboard.restore(); fail("window_focus_lost") }
    let copied = clipboard.read()
    if copied.hasPrefix(requestTag) { clipboard.owned.insert(copied) }
    guard copied == payload, threadURL(snapshot()) == "https://muse.ai/thread/new", foreground(), mainWindowFocused() else { clipboard.restore(); fail("draft_not_verified") }
    guard key(124), key(36) else { clipboard.restore(); fail("send_outcome_unknown", uncertain: true) }
    clipboard.restore()
    for _ in 0..<40 {
        settle(0.25)
        let sent = snapshot(); let url = threadURL(sent)
        if validThread(url), let chat = chatRoot(sent), textIn(chat).contains(requestTag) {
            emit(["ok": true, "sent": true, "thread_url": url, "request_id": requestID])
        }
    }
    fail("send_outcome_unknown", uncertain: true)
}
if command == "observe" {
    guard let expected = input["thread_url"] as? String, validThread(expected), threadURL(initial) == expected else { fail("thread_changed") }
    guard let chat = chatRoot(initial), textIn(chat).contains(requestTag) else { fail("request_not_visible") }
    var messages: [[String: String]] = []
    var resources: [[String: String]] = []
    var replyRoots: [AXUIElement] = []
    var replyParts: [String] = []
    var attachments: [(button: AXUIElement, filename: String)] = []
    var sawRequest = false
    var sawAssistantText = false
    var completedReply = false
    for item in children(chat) {
        let text = textIn(item)
        if text.contains(requestTag) {
            if sawRequest { break }
            sawRequest = true; continue
        }
        guard sawRequest else { continue }
        // A new user turn ends this task's reply scope. Do not attach later
        // assistants' media, even if the original request is still on screen.
        if isUserMessage(item, text) || text.contains("[MODELDESK_REQUEST:") { break }
        // Assistant containers expose their own Copy response control; dates,
        // user messages, suggested prompts and tool approval cards do not.
        let controls = nodes(item)
        let legacyAssistant = controls.contains { label($0) == "Copy response" }
        // New Muse versions expose reply actions via a popup rather than a
        // permanent Copy response button. Accept this shape only with the
        // unique current-task closing marker, never for an arbitrary popup.
        let assistantActions = controls.contains { string($0, kAXRoleAttribute) == kAXPopUpButtonRole && label($0) == "More options" }
        let downloadButtons = controls.filter { string($0, kAXRoleAttribute) == kAXButtonRole && ["Download video", "Download image", "Download picture"].contains(label($0)) }
        let mediaOpen = controls.first { label($0).hasPrefix("Open animated video ") || label($0).hasPrefix("Open image ") || label($0).hasPrefix("Open picture ") }
        let mediaAttachment = !downloadButtons.isEmpty && mediaOpen != nil
        let framedReply = text.trimmingCharacters(in: .whitespacesAndNewlines).hasSuffix(closingTag) && assistantActions
        if legacyAssistant || assistantActions || (mediaAttachment && sawAssistantText) {
            // Restore only the marker boundary lost by rendered soft breaks;
            // do not alter the observed response body or invent any content.
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            let reply = framedReply ? String(trimmed.dropLast(closingTag.count)).trimmingCharacters(in: .whitespacesAndNewlines) + "\n" + closingTag : text
            if !trimmed.isEmpty && trimmed != closingTag { sawAssistantText = true }
            if !reply.isEmpty { replyParts.append(reply) }
            if sawAssistantText { replyRoots.append(item) }
            if mediaAttachment && sawAssistantText, let opened = mediaOpen {
                let openedLabel = label(opened)
                let prefix = ["Open animated video ", "Open image ", "Open picture "].first { openedLabel.hasPrefix($0) } ?? ""
                attachments.append(contentsOf: downloadButtons.map { (button: $0, filename: String(openedLabel.dropFirst(prefix.count))) })
            }
            if trimmed.hasSuffix(closingTag) {
                completedReply = true
                break
            }
        }
    }
    if !replyParts.isEmpty { messages.append(["role": "assistant", "text": String(replyParts.joined(separator: "\n\n").prefix(120000))]) }
    guard threadURL(snapshot()) == expected else { fail("thread_changed") }
    let finished = completedReply && !isBusy(initial) && !hasApproval(initial)
    var downloaded: [[String: String]] = []; var downloadErrors: [String] = []
    if finished {
        var seen = Set<String>()
        for root in replyRoots {
            for resource in resourcesIn(root) where resources.count < 12 && !seen.contains(resource["url"] ?? "") {
                seen.insert(resource["url"] ?? ""); resources.append(resource)
            }
        }
        if let directory = stagingDirectory() {
            for attachment in attachments.prefix(4) {
                if let file = downloadAttachment(attachment.button, filename: attachment.filename, directory: directory, expected: expected) { downloaded.append(file) }
                else { downloadErrors.append(downloadDiagnostic) }
            }
        } else if !attachments.isEmpty { downloadErrors.append("native_private_staging_unavailable") }
    }
    guard threadURL(snapshot()) == expected else { fail("thread_changed") }
    emit(["ok": true, "request_id": requestID, "thread_url": expected, "messages": messages,
          "busy": isBusy(initial), "waiting_user": hasApproval(initial),
          "resource_request_id": finished ? requestID : NSNull(), "resources": finished ? resources : [],
          "file_request_id": finished ? requestID : NSNull(), "downloaded_files": downloaded, "download_errors": downloadErrors])
}
fail("unsupported_command")
