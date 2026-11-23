"""Formulario publico de contato. Sem acentos no codigo."""

from django import forms

from .models import LeadContato


class ContatoForm(forms.ModelForm):
    consentimento = forms.BooleanField(
        label='Autorizo o uso destes dados para responder ao meu contato.',
        error_messages={'required': 'Precisamos da sua autorizacao para responder.'},
    )

    class Meta:
        model = LeadContato
        fields = ('nome', 'email', 'empresa', 'telefone', 'mensagem')
        widgets = {
            'nome': forms.TextInput(attrs={'autocomplete': 'name'}),
            'email': forms.EmailInput(attrs={'autocomplete': 'email'}),
            'empresa': forms.TextInput(attrs={'autocomplete': 'organization'}),
            'telefone': forms.TextInput(attrs={'autocomplete': 'tel'}),
            'mensagem': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['empresa'].required = False
        self.fields['telefone'].required = False
        for nome in ('nome', 'email', 'mensagem'):
            self.fields[nome].required = True
        for nome in ('nome', 'email', 'empresa', 'telefone', 'mensagem'):
            self.fields[nome].widget.attrs.setdefault('class', 'form-control')
        self.fields['nome'].widget.attrs.setdefault('placeholder', 'Como devo te chamar')
        self.fields['email'].widget.attrs.setdefault('placeholder', 'voce@empresa.com.br')
        self.fields['empresa'].widget.attrs.setdefault('placeholder', 'Nome da empresa')
        self.fields['telefone'].widget.attrs.setdefault('placeholder', '(00) 00000-0000')
        self.fields['mensagem'].widget.attrs.setdefault(
            'placeholder', 'Ex.: preciso consultar uma lista de CNPJs e cruzar com minha carteira de clientes.')
