// Expo binding for LabCore.swift.
import ExpoModulesCore

public class LabModule: Module {
  public func definition() -> ModuleDefinition {
    Name("Lab")

    // Launch args, e.g. `-depth 3 -kind tabs`. xctrace --launch passes them through.
    Constant("args") { ProcessInfo.processInfo.arguments }

    Function("begin") { (name: String, msg: String) -> Int in labBegin(name, msg) }
    Function("end") { (handle: Int, msg: String) in labEnd(handle, msg) }
    Function("event") { (name: String, msg: String) in labEvent(name, msg) }
    Function("footprintMB") { labFootprintMB() }
    Function("sinceProcessStartMs") { labSinceProcessStartMs() }
    Function("startHitches") { HitchMonitor.shared.start() }
    // Ends the process so `xctrace record --launch` stops on its own.
    Function("exit") { DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { exit(0) } }

    // Lists lab: scripted fixed-velocity scroll of the "lab.list" view; ends `handle` and resolves with its stats.
    AsyncFunction("autoscroll") { (speed: Double, distance: Double, handle: Int, promise: Promise) in
      LabScroller.run(speed: speed, distance: distance, handle: handle) { promise.resolve($0) }
    }

    // Startup lab: `-natives N` registers N synthetic Expo modules (LabGen.swift) at app start, the way
    // Expo creates every autolinked module when the app context starts. No-op without the arg.
    OnCreate {
      let a = ProcessInfo.processInfo.arguments
      if let i = a.firstIndex(of: "-lab"), i + 1 < a.count, a[i + 1] == "startup" {
        labEvent("mark", String(format: "phase=native ms=%.1f", labSinceProcessStartMs()))  // Expo app context up
      }
      guard let i = a.firstIndex(of: "-natives"), i + 1 < a.count, let n = Int(a[i + 1]), n > 0,
            let reg = self.appContext?.moduleRegistry else { return }
      for t in labGenModules.prefix(n) { reg.register(moduleType: t, name: nil) }
    }

    View(LabProbeView.self) {
      Prop("handle") { (view: LabProbeView, h: Int) in view.handle = h }
      Prop("event") { (view: LabProbeView, e: String) in view.event = e }
    }
  }
}

/// Zero-size view placed last in a screen. One runloop turn after the screen's native subtree is
/// attached to the window (so the CA commit has laid it out) it ends signpost interval `handle`, or for
/// event "ready" emits the startup event once: the same "content on screen" point the UIKit twin uses.
/// RN's onLayout fires earlier, from the shadow tree, before the main thread has mounted anything.
final class LabProbeView: ExpoView {
  private static var readySent = false
  var handle = 0 { didSet { fire() } }
  var event = "" { didSet { fire() } }

  override func didMoveToWindow() {
    super.didMoveToWindow()
    fire()
  }

  private func fire() {
    guard window != nil else { return }
    if handle > 0 {
      let h = handle
      handle = 0
      DispatchQueue.main.async { labEnd(h, "") }
    }
    if event == "ready", !Self.readySent {
      Self.readySent = true
      DispatchQueue.main.async {
        labEvent("ready", String(format: "ms=%.1f mb=%.1f", labSinceProcessStartMs(), labFootprintMB()))
      }
    }
  }
}
