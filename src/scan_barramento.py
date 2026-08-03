#!/usr/bin/env python3
"""
Passo 1 do checklist: descobrir onde o sensor esta.

Varre todas as portas seriais, todos os baud rates comuns e os enderecos
Modbus 0x50..0x5F ate achar o WTGAHRS3. Rode isso ANTES de qualquer outra coisa.

    python3 src/scan_barramento.py
"""

import sys

from wtgahrs3 import BAUDS_COMUNS, WTGAHRS3, listar_portas, procurar_sensor


def main() -> int:
    portas = listar_portas()

    print("=" * 62)
    print("Portas seriais encontradas no sistema")
    print("=" * 62)
    if not portas:
        print("NENHUMA porta serial encontrada.\n")
        print("Verifique:")
        print("  1. O adaptador RS485-USB esta plugado?")
        print("  2. Rode `dmesg | tail -20` logo depois de plugar - deve aparecer")
        print("     algo como 'ch341-uart converter now attached to ttyUSB0'.")
        print("  3. Se aparece a porta mas o Python nao ve, e permissao:")
        print("     sudo usermod -aG dialout $USER   (e faca logout/login)")
        return 1

    for p in portas:
        print(f"  {p}")

    print()
    print("=" * 62)
    print("Procurando o WTGAHRS3 (isso leva ate ~1 min)")
    print("=" * 62)

    sensor = procurar_sensor(
        portas=portas,
        bauds=BAUDS_COMUNS,
        enderecos=range(0x50, 0x60),
        verbose=True,
    )

    if sensor is None:
        print()
        print("Sensor NAO encontrado. Checklist de diagnostico:")
        print("  - Alimentacao: o LED do sensor esta aceso?")
        print("  - A e B invertidos? Troque os dois fios (nao danifica nada).")
        print("  - GND do sensor ligado ao GND do adaptador? (obrigatorio)")
        print("  - Permissao: `ls -l /dev/ttyUSB*` e confira o grupo dialout.")
        print("  - Outro programa segurando a porta? Feche-o e tente de novo.")
        return 1

    print()
    print("=" * 62)
    print("SENSOR ENCONTRADO")
    print("=" * 62)
    print(f"  Porta:    {sensor.porta}")
    print(f"  Baudrate: {sensor.baudrate}")
    print(f"  Endereco: 0x{sensor.endereco:02X}")
    print()
    print("Anote esses tres valores - use nos outros scripts, por exemplo:")
    print(
        f"  python3 src/ler_posicao.py --porta {sensor.porta} "
        f"--baud {sensor.baudrate} --endereco 0x{sensor.endereco:02X}"
    )
    print()

    try:
        amb = sensor.ler_ambiente()
        print(f"  Temperatura do chip: {amb.temperatura_c:.2f} C")
        print(f"  Pressao:             {amb.pressao_pa} Pa")
        pos = sensor.ler_posicao()
        print(f"  Satelites em uso:    {pos.satelites}")
        if pos.tem_fix:
            print(f"  Fix GPS: SIM -> {pos.latitude:.6f}, {pos.longitude:.6f}")
        else:
            print("  Fix GPS: ainda nao. Leve a antena para fora / perto da janela")
            print("           e espere 1-15 min no primeiro fix (cold start).")
    except Exception as e:
        print(f"  (leitura de teste falhou: {e})")

    sensor.fechar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
