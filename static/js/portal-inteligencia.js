/* portal-inteligencia.js - graficos da pagina de estatisticas.
   Le apenas os dados reais do contexto Django (blocos json_script) e desenha
   CSS/SVG. Nao faz fetch nem query: sem dado, nao desenha nada. */
(function () {
    'use strict';

    // Mesma ordem de cores usada no {% cycle %} da legenda (portal-inteligencia.css).
    var CORES = ['var(--viz-1)', 'var(--viz-3)', 'var(--viz-4)', 'var(--viz-2)'];

    function fmt(n) {
        var v = Number(n);
        return isFinite(v) ? v.toLocaleString('pt-BR') : '0';
    }

    function pct(n) {
        return (Number(n) || 0).toFixed(1).replace('.', ',') + '%';
    }

    /* ----------------------------------------------------------
       Rosca "Proporcao por porte": share calculado sobre a soma dos
       proprios portes (dado real de portes.*.total).
       ---------------------------------------------------------- */
    function renderDonut() {
        var alvo = document.getElementById('intel-donut');
        if (!alvo) return;

        // Dados reais da legenda, renderizada pelo template (data-porte-total).
        var itensEls = document.querySelectorAll('.intel-donut-legend li[data-porte-total]');
        if (!itensEls.length) return;

        var total = 0;
        var itens = Array.prototype.map.call(itensEls, function (li) {
            var labelEl = li.querySelector('.intel-donut-legend__label');
            var valor = Number(li.getAttribute('data-porte-total')) || 0;
            total += valor;
            return {
                label: labelEl ? labelEl.textContent.trim() : 'PORTE',
                total: valor,
                el: li
            };
        });
        if (total <= 0) return;

        var acc = 0;
        var paradas = [];
        itens.forEach(function (item, i) {
            var inicio = acc;
            acc += (item.total / total) * 100;
            var fim = (i === itens.length - 1) ? 100 : acc;
            item.pct = (item.total / total) * 100;
            paradas.push(CORES[i % CORES.length] + ' ' + inicio.toFixed(3) + '% ' + fim.toFixed(3) + '%');
        });

        alvo.style.setProperty('--ui-donut', 'conic-gradient(' + paradas.join(', ') + ')');
        alvo.setAttribute('aria-label', 'Proporcao por porte: ' + itens.map(function (item) {
            return item.label + ' ' + pct(item.pct);
        }).join(', '));

        itens.forEach(function (item) {
            var pctEl = item.el.querySelector('[data-pct]');
            if (pctEl) pctEl.textContent = ' \u00b7 ' + pct(item.pct);
        });
    }

    /* ----------------------------------------------------------
       Area "Empresas por ano de abertura".
       Le a propria tabela de detalhamento (data-ano/data-total); a view
       entrega por_ano em ordem decrescente e o grafico ordena ascendente.
       O viewBox acompanha a largura do container para os rotulos SVG
       nao encolherem no mobile.
       ---------------------------------------------------------- */
    function renderArea() {
        var svg = document.getElementById('intel-ano');
        var tabela = document.getElementById('intel-tabela-ano');
        if (!svg || !tabela) return;

        var linhas = tabela.querySelectorAll('tbody tr[data-ano]');
        if (!linhas.length) return;

        var serie = Array.prototype.map.call(linhas, function (tr) {
            return {
                ano: Number(tr.getAttribute('data-ano')) || 0,
                total: Number(tr.getAttribute('data-total')) || 0
            };
        }).sort(function (a, b) { return a.ano - b.ano; });

        var valores = serie.map(function (d) { return d.total; });
        var maximo = Math.max.apply(null, valores);
        if (!isFinite(maximo) || maximo <= 0) return;

        var H = 200;
        var padL = 12;
        var padR = 12;
        var padT = 24;
        var base = H - 32;

        function desenhar() {
            var largura = Math.round(svg.getBoundingClientRect().width) || 640;
            var W = Math.max(300, largura);
            var n = serie.length;
            var passo = n > 1 ? (W - padL - padR) / (n - 1) : 0;

            function px(i) { return padL + passo * i; }
            function py(v) { return base - (v / maximo) * (base - padT); }

            var linha = serie.map(function (d, i) {
                return (i === 0 ? 'M' : 'L') + px(i).toFixed(1) + ',' + py(valores[i]).toFixed(1);
            }).join(' ');
            var area = linha +
                ' L' + px(n - 1).toFixed(1) + ',' + base +
                ' L' + px(0).toFixed(1) + ',' + base + ' Z';

            var grid = '';
            [0.25, 0.5, 0.75, 1].forEach(function (fracao) {
                var y = base - (base - padT) * fracao;
                grid += '<line x1="0" y1="' + y.toFixed(1) + '" x2="' + W + '" y2="' + y.toFixed(1) + '"></line>';
            });

            var pontos = serie.map(function (d, i) {
                return '<circle class="ui-chart-dot" cx="' + px(i).toFixed(1) + '" cy="' +
                    py(valores[i]).toFixed(1) + '" r="2.5"></circle>';
            }).join('');

            var meio = Math.floor((n - 1) / 2);
            var rotulos =
                '<text x="' + padL + '" y="' + (padT - 8) + '">m\u00e1x. ' + fmt(maximo) + '</text>' +
                '<text x="' + padL + '" y="' + (H - 10) + '" text-anchor="start">' + serie[0].ano + '</text>';
            if (n > 2) {
                rotulos += '<text x="' + px(meio).toFixed(1) + '" y="' + (H - 10) + '" text-anchor="middle">' +
                    serie[meio].ano + '</text>';
            }
            rotulos += '<text x="' + (W - padR).toFixed(1) + '" y="' + (H - 10) + '" text-anchor="end">' +
                serie[n - 1].ano + '</text>';

            svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
            svg.innerHTML =
                '<g class="ui-chart-grid">' + grid + '</g>' +
                '<path class="ui-chart-area" d="' + area + '"></path>' +
                '<path class="ui-chart-line" d="' + linha + '"></path>' +
                pontos + rotulos;

            var indiceMaximo = valores.indexOf(maximo);
            svg.setAttribute('aria-label', 'Empresas por ano de abertura de ' + serie[0].ano +
                ' a ' + serie[n - 1].ano + '; maior valor em ' + serie[indiceMaximo].ano +
                ' com ' + fmt(maximo) + ' empresas. Os numeros estao na tabela de detalhamento.');
        }

        desenhar();

        var timer = null;
        window.addEventListener('resize', function () {
            window.clearTimeout(timer);
            timer = window.setTimeout(desenhar, 150);
        });
    }

    function iniciar() {
        renderDonut();
        renderArea();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', iniciar);
    } else {
        iniciar();
    }
})();
