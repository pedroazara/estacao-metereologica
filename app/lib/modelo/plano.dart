/// Uma pincelada da area pintada no painel da base: raio do pincel (a
/// largura da pintura, nao o espacamento de nada - ver planejamento.py no
/// lado da base) e os pontos por onde o cursor passou.
class Traco {
  final double raioM;
  final List<(double, double)> pontos; // (lat, lon)

  Traco({required this.raioM, required this.pontos});

  factory Traco.fromJson(Map<String, dynamic> j) => Traco(
        raioM: (j['raio_m'] as num).toDouble(),
        pontos: (j['pontos'] as List)
            .map((p) => ((p[0] as num).toDouble(), (p[1] as num).toDouble()))
            .toList(),
      );
}

/// Resumo de um plano - o que a lista de planos mostra, sem a area inteira.
class PlanoResumo {
  final int id;
  final String nome;
  final String dev;
  final String criadoEm;
  final bool ativo;

  PlanoResumo({
    required this.id,
    required this.nome,
    required this.dev,
    required this.criadoEm,
    required this.ativo,
  });

  factory PlanoResumo.fromJson(Map<String, dynamic> j) => PlanoResumo(
        id: j['id'] as int,
        nome: j['nome'] as String? ?? '',
        dev: j['dev'] as String? ?? '',
        criadoEm: j['criado_em'] as String? ?? '',
        ativo: j['ativo'] == true,
      );
}

/// Um plano com a area pintada inteira - o que da pra desenhar no mapa.
class PlanoDetalhe {
  final int? id;
  final String nome;
  final List<Traco> tracos;
  final String criadoEm;
  final bool ativo;

  PlanoDetalhe({
    this.id,
    required this.nome,
    required this.tracos,
    required this.criadoEm,
    this.ativo = false,
  });

  factory PlanoDetalhe.fromJson(Map<String, dynamic> j) => PlanoDetalhe(
        id: j['id'] as int?,
        nome: j['nome'] as String? ?? '',
        tracos: (j['tracos'] as List? ?? [])
            .map((t) => Traco.fromJson(t as Map<String, dynamic>))
            .toList(),
        criadoEm: j['criado_em'] as String? ?? '',
        ativo: j['ativo'] == true,
      );
}
