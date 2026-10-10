import 'dart:async';
import 'dart:io';

import 'package:estacao_movel/modelo/sensores.dart';
import 'package:flutter_test/flutter_test.dart';
// Interface já usada pelo path_provider; evita qualquer chamada à plataforma.
// ignore: depend_on_referenced_packages
import 'package:path_provider_platform_interface/path_provider_platform_interface.dart';

class _Repositorio extends RepositorioSelecaoSensores {
  Set<SensorId> dados = {};
  final salvos = <Set<SensorId>>[];
  bool falharLeitura = false, falharEscrita = false;
  Future<void> Function()? esperar;

  @override
  Future<Set<SensorId>> ler() async {
    if (falharLeitura) throw const FileSystemException('leitura indisponível');
    return Set.of(dados);
  }

  @override
  Future<void> salvar(Set<SensorId> sensores) async {
    salvos.add(Set.of(sensores));
    await esperar?.call();
    if (falharEscrita) throw const FileSystemException('disco indisponível');
    dados = Set.of(sensores);
  }
}

class _Pasta extends PathProviderPlatform {
  final String caminho;
  _Pasta(this.caminho);
  @override
  Future<String?> getApplicationDocumentsPath() async => caminho;
}

void main() {
  test('começa vazia e expõe uma seleção imutável', () async {
    final selecao = SelecaoSensores(repositorio: _Repositorio());
    addTearDown(selecao.dispose);
    expect(selecao.carregando, isTrue);
    await selecao.pronto;
    expect(selecao.carregando, isFalse);
    expect(selecao.selecionados, isEmpty);
    expect(
      () => selecao.selecionados.add(SensorId.gpsCelular),
      throwsUnsupportedError,
    );
  });

  test('serializa mudanças concorrentes sem perder escolhas', () async {
    final bloqueios = [Completer<void>(), Completer<void>()];
    var indice = 0;
    final repo = _Repositorio()..esperar = () => bloqueios[indice++].future;
    final selecao = SelecaoSensores(repositorio: repo);
    addTearDown(selecao.dispose);
    await selecao.pronto;

    final primeiro = selecao.definir(SensorId.gpsCelular, true);
    final segundo = selecao.definir(SensorId.somCelular, true);
    await Future<void>.delayed(Duration.zero);
    expect(repo.salvos, [
      {SensorId.gpsCelular},
    ]);
    expect(selecao.salvando, isTrue);
    bloqueios[0].complete();
    await primeiro;
    await Future<void>.delayed(Duration.zero);
    expect(repo.salvos.last, {SensorId.gpsCelular, SensorId.somCelular});
    expect(selecao.salvando, isTrue);
    bloqueios[1].complete();
    await segundo;
    expect(repo.dados, {SensorId.gpsCelular, SensorId.somCelular});
    expect(selecao.salvando, isFalse);
  });

  test(
    'falha de persistência mantém escolha e permite repetir para salvar',
    () async {
      final repo = _Repositorio()..falharEscrita = true;
      final selecao = SelecaoSensores(repositorio: repo);
      addTearDown(selecao.dispose);
      await selecao.pronto;
      await selecao.definir(SensorId.ambienteRs485, true);
      expect(selecao.selecionados, {SensorId.ambienteRs485});
      expect(selecao.erro, contains('não foi salva'));
      expect(selecao.salvando, isFalse);
      expect(repo.dados, isEmpty);

      repo.falharEscrita = false;
      await selecao.definir(SensorId.ambienteRs485, true);
      expect(selecao.erro, isNull);
      expect(repo.dados, {SensorId.ambienteRs485});
    },
  );

  test(
    'bloqueio impede mudanças novas e operações que ainda estavam na fila',
    () async {
      final trava = Completer<void>();
      final repo = _Repositorio()..esperar = () => trava.future;
      final selecao = SelecaoSensores(repositorio: repo);
      addTearDown(selecao.dispose);
      await selecao.pronto;
      final primeira = selecao.definir(SensorId.gpsCelular, true);
      final segunda = selecao.definir(SensorId.somCelular, true);
      await Future<void>.delayed(Duration.zero);
      selecao.bloquear(true);
      await selecao.definir(SensorId.gpsCelular, false);
      trava.complete();
      await Future.wait([primeira, segunda]);
      expect(selecao.selecionados, {SensorId.gpsCelular});
      expect(repo.salvos.length, 1);
      selecao.bloquear(false);
      await selecao.definir(SensorId.gpsCelular, false);
      expect(selecao.selecionados, isEmpty);
    },
  );

  test('erro de leitura é informado e permite uma escolha nova', () async {
    final repo = _Repositorio()..falharLeitura = true;
    final selecao = SelecaoSensores(repositorio: repo);
    addTearDown(selecao.dispose);
    await selecao.pronto;
    expect(selecao.erro, contains('carregar'));
    expect(selecao.carregando, isFalse);
    await selecao.definir(SensorId.luminosidadeGy302, true);
    expect(selecao.erro, isNull);
    expect(repo.dados, {SensorId.luminosidadeGy302});
  });

  test('arquivo persiste seleção e recupera em uma nova instância', () async {
    final pasta = await Directory.systemTemp.createTemp('skylog-selecao-');
    final plataformaAnterior = PathProviderPlatform.instance;
    PathProviderPlatform.instance = _Pasta(pasta.path);
    addTearDown(() async {
      PathProviderPlatform.instance = plataformaAnterior;
      await pasta.delete(recursive: true);
    });
    final selecao = SelecaoSensores();
    await selecao.pronto;
    expect(selecao.selecionados, isEmpty);
    await selecao.definir(SensorId.temperaturaKy013, true);
    await selecao.definir(SensorId.somCelular, true);
    selecao.dispose();

    final recuperada = SelecaoSensores();
    addTearDown(recuperada.dispose);
    await recuperada.pronto;
    expect(recuperada.selecionados, {
      SensorId.temperaturaKy013,
      SensorId.somCelular,
    });
    final arquivo = File('${pasta.path}/selecao_sensores.json');
    await arquivo.writeAsString('{"version":1,"sensores":["inventado"]}');
    expect(ArquivoSelecaoSensores().ler(), throwsFormatException);
  });
}
