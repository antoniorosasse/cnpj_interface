/* Melhoria progressiva: consultas, URLs e respostas continuam no Django.
   Este arquivo cuida do tema, da busca global, do toast, de copiar CNPJ,
   dos filtros avancados, do painel lateral (drawer) e das abas do detalhe.
   Nenhum seletor do contrato e renomeado aqui. */
(function () {
    'use strict';
    var root = document.documentElement;
    var toastTimer;

    /* ---------------------------------------------------------------- tema */
    function pintaTema(t) {
        root.setAttribute('data-theme', t);
        root.setAttribute('data-bs-theme', t);
        var icone = document.getElementById('icone-tema');
        var botao = document.getElementById('btn-tema');
        var meta = document.getElementById('meta-tema');
        if (icone) { icone.className = t === 'dark' ? 'bi bi-sun' : 'bi bi-moon-stars'; }
        if (botao) { botao.setAttribute('aria-checked', t === 'dark' ? 'true' : 'false'); }
        if (meta) { meta.setAttribute('content', t === 'dark' ? '#051512' : '#f4fbf8'); }
    }
    function temaAtual() { return root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'; }
    pintaTema(temaAtual());

    var botaoTema = document.getElementById('btn-tema');
    if (botaoTema && !botaoTema.dataset.uiTema) {
        botaoTema.dataset.uiTema = '1';
        botaoTema.addEventListener('click', function () {
            var novo = temaAtual() === 'dark' ? 'light' : 'dark';
            try { localStorage.setItem('sistema-tema', novo); } catch (e) { /* modo privado */ }
            pintaTema(novo);
        });
    }

    /* ---------------------------------------------------------------- toast */
    function toast(text) {
        var box = document.getElementById('ui-toast');
        if (!box) {
            box = document.createElement('div');
            box.id = 'ui-toast';
            box.className = 'ui-toast';
            box.setAttribute('role', 'status');
            box.setAttribute('aria-live', 'polite');
            document.body.appendChild(box);
        }
        box.textContent = text;
        box.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { box.hidden = true; }, 3200);
    }

    /* ---------------------------------------------------------- copiar CNPJ */
    async function copiar(texto) {
        var foco = document.activeElement;
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(texto);
            } else {
                var campo = document.createElement('textarea');
                campo.value = texto;
                campo.style.position = 'fixed';
                campo.style.opacity = '0';
                document.body.appendChild(campo);
                campo.select();
                var ok = document.execCommand('copy');
                campo.remove();
                if (foco) { foco.focus(); }
                if (!ok) { throw new Error('clipboard'); }
            }
            toast('CNPJ copiado.');
        } catch (erro) {
            toast('Não foi possível copiar. Selecione o CNPJ e copie manualmente.');
        }
    }
    document.addEventListener('click', function (evento) {
        var botao = evento.target.closest('[data-copy-cnpj]');
        if (botao) { evento.preventDefault(); copiar(botao.dataset.copyCnpj); }
    });

    /* ------------------------------------------------------- busca global */
    /* Sem JS o form envia razao_social. Com JS, a partir de 8 digitos
       (ignorando pontuacao) o valor vira o parametro cnpj da mesma view. */
    var buscaGlobal = document.querySelector('[data-ui-global-search]');
    if (buscaGlobal) {
        var campoBusca = buscaGlobal.querySelector('input[name="razao_social"]');
        var campoCnpj = buscaGlobal.querySelector('[data-ui-global-cnpj]');
        buscaGlobal.addEventListener('submit', function () {
            if (!campoBusca || !campoCnpj) { return; }
            var digitos = campoBusca.value.replace(/\D/g, '');
            if (digitos.length >= 8) {
                campoCnpj.value = digitos;
                campoCnpj.disabled = false;
                campoBusca.disabled = true;
            } else {
                campoCnpj.value = '';
                campoCnpj.disabled = true;
            }
        });
    }

    /* --------------------------------------- labels de formularios antigos */
    document.querySelectorAll('form label').forEach(function (label, indice) {
        if (label.htmlFor) { return; }
        var campo = label.parentElement.querySelector('input:not([type=hidden]), select, textarea');
        if (campo) {
            if (!campo.id) { campo.id = 'ui-field-' + indice; }
            label.htmlFor = campo.id;
        }
    });

    /* ------------------------------------------------- filtros avancados */
    var advanced = document.getElementById('ui-advanced');
    var advancedButton = document.getElementById('ui-advanced-toggle');
    if (advanced && advancedButton) {
        var usados = Array.from(advanced.querySelectorAll('input, select')).some(function (campo) {
            return campo.value && campo.name !== 'ordem';
        });
        advanced.hidden = !usados;
        advancedButton.hidden = false;
        advancedButton.setAttribute('aria-expanded', String(usados));
        advancedButton.addEventListener('click', function () {
            advanced.hidden = !advanced.hidden;
            advancedButton.setAttribute('aria-expanded', String(!advanced.hidden));
        });
    }

    /* Uma troca de UF nao deve reenviar o municipio da UF anterior. */
    var uf = document.querySelector('.ui-search-form select[name=uf]');
    if (uf) {
        uf.removeAttribute('onchange');
        uf.addEventListener('change', function () {
            var cidade = uf.form.querySelector('[name=municipio]');
            if (cidade) { cidade.value = ''; }
            if (uf.form.requestSubmit) { uf.form.requestSubmit(); } else { uf.form.submit(); }
        });
    }

    /* ------------------------------------------------------ painel lateral */
    var painel = document.getElementById('ui-details-panel');
    if (painel) {
        var conteudo = document.getElementById('ui-details-content');
        var layout = document.getElementById('ui-results-layout');
        var fechar = document.getElementById('ui-details-close');
        var completo = document.getElementById('ui-details-full');
        var status = document.getElementById('ui-panel-status');
        var origem = null;
        var pendente;
        var mobile = window.matchMedia('(max-width: 991.98px)');

        function focaveis() {
            return Array.from(painel.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
                .filter(function (elemento) { return elemento.offsetParent !== null; });
        }
        function fechaPainel() {
            if (pendente) { pendente.abort(); }
            painel.hidden = true;
            painel.removeAttribute('aria-busy');
            if (layout) { layout.classList.remove('ui-has-detail'); }
            document.body.classList.remove('ui-panel-open');
            if (status) { status.hidden = true; status.classList.remove('is-loading'); }
            if (conteudo) { conteudo.replaceChildren(); }
            document.querySelectorAll('.ui-row-selected').forEach(function (linha) { linha.classList.remove('ui-row-selected'); });
            if (origem && origem.isConnected) { origem.focus(); }
        }
        if (fechar) { fechar.addEventListener('click', fechaPainel); }

        document.addEventListener('keydown', function (evento) {
            if (painel.hidden) { return; }
            if (evento.key === 'Escape') { evento.preventDefault(); fechaPainel(); return; }
            /* Prende o foco apenas no drawer de tela cheia (mobile). */
            if (evento.key === 'Tab' && mobile.matches) {
                var lista = focaveis();
                if (!lista.length) { return; }
                var primeiro = lista[0];
                var ultimo = lista[lista.length - 1];
                if (evento.shiftKey && document.activeElement === primeiro) { evento.preventDefault(); ultimo.focus(); }
                else if (!evento.shiftKey && document.activeElement === ultimo) { evento.preventDefault(); primeiro.focus(); }
            }
        });

        document.addEventListener('click', function (evento) {
            var link = evento.target.closest('a[data-company-detail]');
            if (!link || evento.ctrlKey || evento.metaKey || evento.shiftKey || evento.altKey || evento.button !== 0) { return; }
            var url = new URL(link.href, location.href);
            if (url.origin !== location.origin) { return; }
            evento.preventDefault();
            if (!painel.contains(link)) { origem = link; }
            if (completo) { completo.href = url.href; }
            if (status) {
                status.textContent = 'Carregando ficha cadastral...';
                status.hidden = false;
                status.classList.add('is-loading');
            }
            document.querySelectorAll('.ui-row-selected').forEach(function (linha) { linha.classList.remove('ui-row-selected'); });
            var linha = link.closest('tr');
            if (linha) {
                linha.classList.add('ui-row-selected');
                /* No desktop o painel e' lateral: rola a pagina ate' a linha
                   para o contexto ficar visivel (no mobile o drawer e' a tela). */
                if (!mobile.matches && linha.scrollIntoView) {
                    linha.scrollIntoView({ block: 'center', behavior: 'smooth' });
                }
            }
            painel.hidden = false;
            painel.setAttribute('aria-busy', 'true');
            if (layout) { layout.classList.add('ui-has-detail'); }
            document.body.classList.add('ui-panel-open');
            if (conteudo) { conteudo.replaceChildren(); }
            if (fechar) { fechar.focus(); }
            if (pendente) { pendente.abort(); }
            pendente = new AbortController();
            var controlador = pendente;
            fetch(url.href, {
                credentials: 'same-origin',
                signal: controlador.signal,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            }).then(function (resposta) {
                if (!resposta.ok || new URL(resposta.url).origin !== location.origin) { throw new Error('request'); }
                return resposta.text();
            }).then(function (html) {
                if (controlador.signal.aborted) { return; }
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var pagina = doc.querySelector('.ui-detail-page');
                if (!pagina) { throw new Error('detail'); }
                pagina.querySelectorAll('script').forEach(function (script) { script.remove(); });
                pagina.querySelectorAll('a[href]').forEach(function (anchor) {
                    var alvo = new URL(anchor.getAttribute('href'), url.href);
                    anchor.href = alvo.href;
                    if (alvo.origin === location.origin && /\/empresa\/[^/]+\/$/.test(alvo.pathname)) {
                        anchor.setAttribute('data-company-detail', '');
                        anchor.removeAttribute('target');
                    }
                });
                if (conteudo) {
                    conteudo.replaceChildren(document.importNode(pagina, true));
                    initializeDetailTabs(conteudo.querySelector('.ui-detail-page > .row > .col'));
                }
                painel.removeAttribute('aria-busy');
                if (status) { status.hidden = true; status.classList.remove('is-loading'); }
            }).catch(function (erro) {
                if (erro.name === 'AbortError') { return; }
                painel.removeAttribute('aria-busy');
                if (status) {
                    status.hidden = false;
                    status.classList.remove('is-loading');
                    status.textContent = 'Não foi possível carregar a ficha. Use Abrir página completa para continuar.';
                }
            });
        });
    }

    /* -------------------------------------------------- abas do detalhe */
    /* As abas reorganizam os blocos originais sem descartar campos. */
    function initializeDetailTabs(detail) {
        if (!detail) { return; }
        var cards = Array.from(detail.children).filter(function (elemento) {
            return elemento.classList.contains('card');
        });
        if (cards.length <= 1) { return; }
        var tabs = document.createElement('div');
        tabs.className = 'ui-detail-tabs';
        tabs.setAttribute('role', 'tablist');
        tabs.setAttribute('aria-label', 'Seções da ficha');
        var grupos = [['resumo', 'Resumo', []], ['socios', 'Sócios', []], ['estabelecimentos', 'Filiais e matriz', []]];
        cards.slice(1).forEach(function (card) {
            var titulo = card.querySelector('.card-header');
            var texto = titulo ? titulo.textContent.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase() : '';
            grupos[/socios/.test(texto) ? 1 : /estabelecimentos/.test(texto) ? 2 : 0][2].push(card);
        });
        grupos = grupos.filter(function (grupo) { return grupo[2].length; });
        function seleciona(indice) {
            grupos.forEach(function (grupo, i) {
                grupo[2].forEach(function (card) { card.hidden = i !== indice; });
                var botao = tabs.children[i];
                botao.setAttribute('aria-selected', String(i === indice));
                botao.tabIndex = i === indice ? 0 : -1;
            });
        }
        grupos.forEach(function (grupo, indice) {
            var botao = document.createElement('button');
            botao.type = 'button';
            botao.textContent = grupo[1];
            botao.id = 'ui-tab-' + grupo[0];
            botao.setAttribute('role', 'tab');
            var ids = [];
            grupo[2].forEach(function (card, i) {
                card.id = 'ui-section-' + grupo[0] + '-' + i;
                card.setAttribute('role', 'tabpanel');
                card.setAttribute('aria-labelledby', botao.id);
                ids.push(card.id);
            });
            botao.setAttribute('aria-controls', ids.join(' '));
            botao.addEventListener('click', function () { seleciona(indice); });
            botao.addEventListener('keydown', function (evento) {
                if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(evento.key) < 0) { return; }
                evento.preventDefault();
                var proximo = evento.key === 'Home' ? 0
                    : evento.key === 'End' ? grupos.length - 1
                    : (indice + (evento.key === 'ArrowRight' ? 1 : grupos.length - 1)) % grupos.length;
                seleciona(proximo);
                tabs.children[proximo].focus();
            });
            tabs.appendChild(botao);
        });
        cards[0].after(tabs);
        seleciona(0);
    }
    initializeDetailTabs(document.querySelector('.ui-detail-page > .row > .col'));
})();
