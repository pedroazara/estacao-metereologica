package br.estacao.estacao_movel

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs
import kotlin.math.max

/** Envia apenas estatísticas PCM, sem armazenar ou transmitir o áudio capturado. */
class MicrofoneDecibelimetro(
    private val activity: Activity,
    messenger: BinaryMessenger,
) : MethodChannel.MethodCallHandler, EventChannel.StreamHandler {
    private val main = Handler(Looper.getMainLooper())
    // Uma única fila garante que uma nova captura só abra após liberar a anterior.
    private val reader = Executors.newSingleThreadExecutor { task ->
        Thread(task, "skylog-microfone")
    }
    private val methods = MethodChannel(messenger, "skylog/decibelimetro")
    private val events = EventChannel(messenger, "skylog/decibelimetro/amostras")
    private var sink: EventChannel.EventSink? = null
    private var resumed = false
    private var disposed = false
    private var permissionInFlight = false
    private var pendingStart: MethodChannel.Result? = null
    private var session: Session? = null

    private class Session(var startResult: MethodChannel.Result?) {
        val cancelled = AtomicBoolean(false)
    }

    private class CaptureFailure(val code: String, message: String) : Exception(message)

    init {
        methods.setMethodCallHandler(this)
        events.setStreamHandler(this)
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "start" -> requestStart(result)
            "stop" -> {
                stop("cancelled", "Medição cancelada.")
                result.success(null)
            }
            "openSettings" -> {
                try {
                    activity.startActivity(Intent(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.parse("package:${activity.packageName}"),
                    ))
                    result.success(null)
                } catch (_: Exception) {
                    result.error("settings_unavailable", "Não foi possível abrir as configurações do aplicativo.", null)
                }
            }
            else -> result.notImplemented()
        }
    }

    override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
        stop("cancelled", "Medição reiniciada.")
        sink = events
    }

    override fun onCancel(arguments: Any?) {
        stop("cancelled", "Medição cancelada.")
        sink = null
    }

    fun onResume() {
        resumed = true
        if (!permissionInFlight && pendingStart != null) beginCapture()
    }

    fun onPause() {
        resumed = false
        if (session != null) {
            stop("background", "A medição foi interrompida ao sair do aplicativo.")
            sink?.success(mapOf("type" to "stopped", "reason" to "background"))
        }
        // O diálogo de permissão também pausa a Activity. O início aguarda
        // onResume, e só será cancelado se o app realmente sair de primeiro plano.
    }

    fun onStop() {
        stop("background", "Volte ao aplicativo e inicie a medição novamente.")
    }

    fun onRequestPermissionsResult(requestCode: Int, grantResults: IntArray): Boolean {
        if (requestCode != PERMISSION_REQUEST) return false
        permissionInFlight = false
        if (pendingStart == null || disposed) return true
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            if (resumed) beginCapture()
        } else {
            val permanent = !activity.shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO)
            val result = pendingStart
            pendingStart = null
            result?.error(
                if (permanent) "permission_denied_forever" else "permission_denied",
                if (permanent) "Permita o acesso ao microfone nas configurações do aplicativo."
                else "O decibelímetro precisa da permissão do microfone para medir.",
                null,
            )
        }
        return true
    }

    fun dispose() {
        if (disposed) return
        disposed = true
        resumed = false
        stop("cancelled", "Medição encerrada.")
        sink = null
        methods.setMethodCallHandler(null)
        events.setStreamHandler(null)
        reader.shutdown()
    }

    private fun requestStart(result: MethodChannel.Result) {
        when {
            disposed || !resumed -> result.error("background", "Abra o aplicativo para iniciar a medição.", null)
            Build.VERSION.SDK_INT < Build.VERSION_CODES.M -> result.error("unsupported", "O decibelímetro requer Android 6 ou mais recente.", null)
            session != null || pendingStart != null || permissionInFlight -> result.error("busy", "Já existe uma solicitação de medição em andamento.", null)
            sink == null -> result.error("not_listening", "A tela de medição ainda não está pronta. Tente novamente.", null)
            else -> {
                pendingStart = result
                if (activity.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                    beginCapture()
                } else {
                    permissionInFlight = true
                    try {
                        activity.requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), PERMISSION_REQUEST)
                    } catch (_: Exception) {
                        permissionInFlight = false
                        pendingStart = null
                        result.error("permission_denied", "Não foi possível solicitar acesso ao microfone.", null)
                    }
                }
            }
        }
    }

    private fun beginCapture() {
        if (disposed || !resumed || permissionInFlight) return
        val result = pendingStart ?: return
        pendingStart = null
        val next = Session(result)
        session = next
        reader.execute { capture(next) }
    }

    private fun stop(code: String, message: String) {
        pendingStart?.error(code, message, null)
        pendingStart = null
        val previous = session
        session = null
        previous?.cancelled?.set(true)
        previous?.startResult?.error(code, message, null)
        previous?.startResult = null
        // A thread de leitura observa o cancelamento sem bloquear a interface
        // e é a única responsável por parar/liberar o AudioRecord.
    }

    private fun capture(current: Session) {
        var record: AudioRecord? = null
        try {
            if (current.cancelled.get()) return
            val manager = activity.getSystemService(Context.AUDIO_SERVICE) as AudioManager
            val internalMic = manager.getDevices(AudioManager.GET_DEVICES_INPUTS)
                .firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_MIC }
                ?: throw CaptureFailure("microphone_unavailable", "Não foi encontrado um microfone interno neste aparelho.")
            val unprocessed = Build.VERSION.SDK_INT >= Build.VERSION_CODES.N &&
                manager.getProperty(AudioManager.PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED) == "true"
            val source = if (unprocessed) MediaRecorder.AudioSource.UNPROCESSED else MediaRecorder.AudioSource.VOICE_RECOGNITION
            val sourceName = if (unprocessed) "UNPROCESSED" else "VOICE_RECOGNITION"
            record = createRecord(source)
            record.setPreferredDevice(internalMic)
            if (current.cancelled.get()) return
            record.startRecording()
            if (record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                throw CaptureFailure("microphone_unavailable", "O microfone não iniciou. Feche outros aplicativos que usam áudio e tente novamente.")
            }
            val sampleRate = record.sampleRate
            val buffer = ShortArray(sampleRate / 10)
            var count = 0
            var routeId: Int? = null
            var deviceKey: String? = null
            val startedAt = SystemClock.elapsedRealtime()
            var lastDataAt = startedAt
            while (!current.cancelled.get()) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
                    record.activeRecordingConfiguration?.isClientSilenced == true) {
                    throw CaptureFailure("microphone_unavailable", "O Android bloqueou o áudio do microfone. Confira o acesso ao microfone e feche outros aplicativos de gravação.")
                }
                val read = record.read(buffer, count, buffer.size - count, AudioRecord.READ_NON_BLOCKING)
                if (read < 0) {
                    throw CaptureFailure("microphone_unavailable", "A captura do microfone foi interrompida. Inicie uma nova medição.")
                }
                val route = record.routedDevice
                if (route != null) {
                    if (route.type != AudioDeviceInfo.TYPE_BUILTIN_MIC) {
                        throw CaptureFailure("route_changed", "O áudio foi direcionado a um microfone externo. Desconecte o acessório e inicie novamente usando o celular.")
                    }
                    if (routeId != null && route.id != routeId) {
                        throw CaptureFailure("route_changed", "O microfone utilizado mudou. Inicie outra medição e confira a calibração.")
                    }
                    if (routeId == null) {
                        routeId = route.id
                        deviceKey = "${Build.MANUFACTURER}/${Build.MODEL}/builtin:${route.id}/$sourceName/$sampleRate"
                        val metadata = mapOf("sampleRate" to sampleRate, "source" to sourceName, "deviceKey" to deviceKey)
                        main.post {
                            if (session === current && !current.cancelled.get() && resumed) {
                                current.startResult?.success(metadata)
                                current.startResult = null
                            }
                        }
                    }
                } else if (routeId != null) {
                    throw CaptureFailure("route_changed", "O microfone foi desconectado. Inicie uma nova medição.")
                }
                if (read == 0) {
                    if (SystemClock.elapsedRealtime() - lastDataAt > 3000) {
                        throw CaptureFailure("microphone_unavailable", "Não chegaram amostras do microfone. Confira o acesso ao microfone e tente novamente.")
                    }
                    Thread.sleep(5)
                    continue
                }
                lastDataAt = SystemClock.elapsedRealtime()
                if (deviceKey == null) {
                    if (SystemClock.elapsedRealtime() - startedAt > 2000) {
                        throw CaptureFailure("microphone_unavailable", "Não foi possível identificar o microfone utilizado pelo Android.")
                    }
                    // Alguns aparelhos só informam a rota após os primeiros
                    // frames. Descartá-los evita medir antes de confirmar a origem.
                    continue
                }
                count += read
                if (count < buffer.size) continue
                var sum = 0.0
                var sumSquares = 0.0
                var peak = 0.0
                for (sample in buffer) {
                    val value = sample.toDouble() / 32768.0
                    sum += value
                    sumSquares += value * value
                    peak = max(peak, abs(value))
                }
                val mean = sum / count
                val event = mapOf(
                    "type" to "sample",
                    "meanSquare" to (sumSquares / count - mean * mean).coerceIn(0.0, 1.0),
                    "peak" to peak,
                    "count" to count,
                    "sampleRate" to sampleRate,
                    "deviceKey" to deviceKey,
                )
                main.post {
                    if (session === current && !current.cancelled.get() && resumed) sink?.success(event)
                }
                count = 0
            }
        } catch (failure: CaptureFailure) {
            fail(current, failure.code, failure.message ?: "Não foi possível medir o som.")
        } catch (_: SecurityException) {
            fail(current, "permission_denied", "O acesso ao microfone foi bloqueado. Confira a permissão do aplicativo.")
        } catch (_: Exception) {
            fail(current, "microphone_unavailable", "Não foi possível iniciar o microfone. Feche outros aplicativos de gravação e tente novamente.")
        } finally {
            // Não existe AudioRecord compartilhado com a próxima sessão.
            try {
                if (record?.recordingState == AudioRecord.RECORDSTATE_RECORDING) record.stop()
            } catch (_: Exception) {
                // Mesmo quando o dispositivo falha, a liberação ainda é necessária.
            }
            record?.release()
        }
    }

    private fun createRecord(source: Int): AudioRecord {
        for (sampleRate in intArrayOf(48000, 44100)) {
            val minimum = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            if (minimum <= 0) continue
            val candidate = try {
                AudioRecord(
                    source, sampleRate, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, max(minimum, sampleRate / 10 * 2 * 4),
                )
            } catch (failure: SecurityException) {
                throw failure
            } catch (_: IllegalArgumentException) {
                continue
            }
            if (candidate.state == AudioRecord.STATE_INITIALIZED) return candidate
            candidate.release()
        }
        throw CaptureFailure("unsupported", "Este aparelho não disponibilizou captura de áudio compatível com o decibelímetro.")
    }

    private fun fail(current: Session, code: String, message: String) {
        main.post {
            if (session !== current || current.cancelled.get()) return@post
            val startResult = current.startResult
            current.startResult = null
            session = null
            current.cancelled.set(true)
            if (startResult != null) startResult.error(code, message, null)
            else sink?.error(code, message, null)
        }
    }

    companion object {
        private const val PERMISSION_REQUEST = 21316
    }
}
