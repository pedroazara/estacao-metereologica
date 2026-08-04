# Filtro de Kalman — teoria, derivação e aplicação neste projeto

Documento de referência do filtro implementado em `src/filtro_kalman.py`.
As lições numeradas em `src/kalman/` reproduzem cada etapa de forma executável.

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
datasheets: **o WTGAHRS3 não tem magnetômetro** (ver `README.md`).

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
q = self.q * (1.0 + self.ganho_acc * self.acc_horizontal)
```

Parado ou em velocidade constante, o filtro **aperta** e suaviza agressivamente.
Em manobra, **afrouxa** e passa a seguir mais o GPS. É uma forma honesta de
usar o IMU sem precisar de uma orientação que não temos.

### O rumo do GPS como medida de velocidade

O *course over ground* e a velocidade do GPS, decompostos em Norte e Leste,
entram como medidas do estado de velocidade — mas só acima de 1,5 km/h. Parado,
o rumo do GPS é ruído puro e aplicá-lo injetaria lixo no filtro.

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

## 6. Resultados medidos

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
