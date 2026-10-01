/*
 * KY-038 - amplitude por janela, mais legivel no Serial Plotter que o
 * sinal bruto (que oscila rapido demais pra enxergar o pico).
 */

static const uint8_t PINO_KY038_AO = 32;
static const uint16_t JANELA_MS = 20;  // menor = mais responsivo, maior = mais suave

void setup() {
  Serial.begin(115200);
}

void loop() {
  uint16_t minimo = 4095;
  uint16_t maximo = 0;

  uint32_t fim = millis() + JANELA_MS;
  while (millis() < fim) {
    uint16_t v = analogRead(PINO_KY038_AO);
    if (v < minimo) minimo = v;
    if (v > maximo) maximo = v;
  }

  uint16_t amplitude = maximo - minimo;
  if (amplitude < 1) amplitude = 1;  // log10(0) nao existe

  float dB = 26.475f * log10f(amplitude) + 21.926f;
  if (dB < 0) dB = 0;

  Serial.println(dB, 1);
}