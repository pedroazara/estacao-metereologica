# Filtro de Kalman — teoria, derivação e aplicação neste projeto

> **Escopo histórico — arquitetura revisada em 09/10/2026.** As medições,
> calibrações e exemplos abaixo foram desenvolvidos para o WTGAHRS3 e as
> ferramentas Python existentes. O sensor saiu do hardware previsto: o
> celular é a fonte definida para GPS e nível sonoro. Movimento inercial e
> fusão com Kalman não integram o escopo da V1. A matemática continua como
> referência histórica e bancada de estudo.
> Ver [arquitetura atual](04-telemetria.md).

Documento de referência do filtro implementado em `src/filtro_kalman.py`.
As lições numeradas e os testes antigos foram removidos na limpeza de
10/10/2026. As referências abaixo descrevem o estudo histórico, não arquivos
executáveis disponíveis na árvore atual.

---

## O problema

O GPS e o acelerômetro erram de formas **opostas e complementares**.

| | GPS | Acelerômetro |
|---|---|---|
| Erro acumula com o tempo? | não | **sim, sem limite** |
| Precisão instantânea | ±2,5 m (CEP50) | altíssima |
| Taxa | 1–5 Hz | até 200 Hz |
| Funciona sob obstrução? | não | sim |

O acelerômetro percebe qualquer movimento imediatamente, mas para virar posição
ele precisa ser integrado **duas vezes** — e cada integração amplifica o erro.
Um viés de 0,03 m/s², dentro da especificação de qualquer MEMS barato, produz:

```
erro de velocidade = 0,03 × t
erro de posição    = 0,03 × t² / 2
```

Em 100 segundos são 10,8 km/h de erro de velocidade e 150 m de posição. Medido
na simulação da lição 2: **10,58 km/h**. Navegação inercial pura exige sensores
de dezenas de milhares de dólares justamente por isso.

O GPS não tem esse problema — o erro dele fica sempre na mesma faixa — mas é
ruidoso e lento.

O filtro de Kalman combina os dois de forma **ótima**, no sentido preciso de
minimizar a variância do erro.

---

## 1. O núcleo: fundir duas medidas

Toda a matemática do filtro sai de uma pergunta:

> Tenho duas medidas da mesma grandeza, com incertezas diferentes. Qual a
> melhor estimativa combinada?

### Derivação

Escreva a combinação com um peso qualquer `w`:

$$\hat{x} = w\,x_1 + (1-w)\,x_2$$

Como as duas medidas são não-viesadas, **qualquer** `w` dá uma estimativa
não-viesada. O que muda é a incerteza. Sendo os erros independentes:

$$\operatorname{Var}(\hat{x}) = w^2\sigma_1^2 + (1-w)^2\sigma_2^2$$

Minimizando em `w`:

$$\frac{d\operatorname{Var}}{dw} = 2w\sigma_1^2 - 2(1-w)\sigma_2^2 = 0
\quad\Rightarrow\quad w = \frac{\sigma_2^2}{\sigma_1^2 + \sigma_2^2}$$

A segunda derivada é `2(σ₁² + σ₂²) > 0`, então é mínimo. Substituindo e
dividindo por `σ₁²σ₂²`:

$$\boxed{\hat{x} = \frac{x_1/\sigma_1^2 + x_2/\sigma_2^2}{1/\sigma_1^2 + 1/\sigma_2^2}}
\qquad
\boxed{\frac{1}{\sigma^2} = \frac{1}{\sigma_1^2} + \frac{1}{\sigma_2^2}}$$

O peso é o **inverso da variância**, grandeza que tem nome: precisão. E
precisões se **somam** — por isso o resultado é sempre mais preciso que
qualquer entrada. Você está somando informação, e informação não diminui ao
acrescentar uma medida.

### A forma do ganho de Kalman

Renomeando — `x` = estimativa com variância `P`, `z` = medida com variância `R`:

$$\hat{x} = \frac{R\,x + P\,z}{P+R}
= x\left(1 - \frac{P}{P+R}\right) + z\,\frac{P}{P+R}$$

Definindo $K = \dfrac{P}{P+R}$:

$$\boxed{\hat{x} = x + K\,(z - x)} \qquad \boxed{P_{\text{novo}} = (1-K)\,P}$$

É a forma canônica. As duas escritas são **algebricamente idênticas** — a
lição 1 verifica isso numericamente.

O termo `(z − x)` chama-se **inovação**: o quanto a medida surpreendeu em
relação ao esperado. O filtro corrige por uma fração `K` da surpresa.

| K | significado |
|---|---|
| 0 | ignora a medida, mantém a estimativa |
| 0,5 | confia igualmente nas duas |
| 1 | descarta a estimativa, aceita a medida |

`K` **não é escolhido** — sai da razão entre as incertezas. É essa a diferença
essencial entre Kalman e uma média móvel, onde você chuta um peso fixo.

---

## 2. Predição: o que acontece entre duas medidas

Fundir só resolve o mesmo instante. Um filtro precisa também prever a evolução.
O ciclo completo alterna dois passos:

**Prever** — a cada amostra do IMU:

$$x \leftarrow F x + B u \qquad P \leftarrow F P F^{T} + Q$$

**Corrigir** — a cada leitura do GPS:

$$y = z - Hx \qquad S = HPH^{T} + R \qquad K = PH^{T}S^{-1}$$
$$x \leftarrow x + Ky \qquad P \leftarrow (I - KH)P$$

### A assimetria é o ponto principal

**Prever aumenta a incerteza. Corrigir diminui.**

Entre duas leituras de GPS o filtro vai ficando menos confiante, e quando o GPS
chega ele já está "com fome" — `K` sobe sozinho e ele aceita bem a medida.
Se o GPS sumir por muito tempo, `P` cresce sem parar e o filtro **sabe que não
sabe**. Essa auto-avaliação sai de graça e é uma das coisas mais úteis do
método.

Medido na lição 2, entre dois GPS de 1 Hz:

| t | σ |
|---|---|
| 5,0 s | 0,428 ← GPS |
| 5,3 s | 0,577 |
| 5,6 s | 0,695 |
| 5,9 s | 0,796 |
| 6,0 s | 0,428 ← GPS |

---

## 3. Estado com várias variáveis

Com estado `x = [posição, velocidade]`, a incerteza vira matriz:

$$P = \begin{bmatrix} P_{pp} & P_{pv} \\ P_{vp} & P_{vv} \end{bmatrix}$$

O termo fora da diagonal, `P_pv`, é a **correlação entre os erros** — e é o
motivo de valer a pena juntar as variáveis num filtro só em vez de rodar dois
filtros 1D separados.

A posição foi construída integrando a velocidade. Se a velocidade estimada
estava alta demais, a posição está adiantada. Os erros andam juntos, e o filtro
aprende isso sozinho.

O ganho passa a ter duas componentes:

$$K = \begin{bmatrix} P_{pp}/S \\ P_{pv}/S \end{bmatrix}$$

A segunda é construída **inteiramente sobre a correlação**. Quando chega uma
medida de posição, ela corrige também a velocidade, na proporção de quanto os
erros estão ligados.

**Consequência demonstrada na lição 3:** medindo apenas posição, nunca
velocidade, o filtro estima a velocidade com erro RMS de 0,72 m/s contra 2,44
da integração pura. A correlação nasce em zero, sobe para 0,68 em um segundo e
estabiliza em 0,81.

### A inversão de matriz que assusta

A literatura escreve $K = PH^{T}(HPH^{T}+R)^{-1}$ e a inversão intimida. Mas
quando você mede **uma grandeza por vez**, `H` tem uma linha só e o termo
`(HPH' + R)` é um **escalar**. Vira divisão.

Se precisar aplicar várias medidas no mesmo instante, o truque padrão é
aplicá-las em **sequência**, uma correção escalar depois da outra. Dá
exatamente o mesmo resultado e evita inverter qualquer coisa. É o que
`filtro_kalman.py` faz — não há inversão de matriz em lugar nenhum.

---

## 4. As decisões de projeto neste sensor

### Estado

```
x = [ n, e, vn, ve ]
```

Deslocamento Norte/Leste em metros a partir do primeiro fix, e as velocidades.

### Graus não são metros

O filtro precisa de unidades lineares. A conversão para o plano tangente local:

```python
norte = (lat - lat0) * 111320
leste = (lon - lon0) * 111320 * cos(lat0)
```

O `cos(lat0)` é essencial. Um grau de longitude vale 111 km no equador, 103 km
em Lavras (21° S) e zero nos polos. Sem ele, todo deslocamento leste-oeste sai
**7% errado** por aqui.

Válido para áreas de até algumas dezenas de km. Para trajetos continentais seria
preciso um modelo elipsoidal.

### Por que a aceleração NÃO entra na predição

Esta é a decisão mais importante, e vem direto do que descobrimos nos
datasheets: **o WTGAHRS3 não tem magnetômetro** — agora confirmado
empiricamente com ímã, não só por leitura de datasheet
(`src/verificar_bussola.py`, ver `docs/02-protocolo-modbus-wtgahrs3.md`).

> **Esta regra já foi violada uma vez.** Uma versão intermediária do filtro
> (`FiltroINS`, 6 estados) girava a aceleração para Norte/Leste pelo yaw e a
> integrava na predição. O resultado foi exatamente o previsto aqui: o filtro
> "andava sozinho" e disparava dezenas de metros. Duas medidas explicam por quê:
> o yaw do firmware **andou 32° com o sensor parado na mesa** em 2 minutos, e a
> âncora do rumo era calculada **uma única vez** e nunca corrigida. Foi
> revertido para o modelo CV descrito abaixo.

O acelerômetro mede no referencial **do sensor** — eixos X, Y, Z do próprio
módulo. Para somar isso a uma velocidade Norte/Leste é preciso saber para onde
o sensor aponta em relação ao Norte geográfico. Essa informação não existe
neste hardware: o yaw do IMU é integração de giroscópio, com zero arbitrário e
deriva acumulada.

Sem rotação correta, injetar a aceleração na predição **piora** o resultado —
você estaria empurrando a estimativa numa direção errada com confiança alta.

A solução é o modelo de **velocidade constante** (CV): entre duas leituras de
GPS presume-se que a velocidade não muda, e toda aceleração real é tratada como
ruído de processo. É o modelo padrão para suavização de GPS.

### Onde o acelerômetro entra: modulando o Q

Ele não fica de fora. Quando o módulo da aceleração cresce, a premissa de
velocidade constante está sendo violada — e o filtro precisa saber disso:

```python
q = q_parado if parado else q_base * (1.0 + ganho_acc * acc_horizontal)
```

Parado ou em velocidade constante, o filtro **aperta** e suaviza agressivamente.
Em manobra, **afrouxa** e passa a seguir mais o GPS. É uma forma honesta de
usar o IMU sem precisar de uma orientação que não temos.

### Como o repouso é decidido

Uma única fonte não é suficiente. O estado usa histerese e combina quatro
evidências:

- desvio de `|a|` numa janela curta;
- RMS de `gx/gy/gz`;
- sequência coerente de passos, não um pico isolado;
- mediana da velocidade e deslocamento do GPS em alguns segundos.

São necessários 1,5 s de quietude para entrar em repouso, mas movimento forte
faz a saída ser imediata. Se o GPS confirmar que a velocidade está zerada e o
giroscópio estiver quieto, vibração elétrica ou mecânica do acelerômetro não
mantém falsamente o estado "andando".

Ao entrar em repouso, o ZUPT é aplicado **antes** da propagação, a velocidade é
zerada, o PDR é bloqueado e `Q` passa ao valor pequeno fixo `q_parado`. Assim a
velocidade anterior não desloca mais uma amostra e o ruído do acelerômetro não
abre a covariância enquanto o equipamento está imóvel.

### O rumo do GPS como medida de velocidade

O *course over ground* e a velocidade do GPS, decompostos em Norte e Leste,
entram como medidas do estado de velocidade — mas o rumo só é usado acima de
3 km/h. Parado, o rumo do GPS é ruído puro e aplicá-lo injetaria lixo no
filtro. Uma velocidade abaixo de 1 km/h só vira ZUPT quando a janela do GPS ou
o detector completo também indicam repouso.

### R adaptado pelo HDOP

O sensor reporta HDOP a cada fix, que é uma medida direta da qualidade da
geometria dos satélites. Usá-lo para escalar `R` é quase de graça:

```python
R = r_pos * hdop**2
```

Fix ruim entra com peso menor, automaticamente.

---

## 5. Ajuste dos parâmetros

| Parâmetro | O que é | Como obter |
|---|---|---|
| `r_pos` | variância da posição do GPS (m²) | do datasheet: 2,5 m CEP50 → σ≈3 m → R≈9 |
| `r_vel` | variância da velocidade (m/s)² | medir parado por alguns minutos |
| `q` | densidade de ruído de aceleração | empírico — é o botão de "quanto suavizar" |
| `ganho_acc` | quanto a aceleração amplia o q | 3,0 funciona bem; 0 desliga a adaptação |

**R pode ser medido.** Deixe o sensor parado, colete algumas centenas de
amostras e calcule a variância. É o jeito certo.

**Q é empírico.** Grande demais e o filtro quase não filtra; pequeno demais e
ele fica lento para reagir a manobras reais, "cortando curva". Comece em 0,6 e
ajuste olhando o rastro.

Um teste de sanidade útil: a inovação `(z − x)` deve ter desvio padrão próximo
de `√S`. Se as inovações forem sistematicamente maiores, o filtro está
confiante demais — aumente `Q`.

---

### O rumo geográfico, já que não há bússola

O rumo não sai do filtro de posição — ele é um problema separado, resolvido por
`EstimadorRumo` com um **filtro complementar**.

A primeira versão usava o yaw do firmware ancorado no COG instantâneo do GPS.
Uma caminhada real de 179 s (`data/caminhada-20260806-174706.csv`) mostrou que
**as duas pontas estavam erradas**:

| fonte descartada | por quê |
|---|---|
| yaw do firmware | erro com **p90 de 176°** — aponta para o lado oposto em 10% do tempo |
| COG instantâneo | mediana boa (0,3°), mas **p90 de 16° por época** a 3-4 km/h |

O caso mais eloquente: em 2 s de caminhada quase reta, o COG acusou **149° de
curva** enquanto o giroscópio media 2,9°. A 1 m/s o deslocamento por época
(~0,2 m) é uma fração do ruído de posição (~2,5 m), então o COG é quase só
ruído — e ancorar nele é ancorar em lixo.

**O que substituiu:**

- **Curto prazo: o `gz` cru, integrado e NEGADO.** O giro é positivo no sentido
  anti-horário e o azimute cresce no horário. Medido em 604 viradas reais:
  **81% com `gz` de sinal oposto** à mudança de rumo. Sem inverter, o rumo gira
  para o lado errado. O `gz` cru tem p90 de 34° contra os 176° do yaw.
- **Longo prazo: a direção do deslocamento sobre uma base de ~8 s**, não o COG
  instantâneo. A 1 m/s, 8 s valem ~8 m — bem acima do ruído do GPS. É a mesma
  informação do COG, integrada por tempo suficiente para o ruído sair.

O viés de `gz` medido nos 8 s da fase parada é subtraído de todas as amostras.
Em repouso, o rumo deixa de ser integrado e esse viés é atualizado lentamente.
Em movimento, a âncora usa exclusivamente posições GPS originais — nunca a
posição do próprio Kalman, que pode conter PDR — e rejeita bases em que o
giroscópio indica uma curva maior que 25°. Isso remove a realimentação circular
`rumo → PDR → posição filtrada → rumo`.

**A incerteza é medida, não prometida.** A v1 calculava a incerteza por uma
fórmula de decaimento e relatava 3,5° quando o erro real era 21° — ou seja,
mentia. Agora ela é a média móvel do resíduo observado nas ancoragens: o quanto
giro e GPS de fato discordam.

**Limite honesto, exposto na interface:** enquanto nunca houve ancoragem, não
existe rumo geográfico — parado e sem nunca ter andado, a direção do Norte é
fisicamente desconhecível neste hardware. O painel mostra isso em vez de fingir
um número.

### O acelerômetro na posição: contar passos, não integrar

Integrar aceleração duas vezes faz o erro crescer com **t²** — foi o que
destruiu o `FiltroINS`. Contar passos faz o erro crescer com a **distância
percorrida**, e a poucos por cento: cada passo é um evento discreto, erra-se o
comprimento, nunca a existência.

Os dados da caminhada mostram que o sinal do passo é inequívoco:

| | valor medido |
|---|---|
| oscilação de \|a\| parado | 0,0003 g |
| oscilação de \|a\| andando | **0,0656 g** (200× maior) |
| cadência | 1,50 passos/s, desvio de 0,106 s |
| comprimento do passo | 0,53–0,70 m |

O `DetectorPassos` remove a gravidade por média móvel de ~0,8 s — não por
roll/pitch, porque para a oscilação não importa convenção de eixo nenhuma — e
detecta picos com limiar e tempo refratário.

O PDR só é liberado depois de pelo menos três passos com intervalos coerentes,
é vetado durante ZUPT e sua cadência expira após 2 s sem passos. Portanto, um
esbarrão isolado não reutiliza a última velocidade de caminhada.

A entrada no filtro **não é deslocamento**, é uma **medida de velocidade**:

```
velocidade = cadência (Hz) × comprimento do passo (m)
```

Assim ela reaproveita o caminho de correção já testado, e o R reflete a
incerteza real: o erro perpendicular ao rumo vale `v·sen(σ_rumo)`, muito maior
que o longitudinal quando σ_rumo é de 21°.

**Resultado com o GPS cortado aos 120 s** (túnel, obstrução):

| tempo sem GPS | 5 s | 20 s | 50 s |
|---|---|---|---|
| só CV | 4,1 m | 14,0 m | **33,2 m** |
| com PDR | 1,1 m | 4,7 m | **10,1 m** |

Com o GPS presente o PDR desvia um pouco mais dos fixes crus (0,36 → 0,85 m),
o que é esperado: o CV sozinho suaviza demais, e o PDR devolve o movimento real.

### O procedimento de partida (`CalibracaoInicial`)

Quatro fases, com instruções na tela:

1. **Satélites** — espera GNSS travar (≥6 satélites, HDOP ≤2,5, sustentado 3 s)
2. **Parado** — 8 s imóvel: viés do giro e posição média; o viés passa a ser
   efetivamente subtraído de `gz`
3. **Reto** — andar 15 m em linha reta
4. **Pronto**

**A fase 3 não é opcional.** Sem magnetômetro o giroscópio mede variação de
ângulo, nunca o ângulo absoluto — alguém precisa dizer uma vez "você está
apontado para cá", e a única fonte é o deslocamento do GPS. Andar reto torna
esse deslocamento inequívoco: em linha reta o rumo do deslocamento **é** o rumo
do corpo, o que não vale numa curva.

O mesmo trecho dá o comprimento do passo de graça — distância dividida por
passos contados. Ele varia de 0,4 a 0,8 m entre pessoas, então chutar custa erro
proporcional à distância.

A retidão é medida em **janela deslizante**, não desde o início da fase: a
primeira caminhada gravada fechou um circuito e nunca calibraria (retidão 0,35)
se bastasse uma curva para condenar o trecho inteiro. Basta que os últimos
metros tenham sido retos.

### Convenções de sinal (`SINAL_ROLL`, `SINAL_GZ`)

O firmware não segue a mesma regra de mão em todos os eixos, e a documentação
não fecha com o que o hardware entrega. Os sinais vivem em duas constantes no
topo de `filtro_kalman.py`, e `src/verificar_eixos.py` confere ambas contra o
hardware.

Um sinal trocado não é detalhe: no nivelamento, inclinar o sensor passa a
injetar gravidade nos eixos horizontais **para o lado errado** — o dobro do erro
de simplesmente ignorar a inclinação.

### A precisão que realmente dá para ganhar

`MediaParado` promedia os fixes enquanto o ZUPT indica repouso. A parcela
aleatória do erro do GPS cai com √N — 30 fixes a reduzem ~5,5×. A parcela
sistemática (ionosfera, multicaminho) **não cai**, e é por isso que o ganho
satura perto de 1 m sem correção diferencial.

O acelerômetro não ajuda aqui: parado, ele só acrescenta a deriva medida de
1,26 m em 30 s. Ficar parado e promediar é estritamente melhor.

## 6. Resultados medidos

### Com o sensor parado, IMU real + GPS sintético

120 s de IMU gravado com o sensor imóvel (18,2 Hz), GPS sintético de 1 Hz com
ruído gaussiano de 2,5 m:

| | erro |
|---|---|
| GPS bruto (médio) | 3,05 m |
| FiltroCV (médio) | **0,42 m** |
| FiltroCV (pico) | 1,16 m |
| melhoria | 86,3 % |
| média de 101 fixes em repouso | 0,55 m |

Para comparação, o `FiltroINS` que isso substituiu acumulava **113,8 m** de
deslocamento fantasma nos mesmos 120 s parado.

> ⚠️ Os 86,3 % são otimistas: o ruído sintético é gaussiano e independente,
> enquanto o erro real do GPS é correlacionado no tempo. Em campo espere menos.

### Caminhada real, 179 s

`data/caminhada-20260806-174706.csv` — 2142 amostras a 12 Hz, 972 fixes, com
paradas no início e no fim e retorno ao ponto de partida.

| | resultado |
|---|---|
| comprimento somando fixes crus | 120,5 m |
| comprimento somando saída do filtro | **99,7 m** |
| fechamento (início vs fim, fixes crus) | 6,12 m |
| fechamento (início vs fim, filtro) | 6,31 m |
| erro de rumo, método novo | mediana **21,1°**, p90 **72,2°** |
| erro de rumo, método anterior | mediana 29,9°, p90 **176,5°** |

O ganho maior está na **cauda**: o p90 do rumo caiu de 176° para 72°. A mediana
de 21° ainda não é boa — a pé, com este hardware, é o que há.

> ⚠️ A referência usada para medir o erro de rumo é a própria direção de
> deslocamento de base longa, calculada do mesmo GPS que alimenta a âncora.
> A comparação é parcialmente circular e não substitui uma referência externa.

### Fixes corrompidos: 5,9 % da caminhada

Alguns fixes vinham **257 km fora**, com 17-18 satélites e HDOP 2,09 — ou seja,
saudáveis por qualquer métrica de qualidade. O bloco de registradores **não é
atualizado atomicamente**: lat/lon vêm de uma época transitória enquanto `sat`
e `HDOP` ainda seguram os valores bons da anterior.

**Consequência: não dá para validar um fix pelo `sat`/`HDOP` lidos junto com
ele.** A validação tem que ser espacial. O portão de inovação do `FiltroCV` dá
conta — rodando a caminhada inteira sem nenhum pré-filtro, ele rejeitou 45
fixes e o maior afastamento foi de 24,5 m.

### Simulação controlada

Simulação controlada, 180 s, ruído de GPS de 3 m (`04_no_gps_real.py`):

| | erro RMS |
|---|---|
| GPS bruto | 4,11 m |
| Kalman | **1,94 m** |
| melhoria | 52,8 % |

Caminhada simulada no painel, 25 s a 1,4 m/s com ruído de 3 m:

| | distância |
|---|---|
| real | ~35 m |
| somando pontos brutos | 247 m (7× inflada) |
| somando pontos filtrados | **61,8 m** |

O efeito mais visível num trajeto real é esse: **somando distâncias entre
leituras cruas, cada oscilação do GPS vira "movimento" e infla o total.** Não é
que o filtro encurte o trajeto — é que o valor bruto estava errado.

### Sobre avaliar com dados gravados

O `data/trajeto.csv` do projeto **não serve** para medir o ganho do filtro:

- mistura sessões separadas por até 20 horas, tratadas como trajeto contínuo
- contém trechos gerados com `--simular`
- os pontos já passaram pelo filtro de deslocamento mínimo de 3 m da gravação,
  então o jitter que o Kalman removeria já tinha sido descartado antes

Para avaliar de verdade, grave com `--min-dist 0` numa sessão única.

---

## 7. Limitações

**Transiente inicial.** O filtro começa sem saber nada, com incerteza enorme, e
leva alguns segundos para convergir. Na lição 3 ele chuta −4,18 m/s no primeiro
segundo. Normal, mas os primeiros pontos de qualquer trajeto devem ser vistos
com desconfiança.

**Linearidade.** O Kalman clássico pressupõe modelo linear e ruído gaussiano.
O modelo CV é linear, então estamos dentro da hipótese. Se um dia o estado
incluir orientação, o modelo vira não-linear e será preciso um EKF ou UKF.

**Não recupera de fixes muito ruins.** O filtro suaviza ruído aleatório, não
erro sistemático. Se o GPS entregar uma posição consistentemente deslocada —
como aconteceu no bug de codificação documentado em `02-protocolo`— o filtro vai
segui-la com confiança. Por isso o painel mantém a guarda de salto de 500 m
como camada independente.

**Não substitui um bom fix.** Com HDOP alto e poucos satélites, o filtro reduz o
ruído mas não cria informação que não existe.

---

## Arquivos

| Arquivo | Conteúdo |
|---|---|
| `src/filtro_kalman.py` | implementação usada em produção |
| `src/kalman/01_fusao.py` | fundir duas medidas (com exercício) |
| `src/kalman/02_predicao.py` | ciclo prever/corrigir, filtro 1D completo |
| `src/kalman/03_matricial.py` | estado 2D e a correlação |
| `src/kalman/04_no_gps_real.py` | aplicação no GPS, replay de trajetos |

## Referências

- Kalman, R. E. (1960). *A New Approach to Linear Filtering and Prediction
  Problems*. Journal of Basic Engineering, 82(1), 35–45.
- Welch, G. & Bishop, G. *An Introduction to the Kalman Filter*. UNC-Chapel Hill.
- Simon, D. (2006). *Optimal State Estimation*. Wiley.
