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
    var sawRequest = false
    for item in children(chat) {
        let text = textIn(item)
        if text.contains(requestTag) { sawRequest = true; continue }
        guard sawRequest, !text.isEmpty, !text.hasPrefix("You:"), !text.hasPrefix("User message:"), !text.hasPrefix("你：") else { continue }
        // Assistant containers expose their own Copy response control; dates,
        // user messages, suggested prompts and tool approval cards do not.
        let controls = nodes(item)
        let legacyAssistant = controls.contains { label($0) == "Copy response" }
        // New Muse versions expose reply actions via a popup rather than a
        // permanent Copy response button. Accept this shape only with the
        // unique current-task closing marker, never for an arbitrary popup.
        let framedReply = text.trimmingCharacters(in: .whitespacesAndNewlines).hasSuffix(closingTag)
            && controls.contains { string($0, kAXRoleAttribute) == kAXPopUpButtonRole && label($0) == "More options" }
        if legacyAssistant || framedReply {
            // Restore only the marker boundary lost by rendered soft breaks;
            // do not alter the observed response body or invent any content.
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            let reply = framedReply ? String(trimmed.dropLast(closingTag.count)).trimmingCharacters(in: .whitespacesAndNewlines) + "\n" + closingTag : text
            messages.append(["role": "assistant", "text": String(reply.prefix(100000))])
        }
    }
    guard threadURL(snapshot()) == expected else { fail("thread_changed") }
    emit(["ok": true, "request_id": requestID, "thread_url": expected, "messages": messages, "busy": isBusy(initial), "waiting_user": hasApproval(initial)])
}
fail("unsupported_command")
