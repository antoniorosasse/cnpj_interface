/* prox-mapa.js - mapa da pagina /proximidade/ (Leaflet + tiles do OpenStreetMap).

   Le os dados do json_script #prox-mapa-dados (o ponto de partida e as linhas
   da pagina, ja resolvidos na view) e, sob demanda, a densidade do raio em
   /proximidade/mapa/. Nao faz query por conta propria: sem dado, nao desenha.

   Por que agrupar: a coordenada da empresa e a do CEP, e um CEP concentra
   milhares de empresas no mesmo ponto (Joinville/10 km: mediana de 27 e maximo
   de 5.333 por CEP). Um marcador por empresa empilharia a pagina 1 inteira em
   1-2 pontos, entao cada bolha e um conjunto de empresas do mesmo CEP. */
(function () {
    'use strict';

    var el = document.getElementById('prox-mapa');
    var dadosEl = document.getElementById('prox-mapa-dados');
    if (!el || !dadosEl || typeof L === 'undefined') return;

    var dados;
    try {
        dados = JSON.parse(dadosEl.textContent);
    } catch (e) {
        return;
    }
    if (!dados || !dados.origem) return;

    var origem = [Number(dados.origem.lat), Number(dados.origem.lon)];
    if (!isFinite(origem[0]) || !isFinite(origem[1])) return;

    var status = document.getElementById('prox-mapa-status');
    var toggle = document.getElementById('prox-mapa-densidade');

    function esc(txt) {
        return String(txt == null ? '' : txt).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function fmt(n) {
        var v = Number(n);
        return isFinite(v) ? v.toLocaleString('pt-BR') : '0';
    }

    function km(n) {
        return (Number(n) || 0).toFixed(2).replace('.', ',') + ' km';
    }

    function cnpj(n) {
        var t = String(n || '');
        if (t.length !== 14) return t;
        return t.slice(0, 2) + '.' + t.slice(2, 5) + '.' + t.slice(5, 8) + '/' +
            t.slice(8, 12) + '-' + t.slice(12, 14);
    }

    function textoPrecisao(precisao) {
        if (precisao === 'exato') return 'endereço';
        if (precisao === 'rua') return 'média da rua';
        return 'centro do CEP';
    }

    function distanciaKm(lat1, lon1, lat2, lon2) {
        var rad = Math.PI / 180;
        var dLat = (lat2 - lat1) * rad;
        var dLon = (lon2 - lon1) * rad;
        var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * rad) * Math.cos(lat2 * rad) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
        return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    function aviso(txt) {
        if (status) status.textContent = txt || '';
    }

    // Fundo proprio: o BC250 do IBGE (limite de municipio, mancha urbana e
    // rodovia), gerado por scripts/gera_mapa_bc250.py em static/mapa/<camada>_<UF>.geojson.
    // Nao existe tile de terceiro: o servidor publico do OSM bloqueia rajadas
    // (403 "Access blocked" servido em HTTP 200) e a politica dele permite
    // retirar o acesso de servico comercial sem aviso.
    //
    // center/zoom no construtor: sem uma view o mapa fica "nao carregado" e os
    // layers so entram no onAdd do evento load - ai getBounds() do circulo
    // estoura (this._map undefined). Bug pego com screenshot em 24/11/2026.
    var mapa = L.map(el, {
        center: origem,
        zoom: 13,
        scrollWheelZoom: false,
        preferCanvas: true,
        attributionControl: false
    });
    mapa.attributionControl = L.control.attribution({ prefix: false }).addTo(mapa);
    mapa.attributionControl.addAttribution('Base: IBGE · BC250 2026 (1:250.000)');
    // Zoom com Ctrl (ou Cmd) + rolagem: a roda sozinha continua rolando a
    // pagina, que e o comportamento esperado num mapa embutido.
    el.addEventListener('wheel', function (evento) {
        if (!evento.ctrlKey && !evento.metaKey) return;
        evento.preventDefault();
        var passo = evento.deltaY > 0 ? -1 : 1;
        mapa.setZoomAround(mapa.mouseEventToLatLng(evento), mapa.getZoom() + passo);
    }, { passive: false });

    // Escala em metros/km no canto inferior: ajuda a ler o raio no zoom atual.
    L.control.scale({ metric: true, imperial: false, position: 'bottomleft' })
        .addTo(mapa);

    mapa.createPane('baseMapa');
    mapa.getPane('baseMapa').style.zIndex = 210;
    var rendererBase = L.svg({ pane: 'baseMapa' });
    var ESTILO_MUNICIPIO = { color: '#b3c1d1', weight: 1.2, fillColor: '#f2f5f9', fillOpacity: 1 };
    var ESTILO_URBANO = { color: '#cfd8e3', weight: 0.6, fillColor: '#dfe6ef', fillOpacity: 1 };
    var ESTILO_RODOVIA = { color: '#8fa0b4', weight: 1.3, opacity: 0.95 };
    // Mapa de rua (zoom de quadra): OpenFreeMap (vetorial, sem chave, uso
    // comercial permitido, sem limite). E o fundo principal; o BC250 entra
    // como fallback se o estilo nao carregar (rede/WebGL) - ver carregarRua.
    var ESTILO_RUA = 'https://tiles.openfreemap.org/styles/liberty';

    function camadaBase(nome, uf, estilo) {
        var url = '/static/mapa/' + nome + '_' + uf + '.geojson';
        return fetch(url, { credentials: 'same-origin' }).then(function (resposta) {
            if (!resposta.ok) throw new Error(url + ': ' + resposta.status);
            return resposta.json();
        }).then(function (dadosBase) {
            return L.geoJSON(dadosBase, {
                renderer: rendererBase,
                style: estilo,
                interactive: false
            }).addTo(mapa);
        });
    }

    // Rotulos de cidade: o BC250 nao tem nome de rua, mas tem a sede municipal
    // (lml_cidade_p). Aparecem a partir de zoom 10, senao viram uma nuvem de
    // texto no raio de 100 km.
    var camadaCidades = L.layerGroup();
    function camadaCidade(uf) {
        var url = '/static/mapa/cidades_' + uf + '.geojson';
        return fetch(url, { credentials: 'same-origin' }).then(function (resposta) {
            if (!resposta.ok) throw new Error(url + ': ' + resposta.status);
            return resposta.json();
        }).then(function (dadosCidades) {
            L.geoJSON(dadosCidades, {
                pointToLayer: function (feicao, latlng) {
                    var nome = (feicao.properties && feicao.properties.nome) || '';
                    return L.marker(latlng, {
                        interactive: false,
                        icon: L.divIcon({
                            className: 'prox-mapa__cidade',
                            html: esc(nome),
                            iconSize: null,
                            iconAnchor: [0, 0]
                        })
                    });
                }
            }).addTo(camadaCidades);
        });
    }

    function carregarBC250() {
        var ufs = dados.ufs || [];
        if (!ufs.length) {
            aviso('Base cartográfica indisponível para este ponto.');
            return;
        }
        var tarefas = [];
        ufs.forEach(function (uf) {
            tarefas.push(camadaBase('municipios', uf, ESTILO_MUNICIPIO));
            tarefas.push(camadaBase('urbano', uf, ESTILO_URBANO));
            tarefas.push(camadaBase('rodovias', uf, ESTILO_RODOVIA));
            tarefas.push(camadaCidade(uf));
        });
        Promise.all(tarefas).catch(function () {
            aviso('Base cartográfica indisponível agora; os pontos e o raio continuam na tela.');
        });
    }
    function carregarRua() {
        if (typeof L.maplibreGL !== 'function') return false;
        var camadaRua;
        try {
            camadaRua = L.maplibreGL({
                style: ESTILO_RUA,
                attribution: 'OpenFreeMap · OpenMapTiles · OpenStreetMap'
            });
            camadaRua.addTo(mapa);
        } catch (e) {
            return false;
        }
        var gl = null;
        try {
            gl = camadaRua.getMaplibreMap();
        } catch (e) {
            gl = null;
        }
        if (!gl) return false;
        // Se o estilo nao carregar em 8 s (rede bloqueada, WebGL sem contexto),
        // tira a camada e desenha o BC250 para nao ficar um retangulo vazio.
        window.setTimeout(function () {
            if (gl.isStyleLoaded()) return;
            try {
                mapa.removeLayer(camadaRua);
            } catch (e) { /* ja saiu */ }
            carregarBC250();
            aviso('Mapa de rua indisponível; usando a base do IBGE (BC250).');
        }, 8000);
        return true;
    }

    if (!carregarRua()) carregarBC250();

    // Aviso na tela de em que modo o mapa esta (areas x pontos de endereco).
    var elModo = document.getElementById('prox-mapa-modo');
    function atualizarModo() {
        if (!elModo) return;
        var z = mapa.getZoom();
        if (z >= ZOOM_PONTOS) {
            elModo.textContent = 'Pontos por endereço (zoom ' + z + ')';
            elModo.classList.add('prox-mapa__modo--ativo');
        } else {
            elModo.textContent = 'Áreas de densidade · aproxime até o zoom ' + ZOOM_PONTOS +
                ' para abrir';
            elModo.classList.remove('prox-mapa__modo--ativo');
        }
    }

    function alternarCidades() {
        var visivel = mapa.hasLayer(camadaCidades);
        if (mapa.getZoom() >= 10 && !visivel) camadaCidades.addTo(mapa);
        else if (mapa.getZoom() < 10 && visivel) mapa.removeLayer(camadaCidades);
    }
    mapa.on('zoomend', alternarCidades);
    mapa.on('zoomend', atualizarModo);
    window.setTimeout(alternarCidades, 400);
    atualizarModo();

    // Raio da busca e o ponto de partida (que tambem pode ser um CEP/cidade,
    // ou seja, aproximado).
    var raioKm = Number(dados.raio) || 1;
    var circulo = L.circle(origem, {
        radius: raioKm * 1000,
        color: '#0d6efd',
        weight: 1.5,
        opacity: 0.7,
        dashArray: '6 6',
        fillColor: '#0d6efd',
        fillOpacity: 0.04,
        interactive: false
    }).addTo(mapa);

    // Pino de verdade (divIcon com Bootstrap Icons, ja carregado na pagina) em
    // vez de circulo: as empresas da pagina quase sempre caem no mesmo CEP do
    // ponto de partida (medido: 1-2 coordenadas nas 20 linhas), e um circulo de
    // 8 px esconderia as bolhas embaixo dele.
    L.marker(origem, {
        icon: L.divIcon({
            className: 'prox-mapa__pin',
            html: '<i class="bi bi-geo-alt-fill" aria-hidden="true"></i>',
            iconSize: [28, 28],
            iconAnchor: [14, 26]
        }),
        title: 'Ponto de partida: ' + (dados.origem.rotulo || '')
    }).addTo(mapa).bindTooltip(
        'Ponto de partida: ' + esc(dados.origem.rotulo || ''),
        { direction: 'top', offset: [0, -22] }
    );

    // ---------------------------------------------------------- pagina atual
    var grupos = {};
    (dados.pontos || []).forEach(function (ponto) {
        var lat = Number(ponto.lat);
        var lon = Number(ponto.lon);
        if (!isFinite(lat) || !isFinite(lon)) return;
        var chave = lat.toFixed(5) + ',' + lon.toFixed(5);
        if (!grupos[chave]) {
            grupos[chave] = { lat: lat, lon: lon, km: Number(ponto.km) || 0, itens: [] };
        }
        grupos[chave].itens.push(ponto);
        grupos[chave].km = Math.min(grupos[chave].km, Number(ponto.km) || 0);
    });

    var camadaPagina = L.layerGroup();
    Object.keys(grupos).forEach(function (chave) {
        var grupo = grupos[chave];
        var n = grupo.itens.length;
        var raioBolha = Math.min(24, 7 + Math.log(n + 1) / Math.LN2 * 3);
        var titulo = grupo.itens[0].endereco || 'CEP sem logradouro';

        // Precisao do ponto: endereco (CNEFE), rua (media da rua no CEP) ou
        // centro do CEP. Muda o estilo da bolha e o texto do balao.
        var precisao = 'cep';
        grupo.itens.forEach(function (ponto) {
            if (ponto.precisao === 'exato') precisao = 'exato';
            else if (ponto.precisao === 'rua' && precisao !== 'exato') precisao = 'rua';
        });
        var rotuloPrecisao = precisao === 'exato' ? 'endereço'
            : (precisao === 'rua' ? 'rua' : 'centro do CEP');

        var itens = grupo.itens.slice(0, 8).map(function (ponto) {
            return '<li><a href="' + esc(ponto.url) + '" target="_blank" rel="noopener">' +
                esc(ponto.nome || ponto.razao || cnpj(ponto.cnpj)) + '</a>' +
                '<span class="prox-mapa__popup-meta">' + esc(cnpj(ponto.cnpj)) +
                ' · ' + km(ponto.km) + '</span></li>';
        }).join('');
        if (n > 8) itens += '<li class="prox-mapa__popup-mais">e mais ' + (n - 8) + '</li>';

        var conteudo = '<div class="prox-mapa__popup"><strong>' + esc(titulo) + '</strong>' +
            '<p>' + fmt(n) + (n > 1 ? ' empresas' : ' empresa') + ' · ' + km(grupo.km) +
            ' · ' + rotuloPrecisao + '</p><ul>' + itens + '</ul></div>';

        camadaPagina.addLayer(L.circleMarker([grupo.lat, grupo.lon], {
            radius: raioBolha,
            color: '#0d6efd',
            weight: 2,
            dashArray: precisao === 'cep' ? '4 3' : null,
            fillColor: '#0d6efd',
            fillOpacity: precisao === 'exato' ? 0.5 : (precisao === 'rua' ? 0.35 : 0.22)
        }).bindTooltip(
            fmt(n) + (n > 1 ? ' empresas' : ' empresa') + ' · ' + km(grupo.km) +
            ' · ' + rotuloPrecisao,
            { direction: 'top', offset: [0, -raioBolha] }
        ).bindPopup(conteudo));
    });
    camadaPagina.addTo(mapa);

    // -------------------------------------------------- densidade do raio
    var camadaDensidade = null;
    var densidadePedida = false;

    function desenharDensidade(payload) {
        var celulas = (payload && payload.celulas) || [];
        if (!celulas.length) {
            aviso('Sem densidade para mostrar neste raio.');
            return;
        }

        var max = 1;
        celulas.forEach(function (celula) {
            if (celula[2] > max) max = celula[2];
        });

        var grupo = L.layerGroup();
        var grao = Number(payload.grao) || 0.005;
        celulas.forEach(function (celula) {
            var n = Number(celula[2]) || 0;
            var forca = Math.sqrt(n / max);
            var marca = L.circleMarker([celula[0], celula[1]], {
                radius: 4 + forca * 20,
                stroke: true,
                color: '#ea580c',
                weight: 1,
                opacity: 0.4,
                fillColor: '#f97316',
                fillOpacity: 0.16 + forca * 0.5
            });
            marca.bindTooltip(fmt(n) + (n > 1 ? ' empresas' : ' empresa') +
                ' nesta área', { direction: 'top' });
            // Clicar na area abre a consulta de empresas naquele ponto (raio 1 km,
            // mantendo os filtros da tela). A area e uma celula da grade, nao um
            // CEP: o link usa as coordenadas do centro dela.
            var consulta = new URLSearchParams();
            consulta.set('origem', Number(celula[0]).toFixed(6) + ',' +
                         Number(celula[1]).toFixed(6));
            consulta.set('raio', '1');
            if (el.getAttribute('data-matrizes') === '1') consulta.set('matrizes', '1');
            var situacaoAtual = el.getAttribute('data-situacao');
            if (situacaoAtual) consulta.set('situacao', situacaoAtual);
            marca.bindPopup(
                '<div class="prox-mapa__popup"><strong>' + fmt(n) +
                (n > 1 ? ' empresas' : ' empresa') + '</strong>' +
                '<p>área da grade de ~' + fmt(Math.round(grao * 111320)) + ' m · ' +
                km(distanciaKm(origem[0], origem[1], celula[0], celula[1])) + '</p>' +
                '<a class="btn btn-sm btn-primary prox-mapa__popup-link"' +
                ' target="_blank" rel="noopener" href="/proximidade/?' +
                consulta.toString() + '">Abrir consulta aqui' +
                ' <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i></a></div>');
            grupo.addLayer(marca);
        });

        camadaDensidade = grupo;
        // No zoom de quadra os pontos individuais substituem a area.
        // 14 = ZOOM_PONTOS (literal aqui porque a densidade carrega antes)
        if ((!toggle || toggle.checked) && mapa.getZoom() < 14) grupo.addTo(mapa);

        var metros = Math.round((Number(payload.grao) || 0.005) * 111320);
        var texto = fmt(celulas.length) + ' área(s) com empresas na grade de ~' +
            fmt(metros) + ' m';
        if (payload.truncado) texto += ' (as mais densas)';
        aviso(texto);
    }

    function carregarDensidade() {
        if (camadaDensidade) {
            if (!toggle || toggle.checked) camadaDensidade.addTo(mapa);
            return;
        }
        if (densidadePedida) return;
        densidadePedida = true;

        var url = el.getAttribute('data-densidade-url');
        if (!url || !window.fetch || !window.URLSearchParams) {
            aviso('Densidade do raio indisponível neste navegador.');
            return;
        }

        var params = new URLSearchParams();
        params.set('origem', origem[0].toFixed(6) + ',' + origem[1].toFixed(6));
        params.set('raio', String(raioKm));
        if (el.getAttribute('data-matrizes') === '1') params.set('matrizes', '1');
        var situacao = el.getAttribute('data-situacao');
        if (situacao) params.set('situacao', situacao);

        fetch(url + '?' + params.toString(), {
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin'
        }).then(function (resposta) {
            return resposta.json();
        }).then(function (payload) {
            if (payload && payload.erro) {
                aviso(payload.erro === 'sem-tabela'
                    ? 'Densidade do raio indisponível nesta base; o mapa mostra só as empresas da página.'
                    : payload.erro);
                return;
            }
            desenharDensidade(payload);
        }).catch(function () {
            aviso('Não foi possível carregar a densidade do raio.');
        });
    }

    if (toggle) {
        toggle.addEventListener('change', function () {
            if (toggle.checked) {
                carregarDensidade();
            } else if (camadaDensidade) {
                mapa.removeLayer(camadaDensidade);
                aviso('');
            }
        });
    }

    // Enquadra o circulo do raio: com 1 km o zoom fica no bairro, com 100 km
    // pega a regiao inteira.
    mapa.fitBounds(circulo.getBounds(), { padding: [16, 16] });
    carregarDensidade();

    // Botoes "centralizar no mapa" da tabela: movem o mapa para o ponto da
    // empresa sem recarregar a pagina (nao gasta consulta).
    Array.prototype.forEach.call(document.querySelectorAll('.prox-centrar'), function (botao) {
        botao.addEventListener('click', function () {
            var lat = Number(botao.getAttribute('data-lat'));
            var lon = Number(botao.getAttribute('data-lon'));
            if (!isFinite(lat) || !isFinite(lon) || !lat || !lon) return;
            mapa.setView([lat, lon], Math.max(mapa.getZoom(), 16));
            if (el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
    });

    // ------------------------------------------------------ zoom de quadra
    // A densidade agrega a grade (uma area com 65 empresas). A partir do zoom
    // 14 o mapa troca as areas pelos pontos de cada empresa (endpoint
    // /proximidade/mapa/pontos/), consultando a AREA VISIVEL (nao o raio
    // inteiro): assim arrastar o mapa carrega as empresas do lugar novo.
    var ZOOM_PONTOS = 14;
    var camadaPontos = L.layerGroup();
    var itensPorGrupo = [];
    var pontosPedidos = false;
    var ultimaChavePontos = '';

    function consultaPontos() {
        // Raio que cobre a vista: do centro ate o canto (com 5% de folga).
        var centro = mapa.getCenter();
        var canto = mapa.getBounds().getNorthEast();
        var raioVista = Math.max(0.1,
            distanciaKm(centro.lat, centro.lng, canto.lat, canto.lng) * 1.05);
        var params = new URLSearchParams();
        params.set('origem', centro.lat.toFixed(5) + ',' + centro.lng.toFixed(5));
        params.set('raio', raioVista.toFixed(2));
        if (el.getAttribute('data-matrizes') === '1') params.set('matrizes', '1');
        var sit = el.getAttribute('data-situacao');
        if (sit) params.set('situacao', sit);
        params.set('limite', '300');
        return {
            chave: centro.lat.toFixed(3) + ',' + centro.lng.toFixed(3) + '|' + mapa.getZoom(),
            url: params.toString()
        };
    }

    function carregarPontosZoom() {
        if (mapa.getZoom() < ZOOM_PONTOS) {
            if (mapa.hasLayer(camadaPontos)) mapa.removeLayer(camadaPontos);
            camadaPontos.clearLayers();
            pontosPedidos = false;
            ultimaChavePontos = '';
            if (toggle && toggle.checked && camadaDensidade && !mapa.hasLayer(camadaDensidade)) {
                mapa.addLayer(camadaDensidade);
            }
            return;
        }
        if (!window.fetch || pontosPedidos) return;
        var consulta = consultaPontos();
        if (consulta.chave === ultimaChavePontos && camadaPontos.getLayers().length) {
            return;
        }
        ultimaChavePontos = consulta.chave;
        pontosPedidos = true;
        fetch('/proximidade/mapa/pontos/?' + consulta.url, {
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin'
        }).then(function (resposta) {
            return resposta.json();
        }).then(function (payload) {
            if (payload && payload.erro) {
                aviso(payload.erro);
                pontosPedidos = false;
                return;
            }
            camadaPontos.clearLayers();
            // Varias empresas podem cair na MESMA coordenada (endereco de rua =
            // media do logradouro, ou centro do CEP). Em vez de empilhar bolinhas,
            // agrupa e escreve quantas sao - so o 'exato' (CNEFE com numero) e
            // realmente um ponto de endereco individual.
            var gruposPontos = {};
            (payload.pontos || []).forEach(function (ponto) {
                var chave = Number(ponto.lat).toFixed(5) + ',' + Number(ponto.lon).toFixed(5);
                if (!gruposPontos[chave]) {
                    gruposPontos[chave] = { lat: ponto.lat, lon: ponto.lon, itens: [] };
                }
                gruposPontos[chave].itens.push(ponto);
            });
            itensPorGrupo = [];
            Object.keys(gruposPontos).forEach(function (chave) {
                var grupo = gruposPontos[chave];
                var n = grupo.itens.length;

                // Consulta que reproduz este ponto na /proximidade/ (para o
                // painel oferecer "abrir na pagina", que conta consulta nova).
                var consulta = new URLSearchParams();
                consulta.set('origem', Number(grupo.lat).toFixed(6) + ',' +
                             Number(grupo.lon).toFixed(6));
                consulta.set('raio', '1');
                if (el.getAttribute('data-matrizes') === '1') consulta.set('matrizes', '1');
                var sitGrupo = el.getAttribute('data-situacao');
                if (sitGrupo) consulta.set('situacao', sitGrupo);
                grupo.consulta = consulta.toString();

                var indiceGrupo = itensPorGrupo.length;
                itensPorGrupo.push(grupo);

                var temExato = grupo.itens.some(function (item) {
                    return item.precisao === 'exato';
                });
                var raio = n > 1 ? Math.min(20, 6 + Math.log(n + 1) / Math.LN2 * 2.2)
                    : (temExato ? 5 : 4);

                // Lista curta (4) para o balao nao cobrir o mapa; a lista completa
                // abre no painel flutuante (sem consulta nova).
                var LIMITE_BALAO = 4;
                var itens = grupo.itens.slice(0, LIMITE_BALAO).map(function (item) {
                    // Sem km por linha: todas estao no mesmo ponto.
                    return '<li><a href="' + esc(item.url) + '" target="_blank" rel="noopener">' +
                        esc(item.nome) + '</a><span class="prox-mapa__popup-meta">' +
                        esc(cnpj(item.cnpj)) + '</span></li>';
                }).join('');
                if (n > LIMITE_BALAO) {
                    itens += '<li class="prox-mapa__popup-mais">e mais ' +
                        (n - LIMITE_BALAO) + '</li>';
                }
                var conteudo = n === 1
                    ? '<p>' + esc(cnpj(grupo.itens[0].cnpj)) + ' · ' + km(grupo.itens[0].km) +
                      ' · ' + textoPrecisao(grupo.itens[0].precisao) + '</p>' +
                      '<a class="prox-mapa__popup-link btn btn-sm btn-primary" href="' +
                      esc(grupo.itens[0].url) + '" target="_blank" rel="noopener">Abrir ficha' +
                      ' <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i></a>'
                    : '<button type="button" class="prox-mapa__popup-link btn btn-sm btn-primary"' +
                      ' data-grupo="' + indiceGrupo + '">Ver as ' + fmt(n) + ' empresas</button>' +
                      '<ul class="prox-mapa__popup-lista">' + itens + '</ul>';

                var marca = L.circleMarker([grupo.lat, grupo.lon], {
                    radius: raio,
                    color: temExato ? '#1d4ed8' : '#64748b',
                    weight: 1.2,
                    fillColor: temExato ? '#3b82f6' : '#94a3b8',
                    fillOpacity: temExato ? 0.9 : 0.6
                });
                marca.bindTooltip(n > 1 ? fmt(n) + ' empresas'
                    : esc(grupo.itens[0].nome), { direction: 'top' });
                var tituloBalao = n === 1 ? esc(grupo.itens[0].nome) : '';
                marca.bindPopup('<div class="prox-mapa__popup' +
                    (n > 1 ? ' prox-mapa__popup--grupo' : '') + '">' +
                    (tituloBalao ? '<strong>' + tituloBalao + '</strong>' : '') +
                    conteudo + '</div>', { maxHeight: 240 });
                camadaPontos.addLayer(marca);
            });
            camadaPontos.addTo(mapa);
            if (camadaDensidade) mapa.removeLayer(camadaDensidade);
            var totalPontos = (payload.pontos || []).length;
            aviso(fmt(totalPontos) + ' empresas na área visível (até 300)' +
                (totalPontos >= 300 ? ' - aproxime para ver todas' : '') +
                '. Arraste para ver outras.');
        }).catch(function () {
            pontosPedidos = false;
            aviso('Não foi possível carregar os pontos individuais agora.');
        });
    }

    // Painel flutuante com TODAS as empresas do grupo. Os dados ja vieram na
    // consulta da area visivel: abrir a lista nao conta consulta nova.
    function abrirPainelGrupo(indice) {
        var grupo = itensPorGrupo[indice];
        var painel = document.getElementById('prox-painel');
        if (!grupo || !painel) return;
        var n = grupo.itens.length;
        var contagem = document.getElementById('prox-painel-contagem');
        var filtro = document.getElementById('prox-painel-filtro');
        var lista = document.getElementById('prox-painel-lista');
        var link = document.getElementById('prox-painel-link');
        // Nao mexa no texto do titulo: ele contem o <span> da contagem.
        if (contagem) {
            contagem.textContent = '(' + fmt(n) + (n > 1 ? ' em ' : ' em ') +
                km(grupo.itens[0].km) + ')';
        }
        // Sem km/precisao em cada linha: todas estao no MESMO ponto.
        var linhas = grupo.itens.map(function (item) {
            return '<li><a href="' + esc(item.url) + '" target="_blank" rel="noopener">' +
                esc(item.nome) + '</a><span class="prox-mapa__popup-meta">' +
                esc(cnpj(item.cnpj)) + '</span></li>';
        }).join('');
        lista.innerHTML = linhas;
        // Filtro simples quando a lista e grande (56 nomes nao caem na tela).
        if (filtro) {
            filtro.hidden = n <= 12;
            filtro.value = '';
            filtro.oninput = function () {
                var termo = filtro.value.trim().toLowerCase();
                Array.prototype.forEach.call(lista.children, function (li) {
                    var texto = li.textContent.toLowerCase();
                    li.hidden = termo !== '' && texto.indexOf(termo) === -1;
                });
            };
        }
        if (link) link.href = '/proximidade/?' + (grupo.consulta || '');
        mapa.closePopup();
        painel.hidden = false;
        if (filtro && !filtro.hidden) filtro.focus();
    }

    (function ligarFecharPainel() {
        var painel = document.getElementById('prox-painel');
        var fechar = document.getElementById('prox-painel-fechar');
        if (!painel || !fechar) return;
        fechar.addEventListener('click', function () { painel.hidden = true; });
        document.addEventListener('keydown', function (evento) {
            if (evento.key === 'Escape') painel.hidden = true;
        });
    }());

    el.addEventListener('click', function (evento) {
        var alvo = evento.target;
        var botao = alvo && alvo.closest ? alvo.closest('[data-grupo]') : null;
        if (!botao) return;
        evento.preventDefault();
        abrirPainelGrupo(Number(botao.getAttribute('data-grupo')));
    });

    var timerPontos = null;
    mapa.on('moveend', function () {
        window.clearTimeout(timerPontos);
        timerPontos = window.setTimeout(carregarPontosZoom, 300);
    });
    carregarPontosZoom();

    // O card nasce com altura 0 se o CSS ainda nao aplicou (fonte/layout) e o
    // container muda de tamanho depois do primeiro paint. Sem redesenhar, o
    // circulo do raio fica achatado (visto em 25/11/2026: 292x225 px).
    var reajustando = false;
    var jaEnquadrou = false;
    function reajustarMapa() {
        if (reajustando) return;
        reajustando = true;
        window.requestAnimationFrame(function () {
            mapa.invalidateSize();
            mapa.eachLayer(function (camada) {
                if (camada.redraw) camada.redraw();
            });
            // O primeiro fitBounds pode ter rodado com o card ainda baixo e
            // escolhido um zoom afastado (o raio de 5 km parecia pequeno).
            // Reenquadra UMA vez quando o card tem altura de verdade.
            if (!jaEnquadrou && mapa.getSize().y > 200) {
                jaEnquadrou = true;
                mapa.fitBounds(circulo.getBounds(), { padding: [16, 16] });
            }
            reajustando = false;
        });
    }
    if (window.ResizeObserver) {
        new window.ResizeObserver(reajustarMapa).observe(el);
    } else {
        window.addEventListener('resize', reajustarMapa);
    }

    // Gancho de diagnostico (usado em teste manual; nao aparece na tela).
    window.__proxMapa = {
        mapa: mapa,
        pontos: camadaPontos,
        zoom: function () { return mapa.getZoom(); },
        tamanho: function () { return mapa.getSize(); },
        raioKm: function () { return raioKm; },
        raioMetros: function () { return circulo.getRadius(); },
        limites: function () { return circulo.getBounds().toBBoxString(); }
    };

    // Enquadramento inicial: espera o layout assentar para nao fitar num card
    // de altura zero (o que deixava o raio espremido).
    window.setTimeout(reajustarMapa, 300);
}());
