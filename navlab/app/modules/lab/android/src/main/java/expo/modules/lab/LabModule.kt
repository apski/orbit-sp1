// Expo binding for core/lab/LabCore.kt (Android counterpart of LabModule.swift).
package expo.modules.lab

import android.content.Context
import expo.modules.kotlin.AppContext
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import expo.modules.kotlin.views.ExpoView
import lab.LabCore

class LabModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("Lab")

    // Launch args from the intent extra `args` (LabPackage stashes them in onCreate).
    Constant("args") { LabCore.args }

    Function("begin") { name: String, msg: String -> LabCore.begin(name, msg) }
    Function("end") { handle: Int, msg: String -> LabCore.end(handle, msg) }
    Function("event") { name: String, msg: String -> LabCore.event(name, msg) }
    Function("footprintMB") { LabCore.footprintMB() }
    Function("sinceProcessStartMs") { LabCore.sinceProcessStartMs() }
    Function("startHitches") { LabCore.startHitches(appContext.currentActivity) }
    Function("exit") { LabCore.exit(appContext.currentActivity) }

    View(LabProbeView::class) {
      Prop("handle") { view: LabProbeView, h: Int -> view.handle = h; view.fire() }
      Prop("event") { view: LabProbeView, e: String -> view.event = e; view.fire() }
    }
  }
}

/** Zero-size view placed last in a screen. Once attached to the window, at the next Choreographer frame
 *  (the frame that attached it has been laid out and drawn) it ends trace interval `handle`, or for event
 *  "ready" emits the startup event once. Same point as LabProbeView on iOS and the Compose twin's probe. */
class LabProbeView(context: Context, appContext: AppContext) : ExpoView(context, appContext) {
  var handle = 0
  var event = ""

  override fun onAttachedToWindow() {
    super.onAttachedToWindow()
    fire()
  }

  fun fire() {
    if (!isAttachedToWindow) return
    if (handle > 0) {
      val h = handle
      handle = 0
      LabCore.nextFrame { LabCore.end(h, "") }
    }
    if (event == "ready" && !readySent) {
      readySent = true
      LabCore.nextFrame {
        LabCore.event("ready", "ms=%.1f mb=%.1f".format(LabCore.sinceProcessStartMs(), LabCore.footprintMB()))
      }
    }
  }

  companion object { private var readySent = false }
}
