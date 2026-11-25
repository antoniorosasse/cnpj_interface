/* prox-raio.js - slider do raio na pagina /proximidade/.

   Substitui o <select> de opcoes fixas (1, 2, 5, 10, 20, 50, 100) por um
   range continuo de 1 a 100 km. O valor so vai para o servidor quando o
   usuario clica em Buscar: cada busca conta no limite diario de consultas
   (5/dia no anonimo), entao disparar a cada arrasto queimaria a cota. O
   rotulo muda ao vivo e a dica avisa quando ha valor novo para aplicar. */
(function () {
    'use strict';

    var campo = document.getElementById('prox-raio');
    var valor = document.getElementById('prox-raio-valor');
    if (!campo || !valor) return;

    var inicial = campo.value;
    var dica = document.querySelector('.prox-raio__dica');

    function atualizar() {
        valor.textContent = campo.value + ' km';
        var mudou = campo.value !== inicial;
        campo.classList.toggle('prox-raio--alterado', mudou);
        if (dica) {
            dica.textContent = mudou ? 'clique em Buscar para aplicar' : 'arraste para ajustar';
        }
    }

    campo.addEventListener('input', atualizar);
    campo.addEventListener('change', atualizar);
    atualizar();
}());
