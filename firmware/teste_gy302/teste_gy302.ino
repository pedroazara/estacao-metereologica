#include <AccelStepper.h>

#define STEP_PIN 2
#define DIR_PIN  3

AccelStepper motor(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);

float velocidade = 300;
bool continuo = false;

void setup() {
  Serial.begin(115200);

  motor.setMaxSpeed(2000);
  motor.setSpeed(velocidade);

  Serial.println("Controle do motor");
  Serial.println("-----------------");
  Serial.println("v 300  -> define velocidade");
  Serial.println("c 1    -> gira continuamente horario");
  Serial.println("c 0    -> gira continuamente anti-horario");
  Serial.println("r 200  -> gira 200 passos");
  Serial.println("s      -> parar");
  Serial.println("?      -> ajuda");
}

void loop() {

  // Se estiver em modo contínuo
  if (continuo) {
    motor.runSpeed();
  }
  // Caso contrário, executa movimentos normais
  else {
    motor.run();
  }

  if (Serial.available() > 0) {

    String comando = Serial.readStringUntil('\n');
    comando.trim();

    // Velocidade
    if (comando.startsWith("v ")) {

      velocidade = comando.substring(2).toFloat();

      motor.setSpeed(velocidade);

      Serial.print("Velocidade: ");
      Serial.print(velocidade);
      Serial.println(" passos/s");
    }

    // Giro contínuo
    else if (comando.startsWith("c ")) {

      int sentido = comando.substring(2).toInt();

      continuo = true;

      if (sentido == 1) {
        motor.setSpeed(abs(velocidade));
        Serial.println("Girando continuamente -> horario");
      }
      else {
        motor.setSpeed(-abs(velocidade));
        Serial.println("Girando continuamente -> anti-horario");
      }
    }

    // Movimento por quantidade de passos
    else if (comando.startsWith("r ")) {

      continuo = false;

      long passos = comando.substring(2).toInt();

      motor.move(passos);

      Serial.print("Movendo ");
      Serial.print(passos);
      Serial.println(" passos");
    }

    // Parar
    else if (comando == "s") {

      continuo = false;
      motor.stop();

      Serial.println("Motor parado");
    }

    // Ajuda
    else if (comando == "?") {

      Serial.println();
      Serial.println("v 300  -> velocidade 300 passos/s");
      Serial.println("c 1    -> giro continuo horario");
      Serial.println("c 0    -> giro continuo anti-horario");
      Serial.println("r 200  -> 200 passos");
      Serial.println("r -200 -> 200 passos no sentido contrario");
      Serial.println("s      -> parar");
      Serial.println();
    }

    else {
      Serial.println("Comando desconhecido. Digite ?");
    }
  }
}