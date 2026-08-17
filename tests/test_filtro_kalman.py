import math
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from filtro_kalman import (  # noqa: E402
    DetectorParado,
    DetectorPassos,
    EstimadorRumo,
    FiltroCV,
    dif_angular,
)


class DetectorParadoTest(unittest.TestCase):
    def test_histerese_combina_acelerometro_e_giroscopio(self):
        detector = DetectorParado(janela=10)

        for i in range(35):
            ruido = 0.004 * math.sin(i)
            detector.amostra(0.0, 0.0, 1.0 + ruido,
                              0.1, 0.1, 0.1, dt=0.1)
        self.assertTrue(detector.parado)

        # Um giro inequivoco deve tirar do repouso imediatamente, mesmo sem
        # grande aceleracao translacional.
        detector.amostra(0.0, 0.0, 1.0, 0.0, 0.0, 20.0, dt=0.1)
        self.assertFalse(detector.parado)

    def test_gps_tem_histerese_contra_zero_isolado(self):
        detector = DetectorParado()
        for t in range(5):
            detector.atualizar_gps(float(t), float(t), 0.0, 4.0)
        self.assertTrue(detector.gps_movendo)

        detector.atualizar_gps(5.0, 5.0, 0.0, 0.0)
        self.assertTrue(detector.gps_movendo)

        for t in range(6, 9):
            detector.atualizar_gps(float(t), 5.0, 0.0, 0.0)
        self.assertFalse(detector.gps_movendo)

    def test_gps_parado_veta_ruido_do_acelerometro(self):
        detector = DetectorParado(janela=10)
        for t in range(4):
            detector.atualizar_gps(float(t), 0.0, 0.0, 0.0)
        self.assertTrue(detector.gps_parado)

        # Vibracao forte, mas sem giro, passos coerentes ou deslocamento GPS.
        for i in range(35):
            az = 1.0 + 0.12 * math.sin(i)
            detector.amostra(0.0, 0.0, az, 0.1, 0.1, 0.1, dt=0.1)
        self.assertTrue(detector.parado)

        # Um unico pico falso tambem nao deve religar o PDR.
        detector.amostra(0.0, 0.0, 1.1, 0.1, 0.1, 0.1, dt=0.1,
                          passo_detectado=True, passos_ativos=False)
        self.assertTrue(detector.parado)


class DetectorPassosTest(unittest.TestCase):
    def test_exige_sequencia_e_expira_cadencia(self):
        passos = DetectorPassos(taxa_hz=10.0)
        eventos = 0
        for i in range(100):
            t = i / 10.0
            az = 1.0 + 0.08 * math.sin(2 * math.pi * 1.5 * t)
            eventos += passos.amostra(0.0, 0.0, az, 0.1)

        self.assertGreaterEqual(eventos, 3)
        self.assertTrue(passos.andando())
        self.assertGreater(passos.cadencia_hz, 0.0)

        for _ in range(25):
            passos.amostra(0.0, 0.0, 1.0, 0.1)
        self.assertFalse(passos.andando())
        self.assertEqual(passos.cadencia_hz, 0.0)

        # O primeiro pico depois da pausa nao pode reutilizar os intervalos
        # antigos. Sao necessarios tres passos novos para confirmar caminhada.
        detectou = False
        for i in range(20):
            t = i / 10.0
            az = 1.0 + 0.08 * math.sin(2 * math.pi * 1.5 * t)
            if passos.amostra(0.0, 0.0, az, 0.1):
                detectou = True
                break
        self.assertTrue(detectou)
        self.assertFalse(passos.andando())


class FiltroCVTest(unittest.TestCase):
    def test_zupt_antes_da_propagacao_e_pdr_bloqueado(self):
        filtro = FiltroCV()
        filtro.iniciar(-21.0, -44.0)
        filtro.detector.parado = True
        filtro.parado = True
        filtro.x[2] = 4.0
        filtro.x[3] = -3.0

        filtro.prever(0.0, 0.0, 1.0, 0.0, 0.0, 0.1)
        self.assertEqual(filtro.x[2], 0.0)
        self.assertEqual(filtro.x[3], 0.0)
        self.assertAlmostEqual(filtro.x[0], 0.0)
        self.assertAlmostEqual(filtro.x[1], 0.0)

        aplicado = filtro.corrigir_pdr(1.6, 0.65, 90.0, 10.0)
        self.assertFalse(aplicado)
        self.assertEqual(filtro.velocidade_kmh, 0.0)


class EstimadorRumoTest(unittest.TestCase):
    def test_subtrai_bias_e_congela_integral_em_reposo(self):
        rumo = EstimadorRumo()
        rumo.definir_vies_gz(0.5)
        rumo.fixar_rumo(90.0)

        for _ in range(1000):
            rumo.passo(0.5, 0.1, parado=False)
        self.assertAlmostEqual(dif_angular(rumo.rumo, 90.0), 0.0, places=6)

        antes = rumo.rumo
        for _ in range(100):
            rumo.passo(1.5, 0.1, parado=True)
        self.assertAlmostEqual(dif_angular(rumo.rumo, antes), 0.0, places=6)
        self.assertGreater(rumo.vies_gz, 0.5)

    def test_ancora_rejeita_curva_e_corrige_trecho_reto(self):
        reto = EstimadorRumo()
        reto.fixar_rumo(20.0)
        for t in range(11):
            reto.ancorar_por_deslocamento(float(t), float(t), 0.0, movendo=True)
        self.assertGreater(reto.ancoragens, 0)
        self.assertLess(abs(dif_angular(reto.rumo, 0.0)), 20.0)

        curva = EstimadorRumo()
        curva.fixar_rumo(20.0)
        for t in range(11):
            if t:
                curva.passo(-5.0, 1.0, parado=False)
            curva.ancorar_por_deslocamento(float(t), float(t), 0.0, movendo=True)
        self.assertEqual(curva.ancoragens, 0)


if __name__ == "__main__":
    unittest.main()
