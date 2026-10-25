// Trace sections for the navigation lab, shared by both Android arms: the RN app (via LabModule.kt) and the
// Compose twin (native-android/, which compiles this directory), so both are measured with the same ruler.
// Android counterpart of LabCore.swift. lab/android/analyze.py reads these from a Perfetto trace:
//   intervals  async section "lab:<name> id=<n>"        (begin/end may be on different threads, like signposts)
//   messages   instant slices "lab:b id=<n> k=v ..."     begin message
//                             "lab:e id=<n> k=v ..."     end message
//                             "lab:ev#<n> <event> k=v .." events (ready, config, done, layout, hitch, ...)
// Trace.beginSection rejects names over 127 chars, so long messages are split into several slices with the
// same prefix; the analyzer joins them back.
package lab

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.Process
import android.os.SystemClock
import android.os.Trace
import android.system.Os
import android.system.OsConstants
import android.view.Choreographer
import android.view.WindowManager
import java.io.File
import kotlin.system.exitProcess

// The lab needs API 29+ (async trace sections, thermal status); the RN app's minSdk is lower.
@SuppressLint("NewApi")
object LabCore {
  private val open = HashMap<Int, String>()
  private var nextId = 0
  private var nextEvent = 0
  private var app: Context? = null

  /** Launch args as one string extra, e.g. `am start ... --es args "-depth 3 -kind mix"`: ProcessInfo.arguments on iOS. */
  var args: List<String> = listOf("app")
    private set

  fun init(activity: Activity) {
    app = activity.applicationContext
    args = listOf("app") + (activity.intent?.getStringExtra("args") ?: "").split(" ").filter { it.isNotEmpty() }
  }

  fun begin(name: String, msg: String): Int {
    val id: Int
    val section: String
    synchronized(this) {
      id = ++nextId
      section = "lab:$name id=$id"
      open[id] = section
    }
    mark("lab:b id=$id", msg)
    Trace.beginAsyncSection(section, id)
    return id
  }

  fun end(handle: Int, msg: String) {
    val section = synchronized(this) { open.remove(handle) } ?: return
    mark("lab:e id=$handle", msg)
    Trace.endAsyncSection(section, handle)
  }

  fun event(name: String, msg: String) {
    // Thermal state rides on the run's first and last event, as on iOS.
    val m = when (name) {
      "done" -> "$msg thermal_end=${thermal()}"
      "config" -> "$msg thermal=${thermal()}"
      else -> msg
    }
    mark("lab:ev#${synchronized(this) { ++nextEvent }} $name", m)
  }

  private fun mark(prefix: String, msg: String) {
    var line = prefix
    for (p in msg.split(" ").filter { it.isNotEmpty() }) {
      if (line.length + 1 + p.length > 127 && line != prefix) { slice(line); line = prefix }
      line = "$line $p".take(127)
    }
    slice(line)
  }

  private fun slice(s: String) {
    Trace.beginSection(s)
    Trace.endSection()
  }

  fun thermal(): String {
    val pm = app?.getSystemService(Context.POWER_SERVICE) as? PowerManager ?: return "unknown"
    return when (pm.currentThermalStatus) {
      PowerManager.THERMAL_STATUS_NONE -> "nominal"
      PowerManager.THERMAL_STATUS_LIGHT, PowerManager.THERMAL_STATUS_MODERATE -> "fair"
      PowerManager.THERMAL_STATUS_SEVERE -> "serious"
      else -> "critical"
    }
  }

  /** Resident set size (iOS reports phys_footprint; RSS is the cheap Android read). */
  fun footprintMB(): Double = try {
    val pages = File("/proc/self/statm").readText().split(" ")[1].toLong()
    pages * Os.sysconf(OsConstants._SC_PAGESIZE) / 1_048_576.0
  } catch (e: Exception) { -1.0 }

  /** ms since zygote forked this process. */
  fun sinceProcessStartMs(): Double = (SystemClock.elapsedRealtime() - Process.getStartElapsedRealtime()).toDouble()

  /** Main-thread frames late by more than half a frame, as "hitch" events (CADisplayLink counterpart).
   *  Also keeps the screen on for the run. */
  fun startHitches(activity: Activity?) {
    Handler(Looper.getMainLooper()).post {
      activity?.window?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
      if (hitching) return@post
      hitching = true
      val period = 1e9 / (activity?.windowManager?.defaultDisplay?.refreshRate ?: 60f)
      var last = 0L
      val cb = object : Choreographer.FrameCallback {
        override fun doFrame(t: Long) {
          if (last > 0) {
            val late = (t - last) - period
            if (late > period * 0.5) event("hitch", "ms=${Math.round(late / 1e6)}")
          }
          last = t
          Choreographer.getInstance().postFrameCallback(this)
        }
      }
      Choreographer.getInstance().postFrameCallback(cb)
    }
  }
  private var hitching = false

  /** Runs `fn` on the main thread at the start of the next Choreographer frame: the previous frame's
   *  traversal (measure/layout/draw) and RenderThread sync have happened. iOS: one runloop turn after attach. */
  fun nextFrame(fn: () -> Unit) {
    Handler(Looper.getMainLooper()).post { Choreographer.getInstance().postFrameCallback { fn() } }
  }

  /** Ends the process so the sweep's Perfetto session can stop. */
  fun exit(activity: Activity?) {
    Handler(Looper.getMainLooper()).postDelayed({
      activity?.finishAndRemoveTask()
      exitProcess(0)
    }, 300)
  }
}
