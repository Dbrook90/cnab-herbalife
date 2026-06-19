# CNAB 240 — Herbalife International do Brasil

Sistema web para geração de arquivos de remessa CNAB 240 (SISPAG Itaú), com extração automática de dados a partir de PDFs de boletos.

## Funcionalidades

- Upload de múltiplos PDFs de boletos
- Extração automática de: banco, código de barras, cedente, CNPJ, valor, vencimento
- Separação automática em lotes: **Itaú (forma 30)** e **outros bancos (forma 31)**
- Vencimento e data de pagamento editáveis por boleto, com opção de aplicar a todos
- Download do arquivo `.txt` CNAB 240 com CRLF e 240 bytes por linha

## Estrutura do projeto

```
cnab_herbalife/
├── app.py              # Flask + lógica CNAB
├── requirements.txt
├── Procfile            # Para Render.com
├── templates/
│   └── index.html      # Interface web
└── static/
    └── logo.png        # Logo Herbalife
```

## Rodando localmente

```bash
pip install -r requirements.txt
python app.py
# Acesse http://localhost:5000
```

## Deploy no Render.com

1. Suba o projeto no GitHub
2. No Render, crie um novo **Web Service**
3. Selecione o repositório
4. Configure:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Environment:** Python 3
5. Deploy

## Dados fixos (Herbalife)

| Campo | Valor |
|---|---|
| Banco | 341 (Itaú) |
| CNPJ (14 dig) | 00292858000177 |
| CNPJ (15 dig) | 000292858000177 |
| Agência | 00786 |
| Conta | 000000012855 |
| DAC | 3 |

## Formato do arquivo gerado

```
Header Arquivo                          (1 linha)
  Header Lote 1 — Itaú (forma 30)      (1 linha)
    Segmento J  ← boleto Itaú          (1 linha por boleto)
    Segmento J52                        (1 linha por boleto)
  Trailer Lote 1                        (1 linha)
  Header Lote 2 — Outros (forma 31)    (1 linha, se houver)
    Segmento J  ← outros bancos
    Segmento J52
  Trailer Lote 2
Trailer Arquivo                         (1 linha)
```

Cada linha: **240 bytes + CRLF (`\r\n`)**

---

*Created by Daniele Ribeiro*
