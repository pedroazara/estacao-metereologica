# Skylog — aplicativo Android

Aplicativo Flutter da estação meteorológica: permite escolher e conferir os sensores antes de gravar uma coleta. Combina os sensores do telefone com as leituras recebidas do ESP32 por Bluetooth LE. O início usa o nome **Skylog**; o nome instalado no Android ainda é **Estacao Movel** e o pacote Flutter é `estacao_movel`.

## Sensores definidos para o projeto

A configuração definida em 01/10/2026 usa o telefone da pessoa que faz a
medição para localização, movimento e som. O ESP32 recebe somente os três
módulos externos abaixo e transmite suas leituras ao aplicativo:

| Medição | Origem definida | Situação no aplicativo |
| --- | --- | --- |
| Localização e percurso | GPS do telefone | Captura e gravação disponíveis |
| Movimento | Sensores de movimento do telefone | Captura e gravação ainda precisam ser implementadas |
| Nível sonoro | Microfone do telefone | Captura e gravação de níveis disponíveis; calibração descrita abaixo |
| Temperatura e umidade | Sensor ambiental RS485, ligado ao ESP32 | Recebimento por Bluetooth disponível |
| Luminosidade | GY-302 / BH1750, ligado ao ESP32 | Recebimento por Bluetooth disponível |
| Temperatura auxiliar | KY-028, ligado ao ESP32 | Recebimento por Bluetooth disponível |

O **WTGAHRS3-485** (GPS e movimento/IMU), o **KY-038** (som) e o
**KY-013** (temperatura auxiliar) foram retirados da configuração do projeto.
Os sensores que permanecem são externos à placa; seus footprints na PCB
representam somente os conectores.

Esta definição ainda precisa ser concluída no código: a seleção de sensores
continua oferecendo KY-013 e KY-038, e o contrato de comunicação mantém
referências ao GPS/IMU externo. Esses itens são legados a remover, não módulos
a instalar. O app já usa o GPS e o microfone do telefone, mas ainda não possui
captura dos sensores de movimento do telefone. Esta atualização de
documentação não altera o aplicativo nem o firmware.

## Pastas neste computador

| Conteúdo | Local |
| --- | --- |
| Android Studio | `/home/pedro-henrique/.local/opt/android-studio` |
| Flutter SDK | `/home/pedro-henrique/sdk/flutter` |
| Android SDK | `/home/pedro-henrique/Android/Sdk` |
| Projeto a abrir | `/home/pedro-henrique/Documentos/projetos/estacao-metereologica/app` |

O aplicativo continua dentro do repositório, junto dos componentes da estação. Não é necessário copiar o projeto para dentro da instalação do Android Studio.

## Abrir e executar

1. No menu de aplicativos do Linux, abra **Skylog — Android Studio**. O atalho abre a pasta correta. Também é possível usar **Android Studio > Open** e selecionar a pasta `app` indicada acima.
2. Na primeira abertura, conclua as telas de boas-vindas, se aparecerem. Se o assistente pedir o Android SDK, use `/home/pedro-henrique/Android/Sdk`; o SDK já está instalado. Aguarde o carregamento do projeto. Os plugins Flutter e Dart já foram instalados. Se aparecer **Pub get**, clique para obter as dependências.
3. Selecione a configuração **Skylog** e um celular no seletor de dispositivos.
4. Clique no triângulo verde **Run**. A configuração compartilhada em `.run/Skylog.run.xml` executa `lib/main.dart`.

Se o Studio pedir o SDK Flutter, informe `/home/pedro-henrique/sdk/flutter`. O caminho do Android SDK fica em **Tools > SDK Manager**. Se a configuração não aparecer, crie-a em **Run > Edit Configurations > + > Flutter**, usando o nome `Skylog` e a entrada `lib/main.dart`. Para o trabalho diário com Flutter, abra `app`; a subpasta `android` isolada serve para trabalhar na integração nativa. [Documentação oficial do editor Flutter](https://docs.flutter.dev/tools/android-studio).

## Usar um celular por USB

1. No celular, ative as **Opções do desenvolvedor** e a **Depuração USB**. Em muitos aparelhos, as opções aparecem depois de tocar sete vezes em **Número da versão**, em **Sobre o telefone**.
2. Conecte um cabo USB que transfira dados, desbloqueie o celular e aceite a autorização de depuração mostrada na tela.
3. Selecione o aparelho no Android Studio e clique em **Run**.
4. Ao abrir o app, permita acesso à localização e aos dispositivos próximos/Bluetooth quando solicitado. Ligue a localização e o Bluetooth para testar a coleta completa.

Se o aparelho aparecer como `unauthorized`, falta aceitar a autorização no celular. Se houver `no permissions` no Linux, verifique as regras USB e o grupo de acesso seguindo a [configuração oficial de aparelhos físicos](https://developer.android.com/studio/run/device).

Para conferir a conexão pelo terminal do Studio:

```bash
/home/pedro-henrique/Android/Sdk/platform-tools/adb devices
```

## Emulador e estação real

Nenhum emulador foi instalado nesta preparação. Comece pelo celular USB, que também exige menos memória do computador. Se quiser configurar um emulador depois, use **Tools > Device Manager > Create Virtual Device**, escolha um telefone e baixe uma imagem Android compatível. A virtualização também precisa estar disponível. [Preparação Android para Flutter](https://docs.flutter.dev/platform-integration/android/setup).

O emulador é útil para telas, navegação, permissões e posições simuladas. Para conferir GPS em campo, dados ambientais e conexão com o ESP32, use o celular real. Versões atuais do emulador oferecem recursos de Bluetooth virtual, mas isso não garante acesso à estação física pelo Bluetooth do computador. [Recursos de rede do emulador](https://developer.android.com/studio/run/emulator-networking).

Neste projeto, a posição e o som vêm do telefone; temperatura/umidade RS485,
luminosidade GY-302 e temperatura auxiliar KY-028 vêm da estação. A coleta
de movimento pelo telefone ainda está pendente. Sem o ESP32 conectado, as
telas de ajustes da estação e os dados desses módulos ficam indisponíveis.
A busca pela estação ocorre automaticamente por UUID de serviço; o nome
padrão do firmware é `esp32-01`.

## Editar e ver o resultado

- As telas ficam em `lib/telas`; `lib/main.dart` monta o aplicativo e as abas.
- Depois de alterar uma tela com o app executando em modo de depuração, use **Hot Reload**, o botão com um raio, para aplicar mudanças preservando o estado atual.
- Alterações em `main()` ou na inicialização podem exigir **Hot Restart**. Mudanças em código Android, permissões ou Gradle exigem parar e executar novamente.
- Encerre uma coleta antes de reiniciar ou parar o app durante o desenvolvimento.

O hot reload não executa novamente `main()` nem `initState()`. [Como funciona o hot reload](https://docs.flutter.dev/tools/hot-reload).

## Conectar à base

Na aba **Coletas**, o campo **Endereco da base** aceita somente o nome ou IP, sem `http://`, caminho ou porta. A porta usada pelo app é `8771`.

| Onde a base está rodando | Valor no campo |
| --- | --- |
| Raspberry Pi na mesma rede | `raspberrypi.local` ou o IP real do Raspberry Pi |
| Neste computador, acessada pelo emulador Android | `10.0.2.2` |
| Neste computador, acessada pelo celular na mesma rede | IP local deste computador, por exemplo `192.168.1.50` |

O IP da última linha é apenas um exemplo. Confirme o endereço real e se o servidor da base está em execução. Se `raspberrypi.local` não resolver, use o IP da base. O endereço `10.0.2.2` é o acesso especial do emulador ao computador; `localhost` dentro do app aponta para o próprio dispositivo. [Endereços de rede do emulador](https://developer.android.com/studio/run/emulator-networking-address).

Confirme o campo com Enter/Concluído no teclado; enviar uma sessão também salva o endereço. O indicador da base pode continuar usando o endereço anterior até reiniciar o aplicativo. Enquanto uma coleta está sendo gravada, a verificação periódica da base fica pausada.

O código consulta `http://<host>:8771/sessoes`, envia coletas para `/sync` e consulta planos em `/planos` e `/plano`. A base estar inacessível não impede a gravação local. A sincronização preserva os arquivos originais no telefone.

## Seleção, conferência e coleta

A navegação é **Início → Sensores → Mapa → Planos → Coletas**. O início mostra
a preparação, a quantidade de sensores escolhidos/prontos e os controles da
coleta. O botão **Selecionar sensores** abre a aba de conferência. A engrenagem
em Sensores abre os ajustes existentes da estação.

1. Escolha os sensores da configuração definida: GPS e som do celular;
   temperatura/umidade RS485, luminosidade GY-302 e temperatura KY-028 da
   estação. A escolha começa vazia e é salva localmente para a próxima
   abertura. Enquanto a interface não for atualizada, deixe KY-013 e KY-038
   desmarcados, inclusive se estiverem em uma seleção salva anteriormente.
   Movimento do telefone ainda não está disponível para seleção ou gravação.
2. Confira valores e estado em cada cartão. **Testar microfone** inicia a prévia
   de som, sem gravar uma sessão; o decibelímetro completo compartilha essa
   captura. Desmarcar o som encerra seu teste. As demais leituras são recebidas
   pelos serviços de GPS/Bluetooth já existentes.
3. **Iniciar coleta** exige ao menos um sensor e leitura recente de todos os
   escolhidos. GPS tem prazo de 5 s; som, 2 s; estação, o maior entre 5 s e três
   intervalos de publicação. Ausência de campo, valor inválido e `lux = -1`
   não viram zero; `lux = 0` é uma leitura válida. O estado confirma chegada de
   dados, não a calibração ou precisão física do sensor.
4. Durante a coleta, a escolha fica bloqueada. Somente sensores escolhidos
   entram no arquivo: GPS controla também o traçado; campos ambientais são
   filtrados por sensor; som do celular registra níveis, nunca áudio. A prévia
   anterior não entra na coleta. Ao encerrar, veja o arquivo em **Coletas**.

O primeiro envelope JSONL tem `src = selecao` e a lista imutável de IDs.
Os níveis do microfone usam `src = som_celular`, com `nivel_dbfs`,
`nivel_db_spl_estimado` (nulo sem calibração), estado de saturação/silêncio e
identificação do microfone. A interpretação deste novo tipo pela base fica
para uma etapa posterior. A seleção controla o aplicativo e não desliga
fisicamente os sensores da estação.

Se a captura do microfone for interrompida durante uma coleta que o inclui,
a sessão inteira é encerrada e os registros recebidos são preservados. Isso
inclui colocar o app em segundo plano ou bloquear o telefone. A conferência
aponta perda de sinal dos outros sensores durante a coleta.

## Decibelímetro do celular

Em **Sensores**, selecione **Som do celular**, toque em **Testar microfone**
ou abra o **Decibelímetro**. A permissão
de microfone é solicitada somente nessa ação. Esta ferramenta funciona sem o
ESP32 e sem a base, usando o microfone interno do Android.

- Mostra nível RMS a cada 100 ms, gráfico dos últimos 30 segundos, duração,
  mínimo, máximo e média energética da medição inteira. Mínimo/máximo são dos
  blocos RMS, não picos instantâneos. **Nova medição** zera essas estatísticas.
- Sem calibração, a unidade é **dBFS**, relativa à escala digital do microfone;
  valores negativos são normais. Não há conversão universal de dBFS para dB SPL.
  O piso numérico de −120 dBFS representa zero/potência muito baixa e não é
  apresentado como nível acústico calibrado.
- **Calibrar com referência** fica disponível após pelo menos 2 segundos de
  sinal válido, sem saturação. Compare sob som estável com uma referência de
  dB SPL sem ponderação A/C (ou referência tonal compatível), na mesma posição
  acústica, e informe a leitura. O ajuste é calculado a partir da média
  energética dos últimos 3 segundos e salvo somente para aquela rota, modo de
  captura e taxa de amostragem. A unidade passa a **dB SPL estimado**. Uma
  calibração por um ponto não corrige a resposta em frequência nem estabelece
  precisão ou certificação. Não há ponderação A/C ou classificação de risco.
- O processamento é local e o áudio PCM nunca é salvo nem enviado. Na
  conferência, os níveis ficam somente em memória; após iniciar uma coleta,
  os níveis são registrados no JSONL. A calibração persiste. Voltar do
  decibelímetro à aba Sensores mantém a captura compartilhada; bloquear o
  telefone ou colocar o app em segundo plano encerra o microfone.

A integração usa `AudioRecord`, mono PCM16 a 48 kHz (alternativa: 44,1 kHz),
remove a componente DC por bloco e calcula `10*log10(E[x²] - E[x]²)` com
amostras normalizadas pela escala de 16 bits. A média é ponderada pela duração
real dos blocos em energia linear. Usa `UNPROCESSED` quando o aparelho declara
suporte, com alternativa `VOICE_RECOGNITION`, conforme a
[orientação oficial do Android](https://developer.android.com/media/platform/mediarecorder).
Uma mudança de microfone durante a medição interrompe a captura; acessórios
externos não são usados silenciosamente com a calibração do microfone interno.

A validação automatizada cobre cálculo, calibração, silêncio/saturação,
permissões, cancelamento de início, mudança de rota e layout. A confirmação de
permissão real, resposta acústica e precisão exige teste no aparelho físico e
comparação com uma referência.

## Comandos úteis

O comando `skylog-studio` também abre o projeto. Os comandos `flutter`, `dart` e `adb` estão disponíveis em `/home/pedro-henrique/.local/bin`; os caminhos completos abaixo funcionam mesmo se um terminal antigo ainda não tiver atualizado seu ambiente.

Execute no terminal com a pasta `app` aberta:

```bash
cd /home/pedro-henrique/Documentos/projetos/estacao-metereologica/app
/home/pedro-henrique/sdk/flutter/bin/flutter doctor -v
/home/pedro-henrique/sdk/flutter/bin/flutter pub get
/home/pedro-henrique/sdk/flutter/bin/flutter devices
/home/pedro-henrique/sdk/flutter/bin/flutter run -t lib/main.dart
```

Para verificar o código e os testes existentes:

```bash
/home/pedro-henrique/sdk/flutter/bin/flutter analyze
/home/pedro-henrique/sdk/flutter/bin/flutter test
```

Para gerar um APK de desenvolvimento:

```bash
/home/pedro-henrique/sdk/flutter/bin/flutter build apk --debug
```

O APK fica em `build/app/outputs/flutter-apk/app-debug.apk` e serve para instalação local durante o desenvolvimento. O projeto usa assinatura de desenvolvimento também na configuração `release`; publicar na loja exige preparar a assinatura de distribuição.
