/* Melhoria progressiva: consultas, URLs e respostas continuam no Django. */
(function () {
    'use strict';
    var root = document.documentElement;
    var toastTimer;
    function toast(text) {
        var box = document.getElementById('ui-toast');
        if (!box) { box = document.createElement('div'); box.id = 'ui-toast'; box.className = 'ui-toast'; box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite'); document.body.appendChild(box); }
        box.textContent = text; box.hidden = false;
        clearTimeout(toastTimer); toastTimer = setTimeout(function () { box.hidden = true; }, 3000);
    }
    async function copy(text) {
        var focus = document.activeElement;
        try {
            if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); }
            else { var input = document.createElement('textarea'); input.value = text; input.style.position = 'fixed'; input.style.opacity = '0'; document.body.appendChild(input); input.select(); var ok = document.execCommand('copy'); input.remove(); if (focus) { focus.focus(); } if (!ok) { throw new Error('clipboard'); } }
            toast('CNPJ copiado.');
        } catch (error) { toast('Nao foi possivel copiar. Selecione o CNPJ e copie manualmente.'); }
    }
    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-copy-cnpj]');
        if (button) { event.preventDefault(); copy(button.dataset.copyCnpj); }
    });
    document.querySelectorAll('.ui-navbar a.nav-link').forEach(function (link) {
        var url = new URL(link.href, location.href);
        if (url.origin === location.origin && url.pathname === location.pathname) { link.classList.add('active'); link.setAttribute('aria-current', 'page'); }
    });
    /* Relaciona labels e campos dos formularios originais. */
    document.querySelectorAll('form label').forEach(function (label, index) {
        if (label.htmlFor) { return; }
        var field = label.parentElement.querySelector('input:not([type=hidden]), select, textarea');
        if (field) { if (!field.id) { field.id = 'ui-field-' + index; } label.htmlFor = field.id; }
    });
    var advanced = document.getElementById('ui-advanced');
    var advancedButton = document.getElementById('ui-advanced-toggle');
    if (advanced && advancedButton) {
        var used = Array.from(advanced.querySelectorAll('input, select')).some(function (field) { return field.value && field.name !== 'ordem'; });
        advanced.hidden = !used; advancedButton.hidden = false; advancedButton.setAttribute('aria-expanded', String(used));
        advancedButton.addEventListener('click', function () { advanced.hidden = !advanced.hidden; advancedButton.setAttribute('aria-expanded', String(!advanced.hidden)); });
    }
    /* Uma troca de UF nao deve reenviar o municipio da UF anterior. */
    var uf = document.querySelector('.ui-search-form select[name=uf]');
    if (uf) { uf.removeAttribute('onchange'); uf.addEventListener('change', function () { var city = uf.form.querySelector('[name=municipio]'); if (city) { city.value = ''; } if (uf.form.requestSubmit) { uf.form.requestSubmit(); } else { uf.form.submit(); } }); }
    var panel = document.getElementById('ui-details-panel');
    if (panel) {
        var content = document.getElementById('ui-details-content');
        var layout = document.getElementById('ui-results-layout');
        var close = document.getElementById('ui-details-close');
        var full = document.getElementById('ui-details-full');
        var status = document.getElementById('ui-panel-status');
        var opener = null;
        var pending;
        function closePanel() { if (pending) { pending.abort(); } panel.hidden = true; layout.classList.remove('ui-has-detail'); document.body.classList.remove('ui-panel-open'); content.replaceChildren(); document.querySelectorAll('.ui-row-selected').forEach(function (row) { row.classList.remove('ui-row-selected'); }); if (opener && opener.isConnected) { opener.focus(); } }
        close.addEventListener('click', closePanel);
        document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && !panel.hidden) { closePanel(); } });
        document.addEventListener('click', function (event) {
            var link = event.target.closest('a[data-company-detail]');
            if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) { return; }
            var url = new URL(link.href, location.href);
            if (url.origin !== location.origin) { return; }
            event.preventDefault(); if (!panel.contains(link)) { opener = link; } full.href = url.href;
            status.textContent = 'Carregando ficha cadastral...'; status.hidden = false;
            document.querySelectorAll('.ui-row-selected').forEach(function (row) { row.classList.remove('ui-row-selected'); });
            var row = link.closest('tr'); if (row) { row.classList.add('ui-row-selected'); }
            panel.hidden = false; layout.classList.add('ui-has-detail'); document.body.classList.add('ui-panel-open'); content.replaceChildren(); close.focus();
            if (pending) { pending.abort(); } pending = new AbortController();
            var controller = pending;
            fetch(url.href, {credentials:'same-origin', signal:controller.signal, headers:{'X-Requested-With':'XMLHttpRequest'}}).then(function(response){
                if (!response.ok || new URL(response.url).origin !== location.origin) { throw new Error('request'); } return response.text();
            }).then(function(html){
                if (controller.signal.aborted) { return; }
                var doc = new DOMParser().parseFromString(html, 'text/html'); var page = doc.querySelector('.ui-detail-page');
                if (!page) { throw new Error('detail'); }
                page.querySelectorAll('script').forEach(function(script){script.remove()});
                page.querySelectorAll('a[href]').forEach(function(anchor){var target=new URL(anchor.getAttribute('href'), url.href);anchor.href=target.href;if(target.origin===location.origin&&/\/empresa\/[^/]+\/$/.test(target.pathname)){anchor.setAttribute('data-company-detail','');anchor.removeAttribute('target')}});
                content.replaceChildren(document.importNode(page,true)); initializeDetailTabs(content.querySelector('.ui-detail-page > .row > .col'));status.hidden=true;
            }).catch(function(error){if(error.name!=='AbortError'){status.hidden=false;status.textContent='Nao foi possivel carregar a ficha. Use Abrir pagina completa para continuar.'}});
        });
    }
    /* As abas reorganizam os blocos originais sem descartar campos. */
    function initializeDetailTabs(detail) {
    if (detail) {
        var cards = Array.from(detail.children).filter(function (element) { return element.classList.contains('card'); });
        if (cards.length > 1) {
            var tabs = document.createElement('div'); tabs.className = 'ui-detail-tabs'; tabs.setAttribute('role','tablist'); tabs.setAttribute('aria-label','Secoes da ficha');
            var groups = [['resumo','Resumo',[]],['socios','Socios',[]],['estabelecimentos','Filiais e matriz',[]]];
            cards.slice(1).forEach(function (card) { var title = card.querySelector('.card-header'); var text = title ? title.textContent.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase() : ''; groups[/socios/.test(text)?1:/estabelecimentos/.test(text)?2:0][2].push(card); });
            groups = groups.filter(function (group) { return group[2].length; });
            function select(index) { groups.forEach(function (group, i) { group[2].forEach(function (card) { card.hidden = i !== index; }); var button = tabs.children[i]; button.setAttribute('aria-selected',String(i===index)); button.tabIndex=i===index?0:-1; }); }
            groups.forEach(function (group, index) { var button=document.createElement('button'); button.type='button'; button.textContent=group[1]; button.id='ui-tab-'+group[0]; button.setAttribute('role','tab'); var ids=[]; group[2].forEach(function(card,i){card.id='ui-section-'+group[0]+'-'+i;card.setAttribute('role','tabpanel');card.setAttribute('aria-labelledby',button.id);ids.push(card.id)});button.setAttribute('aria-controls',ids.join(' '));button.addEventListener('click',function(){select(index)});button.addEventListener('keydown',function(event){if(['ArrowLeft','ArrowRight','Home','End'].indexOf(event.key)<0)return;event.preventDefault();var next=event.key==='Home'?0:event.key==='End'?groups.length-1:(index+(event.key==='ArrowRight'?1:groups.length-1))%groups.length;select(next);tabs.children[next].focus()});tabs.appendChild(button); });
            cards[0].after(tabs); select(0);
        }
    }
    }
    initializeDetailTabs(document.querySelector('.ui-detail-page > .row > .col'));
})();
