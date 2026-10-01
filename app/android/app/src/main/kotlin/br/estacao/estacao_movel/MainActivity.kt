package br.estacao.estacao_movel

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine

class MainActivity : FlutterActivity() {
    private var microfone: MicrofoneDecibelimetro? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        microfone = MicrofoneDecibelimetro(this, flutterEngine.dartExecutor.binaryMessenger)
    }

    override fun onResume() {
        super.onResume()
        microfone?.onResume()
    }

    override fun onPause() {
        microfone?.onPause()
        super.onPause()
    }

    override fun onStop() {
        microfone?.onStop()
        super.onStop()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        if (microfone?.onRequestPermissionsResult(requestCode, grantResults) != true) {
            super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        }
    }

    override fun cleanUpFlutterEngine(flutterEngine: FlutterEngine) {
        microfone?.dispose()
        microfone = null
        super.cleanUpFlutterEngine(flutterEngine)
    }

    override fun onDestroy() {
        microfone?.dispose()
        microfone = null
        super.onDestroy()
    }
}
