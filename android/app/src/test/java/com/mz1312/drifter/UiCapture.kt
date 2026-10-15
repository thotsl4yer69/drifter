package com.mz1312.drifter

import android.graphics.Bitmap
import android.graphics.Canvas
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.AndroidComposeTestRule
import org.junit.rules.TestRule
import java.io.File

/** Render the real decor view; Robolectric has no device PixelCopy callback. */
fun <R : TestRule, A : ComponentActivity> AndroidComposeTestRule<R, A>.captureScreen(name: String) {
    waitForIdle()
    runOnUiThread {
        val view = activity.window.decorView
        check(view.width > 0 && view.height > 0)
        val bitmap = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
        view.draw(Canvas(bitmap))
        val file = File("build/reports/ui/$name.png")
        file.parentFile?.mkdirs()
        file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }
}
