# Medindo a cidade quarteirão por quarteirão

*Como montei uma estação meteorológica móvel com ESP32, sensores baratos, um app em Flutter e um Raspberry Pi — e o que deu errado no caminho.*

<!-- IMAGEM DE CAPA: foto da estação em uso na rua, ou montada na bancada com o celular ao lado. -->
![A estação meteorológica móvel montada](img/capa-estacao.jpg)

Quando o jornal diz que está fazendo 28 °C na cidade, esse número vem de uma estação fixa, instalada num lugar escolhido justamente para não sofrer influência do entorno. Só que ninguém mora dentro de uma estação meteorológica. Quem anda a pé sabe que uma rua de asfalto sem nenhuma árvore e uma praça arborizada, a duzentos metros uma da outra, não têm a mesma temperatura. É a ilha de calor urbana, e ela acontece na escala do quarteirão.

Daí veio a ideia deste projeto pessoal: uma estação meteorológica **móvel**. Eu a carrego enquanto ando pela cidade, e ela registra temperatura, umidade, ruído e luminosidade junto com a posição de cada leitura. No fim, cada caminhada vira um mapa.

## Como funciona

São três partes, e cada uma faz uma coisa só:

```text
ESTAÇÃO (ESP32)         lê os sensores e transmite
      │
      │  Bluetooth LE
      ▼
CELULAR (app Flutter)   pega a posição do GPS do telefone e grava a coleta
      │
      │  HTTP, quando volto para a rede de casa
      ▼
BASE (Raspberry Pi)     guarda o arquivo original, processa e mostra o mapa
```

- **A estação** é um ESP32 com os sensores. Ela não interpreta nada: lê, numera cada leitura e transmite por Bluetooth. Gosto de chamá-la de *sensor honesto*.
- **O celular** roda um app que escrevi em Flutter. Ele recebe as leituras da estação, pega a posição pelo GPS do próprio telefone e grava tudo num arquivo enquanto eu ando.
- **A base** é um Raspberry Pi ligado na tomada. Quando chego em casa, o app envia a coleta; a base guarda o arquivo original, processa e mostra o trajeto num mapa colorido pela grandeza que eu escolher.

Uma regra atravessa as três partes: **o dado cru nunca é alterado nem descartado**. Todo processamento gera uma camada nova, que pode ser apagada e recalculada do zero a partir do arquivo original.

## Os sensores

A estação mistura um sensor industrial com módulos baratos de kit Arduino. O industrial é a referência; os baratos são calibrados.

| Sensor | O que mede | Como liga no ESP32 | Observação |
|---|---|---|---|
| Sensor ambiental RS485 (Modbus, endereço `0x01`) | temperatura e umidade | RS485, por um transceptor na UART2 | a referência da estação |
| KY-038 (microfone) | ruído, em dB | analógico, GPIO32, com divisor 10k/10k | calibrado contra um app medidor de decibéis |
| KY-028 (termistor NTC) | temperatura auxiliar | analógico, GPIO35 | calibrado contra o sensor RS485 |
| KY-013 (termistor NTC) | temperatura auxiliar | analógico, GPIO39 | calibrado contra o sensor RS485 |
| GY-302 (chip BH1750) | luminosidade, em lux | I²C (SDA 21, SCL 22), endereço `0x23` | já vem calibrado de fábrica |
| WitMotion WTGAHRS3-485 (Modbus, endereço `0x50`) | GPS + acelerômetro e giroscópio | mesmo barramento RS485 | o primeiro sensor do projeto; hoje fica de reserva |
| GPS do celular | posição | pelo app, uma leitura por segundo | a fonte de posição atual |

<!-- IMAGEM: foto das ligações — ESP32, transceptor RS485 e os módulos KY/GY na protoboard. -->
![Os sensores ligados ao ESP32](img/sensores.jpg)
*O ESP32 com o sensor RS485 e os módulos baratos.*

**Três temperaturas.** O sensor RS485 é a referência. Os dois termistores custam centavos, mas os valores "de fábrica" (resistor fixo, resistência nominal, coeficiente Beta) variam tanto entre clones que nenhuma das hipóteses batia com a referência. Em vez de confiar neles, ajustei direto dos dados, por mínimos quadrados, a forma da equação de um NTC num divisor de tensão: `1/T = a·ln((1−x)/x) + b`, com `x` sendo a leitura do ADC normalizada. O app mostra as três temperaturas lado a lado, com o mesmo peso: por enquanto nenhuma delas é "a" temperatura oficial.

**Ruído.** O KY-038 só entrega um sinal analógico. Para calibrar, deixei o monitor serial e um app medidor de decibéis lado a lado e anotei pares de leituras em vários níveis de som — silêncio, conversa, palmas. Como decibel é logarítmico, o ajuste é `dB = a·log₁₀(amplitude) + b`, e saiu `dB = 26,475·log₁₀(amplitude) + 21,926`. O firmware mede a amplitude em janelas de 20 ms e acumula o **Leq**, o nível equivalente: média de potência, não média de decibéis. O módulo é alimentado em 5 V para ter ganho, e o divisor protege o ADC do ESP32, que não aguenta mais que 3,3 V.

**O que ainda falta: pressão.** O plano original era medir temperatura, umidade e pressão. A capa do datasheet do WTGAHRS3 fala em barômetro e magnetômetro, mas o próprio documento diz que o chip interno é um JY61P, de 6 eixos — só acelerômetro e giroscópio —, e na bancada o registrador de pressão devolve 0 Pa. Ou seja: a pressão ainda não é medida, e um BME280 está na lista de compras.

## A estação: um ESP32 que só lê e transmite

O firmware é escrito na Arduino IDE, com drivers Modbus próprios, sem biblioteca: o quadro e o CRC16 são montados na mão. Duas histórias dessa parte valem contar.

**Dois sensores no mesmo par de fios.** O RS485 permite vários dispositivos no mesmo barramento, cada um com seu endereço. Quando pus o WTGAHRS3 e o sensor ambiental juntos, o GPS continuou perfeito e o ambiental passou a responder só de vez em quando. Eram duas causas somadas. O Modbus RTU separa mensagens por um silêncio de 3,5 caracteres (3,65 ms a 9600 baud), e com o WTGAHRS3 sendo lido dez vezes por segundo o pedido ao ambiental saía colado numa resposta do outro sensor. Além disso, o driver aceitava como início de resposta qualquer byte que estivesse na linha — inclusive o resto atrasado de uma resposta do GPS.

A pista veio de um lugar inesperado: o intervalo entre os lotes do acelerômetro alternava entre 0,0985 s e 0,1229 s, num ciclo de exatamente 2 segundos — o intervalo de leitura do termômetro. Cada tentativa falha de ler o termômetro esperava os 200 ms inteiros de timeout e engolia duas amostras do acelerômetro.

A solução final foi um escalonador: o tempo é fatiado em janelas de 100 ms, num ciclo de 2 s, e cada janela tem um dono fixo. Uma é do sensor ambiental, duas são do GPS, o resto é do acelerômetro. Ninguém disputa o barramento.

**Bluetooth e o tamanho da mensagem.** A estação expõe um serviço BLE próprio, com uma característica para cada tipo de dado (ambiente, status, configuração). Cada leitura do ambiente vai como um JSON pequeno:

```json
{"seq": 412, "temp": 27.8, "umid": 40.2, "db": 63.5, "t28": 28.3, "t13": 27.4, "lux": 41250.0}
```

Um detalhe fácil de perder: o pacote padrão do BLE tem 23 bytes, 20 úteis. Sem pedir um MTU maior logo depois de conectar (o app pede 517), essas mensagens chegariam cortadas no meio do JSON, a leitura falharia em toda amostra — e o app pareceria funcionar, porque conecta normalmente.

## O app: o celular como coletor

Minha primeira ideia era não ter app nenhum: o ESP32 já servia uma página web de conferência pelo Wi-Fi, e eu pegaria a posição do celular pelo navegador. Não funciona: `navigator.geolocation` exige HTTPS, e o ESP32 serve HTTP puro. Certificado autoassinado no ESP32 e PWA esbarram no mesmo problema. Isso, somado à vontade de usar Bluetooth, me levou a um app nativo — em Flutter, Android primeiro.

O app tem cinco abas: **Painel**, **Mapa**, **Planos**, **Estação** e **Sessões**.

O **Painel** responde às perguntas de quem está na rua: estou gravando? o GPS pegou posição? a estação está conectada? Ele mostra as três temperaturas, a umidade, o ruído e a luz, a posição com a precisão informada pelo GPS e o botão de gravar. Só o que acontece entre "Gravar" e "Parar" entra na coleta.

<!-- IMAGEM: print do app, aba Painel, de preferência durante uma gravação. -->
![Aba Painel do app durante uma gravação](img/app-painel.png)
*Aba Painel: as três temperaturas, umidade, ruído, luz e a posição do GPS do telefone.*

O **Mapa** usa o OpenStreetMap, mostra onde estou e desenha a trajetória da gravação em andamento.

<!-- IMAGEM: print do app, aba Mapa, com uma trajetória desenhada. -->
![Aba Mapa do app com a trajetória de uma coleta](img/app-mapa.png)
*Aba Mapa: a trajetória da gravação sobre o OpenStreetMap.*

A coleta é gravada em **JSONL**, um JSON por linha, sempre anexando no fim do arquivo:

```json
{"src": "gps", "ts_app": "2026-09-20T14:30:13", "m": {"lat": -21.2424, "lon": -44.9991, "precisao": 4.2, "vel": 4.9, "rumo": 31.0}}
{"src": "amb", "ts_app": "2026-09-20T14:30:14", "m": {"seq": 1, "temp": 27.8, "umid": 40.2, "db": 63.5, "t28": 28.3, "t13": 27.4, "lux": 41250.0}}
```

O formato foi escolhido por um motivo prático: se o app morrer no meio da coleta, o arquivo continua válido até a última linha inteira. Um array JSON precisaria do `]` final, e uma sessão interrompida viraria um arquivo corrompido.

Dois bugs dessa parte me ensinaram bastante:

- **Coletas vazias.** As primeiras sessões se perderam. O app escrevia num buffer em memória que só ia para o disco ao parar a gravação; quem sincronizasse antes — ou fechasse o app — perdia tudo. Hoje o buffer vai para o disco a cada 2 segundos, então o pior caso é perder 2 segundos, não a sessão.
- **Trajetória em degraus.** A linha no mapa saía em degraus em vez de acompanhar a rua. O pacote de localização do Android usa, por padrão, um intervalo de 5 segundos entre leituras; andando a pé, isso dá um ponto a cada 7 metros. Bastou pedir 1 segundo explicitamente.

Um detalhe de uso: quando começo a gravar, o app para de procurar a base na rede. Em campo ela está fora de alcance mesmo, e isso economiza bateria. Parado, ele volta a verificar e avisa quando dá para sincronizar.

<!-- IMAGEM OPCIONAL: print da aba Sessões do app. Para usar, tire as duas linhas abaixo de dentro deste comentário.
![Aba Sessões: as coletas guardadas no celular e o envio para a base](img/app-sessoes.png)
*Aba Sessões: enviar para a base não apaga nada do celular.*
-->

## A base: um Raspberry Pi que vira banco de dados

Tecnicamente, meu notebook é melhor que um Raspberry Pi 3 em tudo. Mas o objetivo não é só rodar um script: é transformar o projeto num instrumento. Com a base, ninguém precisa lembrar de abrir terminal, ativar ambiente virtual e rodar programa. Ela fica ligada na tomada, sempre no mesmo endereço, acumulando histórico. E o código é o mesmo: desenvolvo no PC e implanto no Pi sem mudar uma linha.

A base é um servidor HTTP pequeno em Python, que sobe sozinho com o sistema. Quando o app envia uma coleta:

1. o arquivo original é gravado primeiro num temporário e só depois renomeado, para que uma queda no meio da escrita nunca deixe um arquivo pela metade passar por sessão completa;
2. se a mesma sessão já tinha chegado, o envio só é aceito se for maior — é o caso de ter sincronizado no meio da gravação e mandado de novo depois. Reenviar é sempre seguro;
3. a sessão é processada para um banco SQLite. Se o processamento falhar, o arquivo original já está salvo e pode ser reprocessado depois.

Aqui mora o meu bug favorito do projeto: as coletas chegavam na base **com 0 bytes**, apesar de o arquivo no celular estar cheio. O `HttpClient` do Dart, quando você não informa o tamanho do corpo, envia em *chunked transfer encoding* — em pedaços, sem o cabeçalho `Content-Length`. O servidor lia exatamente `Content-Length` bytes, que não existia. Resultado: zero. Corrigi dos dois lados: o app passou a informar o tamanho, e o servidor aprendeu a ler os pedaços.

O painel da base é uma página web servida pelo próprio Pi. À esquerda ficam as coletas recebidas; à direita, o trajeto no mapa, com cada ponto colorido pela grandeza escolhida: temperatura, umidade, ruído, as temperaturas auxiliares ou luminosidade.

![Painel da base: uma coleta ao redor da Praça Dr. Augusto Silva, colorida pela temperatura](img/painel-base-sessoes.png)
*O painel da base. Cada ponto é uma leitura, colorida pela temperatura. A coleta desta imagem é de demonstração (valores simulados), ao redor da Praça Dr. Augusto Silva, no centro de Lavras. A "incerteza" no rodapé é a precisão informada pelo GPS do celular.*

O mapa usa Leaflet e OpenStreetMap, e os tiles são baixados pelo navegador de quem abre o painel — o Pi só serve a página.

<!-- IMAGEM OPCIONAL: o mesmo painel com outra grandeza selecionada (ex.: ruído ou luminosidade). Para usar, tire as duas linhas abaixo de dentro deste comentário.
![Painel da base colorido pelo ruído](img/painel-base-ruido.png)
*A mesma coleta, agora colorida pelo ruído.*
-->

Uma lição de hardware: o Pi 3 é alimentado por microUSB e sofre com fonte fraca. Na minha base, o comando `vcgencmd get_throttled` devolve `0x50000` — o registro de que já houve subtensão e redução de clock, e isso volta a cada boot. Subtensão em Raspberry Pi causa travamentos intermitentes e cartão SD corrompido, e parece bug de software. A correção é uma fonte de 2,5 A de verdade com cabo curto e grosso, não código. É também por isso que o Pi fica na base e nunca vai a campo.

<!-- IMAGEM OPCIONAL: foto do Raspberry Pi da base. Para usar, tire a linha abaixo de dentro deste comentário.
![O Raspberry Pi 3 que funciona como base](img/raspberry-base.jpg)
-->

## Em construção: planejar a área antes de sair

A parte mais nova, ainda em teste e fora da base de verdade, é o planejamento. Na aba **Planejar área** do painel, eu pinto no mapa, com um pincel de tamanho ajustável, a região que quero cobrir — com borracha e desfazer. A área vira um plano, e cada estação tem um plano ativo. No app, a aba **Planos** lista os planos da estação conectada, e o mapa mostra a área pintada por baixo da trajetória: um guia de "é por aqui".

A primeira versão era mais ambiciosa e pior: gerava uma grade com centenas de pontos-alvo para visitar um por um. Ninguém sai de casa decorando 174 pontos. Hoje o plano guarda só a área pintada. Eu meço do jeito de sempre, e o cruzamento das medições com a área é um pós-processamento, calculado na hora em que alguém abre o plano.

<!-- IMAGEM: print da aba "Planejar área" do painel da base, com uma área pintada. -->
![Aba Planejar área: a região a medir pintada com o pincel](img/painel-base-planejar.png)
*Aba Planejar área: a região que quero cobrir, pintada à mão sobre o mapa.*

<!-- IMAGEM OPCIONAL: print da aba Planos do app. Para usar, tire a linha abaixo de dentro deste comentário.
![Aba Planos do app, com o plano ativo da estação](img/app-planos.png)
-->

## Decisões que mudaram no caminho

Olhando para trás, o projeto melhorou quase toda vez que eu tirei alguma coisa.

**O filtro de Kalman que eu tirei.** Boa parte do começo foi dedicada a um filtro de Kalman que fundia o GPS com o acelerômetro do WTGAHRS3: estado com posição e velocidade, modelo de velocidade constante, ruído do processo modulado pelo acelerômetro e ruído da medida adaptado pela qualidade do sinal de GPS. Numa simulação controlada, com ruído de 3 m no GPS, o erro RMS caiu de 4,11 m para 1,94 m — 52,8% de melhoria. Aprendi muito com ele. Mas, numa rodada de simplificação, quando a posição passou a vir do GPS do celular, o filtro saiu do caminho principal e ficou só no código de bancada.

Uma decisão ligada a ele continua de pé: o processamento mora na base, e não no ESP32. Não por falta de CPU — o ESP32 rodaria o filtro com folga —, mas porque filtro que roda ao vivo e descarta o dado cru é irreversível. Na base, a mesma coleta pode ser reprocessada quantas vezes eu quiser.

**O MQTT saiu.** Comecei com um broker MQTT no Pi e a estação publicando ao vivo. Quando tudo virou "despejar o arquivo quando chego em casa", um POST HTTP resolvia com menos peças. Sem o broker, sumiu junto uma categoria inteira de bugs: jitter da rede bagunçando o intervalo entre amostras, amostra chegando fora de ordem.

**O GPS do celular venceu.** O GPS do WTGAHRS3 continua lá, dormente. O do celular, com A-GPS, pega posição muito mais rápido e tem hora absoluta confiável.

**Datasheet não é prova.** Além do barômetro e do magnetômetro que não existem, a fórmula do manual do WTGAHRS3 para decodificar as coordenadas só funciona nos hemisférios norte e leste: o sensor aplica o sinal só na parte dos graus, e os minutos entram somados em vez de subtraídos. Aqui no Brasil, a fórmula oficial erra a posição em 136 km. Com a fórmula corrigida, a diferença cai para 36 m — o esperado entre a antena do sensor e o GPS do celular.

## O que vem agora

- **Cartão SD na estação.** Hoje, entre o campo e a base, a única cópia do dado cru é a do celular. O ESP32 precisa guardar a dele.
- **Pressão atmosférica**, com um BME280.
- **Fechar a calibração** dos sensores baratos e **comparar com a estação automática do INMET** em Lavras, para saber o tamanho do erro.
- **Levar o planejamento de área para a base de verdade** e para uma versão nova do app.
- **Tirar o banco do cartão SD do Pi**, para um pendrive ou SSD.
- **Uma placa própria** (comecei a desenhar no KiCad) e uma montagem definitiva, protegida da chuva.

## Ficha técnica

- **Estação:** ESP32 DevKit (WROOM-32), firmware em C++ na Arduino IDE, Modbus RTU sobre RS485, I²C e Bluetooth LE.
- **App:** Flutter/Dart, com `flutter_blue_plus` (Bluetooth), `geolocator` (GPS) e `flutter_map` (OpenStreetMap).
- **Base:** Raspberry Pi 3 Model B com Debian 13, servidor HTTP em Python (só biblioteca padrão), SQLite e `systemd`.
- **Painel:** HTML e JavaScript puro, com Leaflet e OpenStreetMap.

## Para fechar

A maior lição do projeto não foi técnica: foi aprender a separar papéis. A estação só mede, o celular só coleta, a base só processa e mostra, e o dado cru é sagrado. Cada vez que uma peça tentou fazer o trabalho da outra, apareceu bug.

Se você trabalha com clima urbano, sensoriamento ou só gosta de brincar com ESP32, me conta o que achou.

<!-- LINK: se o repositório for público, coloque o link aqui. -->
