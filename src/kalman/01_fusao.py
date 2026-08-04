#!/usr/bin/env python3
"""
KALMAN - LICAO 1: fundir duas medidas

    python3 01_fusao.py        # roda os testes e te diz o que falta

O coracao do filtro de Kalman e responder a esta pergunta:

    Tenho duas medidas da mesma grandeza, com incertezas diferentes.
    Qual a melhor estimativa combinada?

A resposta e ponderar pelo INVERSO DA VARIANCIA. Quanto menor a incerteza de
uma medida, maior o peso dela.

Notacao:
    x1, x2      as duas medidas
    var1, var2  as VARIANCIAS (desvio padrao ao quadrado)

Por que variancia e nao desvio padrao: variancias se somam de forma simples
quando voce combina fontes independentes de erro; desvios padrao nao. E a
mesma razao pela qual, em estatistica, voce soma variancias e nao desvios.

--------------------------------------------------------------------------
SUA TAREFA: implementar as duas funcoes abaixo marcadas com TODO.
Rode o arquivo para ver se acertou.
--------------------------------------------------------------------------
"""

import math


def fundir(x1: float, var1: float, x2: float, var2: float) -> tuple:
    """
    Funde duas medidas independentes da mesma grandeza.

    Retorna (estimativa, variancia_resultante).

    DICA - a formula da estimativa:

                  x1/var1  +  x2/var2
        x_final = ---------------------
                   1/var1  +  1/var2

    DICA - a formula da variancia resultante:

           1         1        1
        -------- = ------ + ------
        var_final   var1     var2

    Repare que 1/variancia aparece nas duas. Esse termo tem nome: PRECISAO.
    O filtro de Kalman e, no fundo, "somar precisoes".
    """
    # TODO: implemente aqui
    raise NotImplementedError("implemente fundir()")


def ganho_kalman(var_estimativa: float, var_medida: float) -> float:
    """
    O "ganho de Kalman" K - a forma como o filtro costuma ser escrito.

    Em vez da media ponderada, o Kalman escreve a atualizacao assim:

        x_novo   = x_estimativa + K * (medida - x_estimativa)
        var_novo = (1 - K) * var_estimativa

    onde K vai de 0 a 1 e diz o quanto confiar na MEDIDA NOVA:

        K = 0  -> ignora a medida, mantem a estimativa
        K = 1  -> descarta a estimativa, aceita a medida inteira

    DICA:
                    var_estimativa
        K = --------------------------------
             var_estimativa  +  var_medida

    Note a intuicao: se a medida for muito ruidosa (var_medida grande),
    K fica perto de 0 e o filtro quase a ignora. Se a estimativa estiver
    muito incerta (var_estimativa grande), K vai para perto de 1 e o filtro
    se agarra na medida nova.
    """
    # TODO: implemente aqui
    raise NotImplementedError("implemente ganho_kalman()")


# ---------------------------------------------------------------------------
# Testes. Nao mexa daqui para baixo - e o seu gabarito.
# ---------------------------------------------------------------------------
def _quase(a, b, tol=1e-9):
    return abs(a - b) < tol


def testar():
    passou = True

    def check(nome, cond, detalhe=""):
        nonlocal passou
        print(f"  {'OK  ' if cond else 'FALHOU'}  {nome}")
        if not cond:
            passou = False
            if detalhe:
                print(f"          {detalhe}")

    print("\n" + "=" * 66)
    print("  TESTE 1  fundir() - o caso do enunciado")
    print("=" * 66)
    # GPS: 0,4 km/h com desvio 0,5    ->  variancia 0,25
    # IMU: 0,1 km/h com desvio 0,1    ->  variancia 0,01
    x, var = fundir(0.4, 0.5**2, 0.1, 0.1**2)
    print(f"  GPS  0,400 km/h  (desvio 0,5)")
    print(f"  IMU  0,100 km/h  (desvio 0,1)")
    print(f"  ->   {x:.4f} km/h  (desvio {math.sqrt(var):.4f})")
    check("estimativa correta", _quase(x, 0.4 * (1/0.25) / (1/0.25 + 1/0.01)
                                       + 0.1 * (1/0.01) / (1/0.25 + 1/0.01), 1e-9))
    check("puxou para o IMU (mais confiavel)", x < 0.25,
          f"esperado abaixo de 0,25 (a media simples); veio {x:.4f}")
    check("incerteza final menor que a melhor entrada", math.sqrt(var) < 0.1,
          f"desvio final {math.sqrt(var):.4f} deveria ser < 0,1")

    print("\n" + "=" * 66)
    print("  TESTE 2  fundir() - propriedades gerais")
    print("=" * 66)
    x, var = fundir(10.0, 1.0, 20.0, 1.0)
    check("incertezas iguais -> media simples", _quase(x, 15.0),
          f"esperado 15,0; veio {x}")
    check("variancia cai pela metade", _quase(var, 0.5),
          f"esperado 0,5; veio {var}")

    x, var = fundir(10.0, 1e-9, 20.0, 1e9)
    check("medida pessima e quase ignorada", abs(x - 10.0) < 1e-6,
          f"esperado ~10,0; veio {x}")

    x1, v1 = fundir(3.0, 2.0, 7.0, 5.0)
    x2, v2 = fundir(7.0, 5.0, 3.0, 2.0)
    check("a ordem nao importa", _quase(x1, x2) and _quase(v1, v2))

    print("\n" + "=" * 66)
    print("  TESTE 3  ganho_kalman()")
    print("=" * 66)
    check("K=0,5 quando as incertezas sao iguais", _quase(ganho_kalman(1.0, 1.0), 0.5))
    check("K->0 quando a medida e pessima", ganho_kalman(1.0, 1e9) < 1e-6)
    check("K->1 quando a estimativa e pessima", ganho_kalman(1e9, 1.0) > 0.999999)

    print("\n" + "=" * 66)
    print("  TESTE 4  as duas formas sao a MESMA coisa")
    print("=" * 66)
    print("  Esta e a parte importante da licao: media ponderada e ganho de")
    print("  Kalman sao algebricamente identicos, so escritos diferente.\n")
    for xe, ve, xm, vm in [(0.4, 0.25, 0.1, 0.01), (10, 3, 12, 1), (-5, 0.2, -4.5, 0.8)]:
        xa, va = fundir(xe, ve, xm, vm)
        K = ganho_kalman(ve, vm)
        xb = xe + K * (xm - xe)
        vb = (1 - K) * ve
        print(f"    media ponderada: x={xa:9.5f} var={va:9.5f}")
        print(f"    ganho de Kalman: x={xb:9.5f} var={vb:9.5f}  (K={K:.4f})")
        check("  as duas formas batem", _quase(xa, xb, 1e-9) and _quase(va, vb, 1e-9))
        print()

    print("=" * 66)
    if passou:
        print("  TUDO CERTO. Voce implementou o nucleo do filtro de Kalman.")
        print()
        print("  O que falta para virar um filtro de verdade: ate aqui voce so")
        print("  combina duas medidas do MESMO instante. Um filtro precisa")
        print("  tambem PREVER como a grandeza evolui entre uma medida e outra,")
        print("  e aumentar a incerteza enquanto nao chega medida nova.")
        print("  Isso e a licao 2.")
    else:
        print("  Ainda falta algo. Releia as dicas nas docstrings.")
    print("=" * 66)
    return passou


if __name__ == "__main__":
    try:
        testar()
    except NotImplementedError as e:
        print(f"\n  {e}")
        print("  Abra o arquivo e implemente as funcoes marcadas com TODO.\n")
