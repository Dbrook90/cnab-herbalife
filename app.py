import os
import re
import io
import pdfplumber
from flask import Flask, request, render_template, send_file, jsonify

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20MB

# ── DADOS FIXOS HERBALIFE ────────────────────────────────────────────────────
BANCO         = '341'
NOME_EMP      = 'HERBALIFE INTERNATIONAL DO BRA'
CNPJ14        = '00292858000177'
CNPJ15        = '000292858000177'
AGENCIA       = '00786'
CONTA         = '000000012855'
DAC           = '3'
NOME_BANCO    = 'BANCO ITAU SA'


# ── HELPERS ──────────────────────────────────────────────────────────────────
def pad_right(s, n, char=' '):
    return str(s)[:n].ljust(n, char)

def pad_left(s, n, char='0'):
    return str(s)[:n].rjust(n, char)

def limpa_cnpj(cnpj):
    """Remove pontos, barras e traços do CNPJ e retorna apenas dígitos."""
    return re.sub(r'\D', '', str(cnpj))

def formata_valor_nominal(centavos_str):
    """
    Replica o padrão do arquivo de referência que funcionou:
    valor em centavos + '00000000', pad esquerda 15 dígitos.
    Ex: 237513 → '023751300000000'
    """
    v = str(int(centavos_str))
    return pad_left(v + '00000000', 15)

def formata_valor_pgto(centavos_str):
    """
    Valor de pagamento: pad esquerda 15 dígitos.
    Ex: 237513 → '000000000237513'
    """
    return pad_left(str(int(centavos_str)), 15)

def valor_para_centavos(valor_str):
    """Converte string 'R$ 1.264,75' ou '1264,75' ou '1264.75' em centavos inteiros."""
    v = re.sub(r'[R$\s]', '', valor_str)
    v = v.replace('.', '').replace(',', '.')
    return int(round(float(v) * 100))

def formata_data(data_str):
    """Converte data DD/MM/AAAA → DDMMAAAA."""
    return re.sub(r'\D', '', data_str)


# ── EXTRAÇÃO DE DADOS DO PDF ─────────────────────────────────────────────────
def extrai_dados_boleto(pdf_bytes):
    """
    Extrai do PDF de boleto:
    - banco (3 dígitos do código de barras)
    - código de barras (44 chars)
    - nome cedente (fornecedor)
    - CNPJ cedente
    - vencimento (DDMMAAAA)
    - valor em centavos
    """
    texto = ''
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            texto += (page.extract_text() or '') + '\n'

    dados = {}

    # ── Código de barras (44 dígitos) ────────────────────────────────────────
    # Prioridade 1: 44 dígitos contínuos no texto
    match_barras = re.search(r'\b(\d{44})\b', texto)
    if match_barras:
        dados['cod_barras'] = match_barras.group(1)
    else:
        # Prioridade 2: linha digitável no formato BBBMC.CCCCD CCCCC.CCCCCD CCCCC.CCCCCD D FFFFFFFVVVVVVVVVV
        ld_match = re.search(
            r'(\d{5})\.(\d{5})\s+(\d{5})\.(\d{6})\s+(\d{5})\.(\d{6})\s+(\d)\s+(\d{14})',
            texto
        )
        if ld_match:
            g = ld_match.groups()
            # g[0]=BBBMC+cl[0], g[1]=cl[1:5]+dac1, g[2]=cl[5:10], g[3]=cl[10:15]+dac2
            # g[4]=cl[15:20], g[5]=cl[20:25]+dac3, g[6]=dac_geral, g[7]=fator(4)+valor(10)
            banco_moeda = g[0][:4]
            cl_p1 = g[0][4] + g[1][:4]       # 1 char + 4 chars = 5
            cl_p2 = g[2] + g[3][:5]           # 5 + 5 = 10
            cl_p3 = g[4] + g[5][:5]           # 5 + 5 = 10
            campo_livre = cl_p1 + cl_p2 + cl_p3  # 25 chars
            dados['cod_barras'] = banco_moeda + g[6] + g[7] + campo_livre  # 4+1+14+25 = 44

        # Prioridade 3: linha digitável compacta sem pontos (47 dígitos)
        if 'cod_barras' not in dados:
            ld2 = re.search(r'\b(\d{47})\b', texto)
            if ld2:
                ld = ld2.group(1)
                cl = ld[4:9] + ld[10:20] + ld[21:31]
                dados['cod_barras'] = ld[0:4] + ld[32] + ld[33:47] + cl

    # ── Banco (3 primeiros dígitos do código de barras) ──────────────────────
    if 'cod_barras' in dados:
        dados['banco'] = dados['cod_barras'][:3]
    else:
        # Tenta encontrar o banco pelo texto
        m = re.search(r'BANCO\s+\w+\s+(\d{3})', texto, re.IGNORECASE)
        dados['banco'] = m.group(1) if m else '000'

    # ── Vencimento ───────────────────────────────────────────────────────────
    venc_match = re.search(r'[Vv]encimento[\s\S]{0,30}?(\d{2}/\d{2}/\d{4})', texto)
    if not venc_match:
        venc_match = re.search(r'(\d{2}/\d{2}/\d{4})', texto)
    dados['vencimento'] = formata_data(venc_match.group(1)) if venc_match else '00000000'
    dados['vencimento_display'] = venc_match.group(1) if venc_match else ''

    # ── Valor ────────────────────────────────────────────────────────────────
    valor_match = re.search(
        r'(?:Valor|Quantia|Quantia Cobrada|Valor do Documento|Valor Cobrado)'
        r'[\s\S]{0,30}?([\d\.]+,\d{2})', texto, re.IGNORECASE
    )
    if not valor_match:
        # Tenta pegar valor do código de barras (posições 10-19 = fator+valor)
        if 'cod_barras' in dados and len(dados['cod_barras']) == 44:
            valor_barras = dados['cod_barras'][9:19]
            centavos = int(valor_barras)
            dados['valor_centavos'] = centavos
            reais = centavos / 100
            dados['valor_display'] = f"{reais:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        else:
            dados['valor_centavos'] = 0
            dados['valor_display'] = '0,00'
    else:
        dados['valor_display'] = valor_match.group(1)
        dados['valor_centavos'] = valor_para_centavos(valor_match.group(1))

    # ── Nome cedente ─────────────────────────────────────────────────────────
    cedente_match = re.search(
        r'(?:Cedente|Benefici[aá]rio|Favorecido)[:\s]+([A-Z][A-Z\s\.&]{5,50})',
        texto, re.IGNORECASE
    )
    dados['cedente_nome'] = cedente_match.group(1).strip()[:40] if cedente_match else 'NAO IDENTIFICADO'

    # ── CNPJ cedente ─────────────────────────────────────────────────────────
    cnpjs = re.findall(r'\d{2}[\.\s]?\d{3}[\.\s]?\d{3}[\/\s]?\d{4}[-\s]?\d{2}', texto)
    cnpj_cedente = ''
    for c in cnpjs:
        limpo = limpa_cnpj(c)
        if limpo != '00292858000177'[:14] and limpo != '00292858000258'[:14] and len(limpo) == 14:
            cnpj_cedente = limpo
            break
    dados['cedente_cnpj14'] = cnpj_cedente
    dados['cedente_cnpj15'] = pad_left(cnpj_cedente, 15) if cnpj_cedente else '0' * 15

    return dados


# ── GERADOR CNAB 240 ─────────────────────────────────────────────────────────
def make_header_arquivo(data_arq, hora_arq):
    h  = BANCO + '0000' + '0'
    h += ' ' * 6 + '080' + '2' + CNPJ14 + ' ' * 20
    h += AGENCIA + ' ' + CONTA + ' ' + ' '
    h += pad_right(NOME_EMP, 30) + pad_right(NOME_BANCO, 30)
    h += ' ' * 10 + '1' + data_arq + hora_arq
    h += '0' * 9 + '00101' + ' ' * 69
    assert len(h) == 240, f'Header arquivo: {len(h)}'
    return h

def make_header_lote(lote, forma):
    l  = BANCO + lote + '1' + 'C' + '98' + forma + '030' + ' '
    l += '2' + CNPJ14 + ' ' * 20
    l += AGENCIA + ' ' + CONTA + ' ' + DAC
    l += pad_right(NOME_EMP, 30) + ' ' * 40 + ' ' * 30 + ' ' * 10
    l += '0' * 8 + ' ' * 50
    assert len(l) == 240, f'Header lote: {len(l)}'
    return l

def make_segmento_j(lote, seq, barras, cedente30, venc, centavos, doc):
    vnom = formata_valor_nominal(centavos)
    vpgt = formata_valor_pgto(centavos)
    seq5 = pad_left(str(seq), 5)

    j  = BANCO + lote + '3' + seq5 + 'J' + '0' + '00'
    j += barras                          # 018-061 (44)
    j += pad_right(cedente30, 30)        # 062-091
    j += venc                            # 092-099
    j += '00000000'                      # 100-107 data pgto = zeros
    j += vnom                            # 108-122 valor nominal
    j += '000000000000000'               # 123-137 desconto/abat
    j += '0000000' + venc               # 138-152 acréscimo (7 zeros + venc)
    j += vpgt                            # 153-167 valor pagamento
    j += '00000'                         # 168-172 qtd moeda
    j += '00000'                         # 173-177 num doc deb
    j += pad_right('00000' + doc[:10], 15)  # 178-192 num doc banco
    j += ' '                             # 193 cod moeda
    j += ' ' * 16                        # 194-209 num doc empresa
    j += ' ' * 5                         # 210-214
    j += ' ' * 26                        # 215-240
    assert len(j) == 240, f'Seg J: {len(j)}'
    return j

def make_segmento_j52(lote, seq, cnpj15_ced, nome40_ced):
    seq5 = pad_left(str(seq), 5)
    j52  = BANCO + lote + '3' + seq5 + 'J' + '000' + '52'
    j52 += '2' + CNPJ15 + pad_right(NOME_EMP, 40)
    j52 += '2' + cnpj15_ced + pad_right(nome40_ced, 40)
    j52 += '0' + '0' * 15 + ' ' * 93
    assert len(j52) == 240, f'J52: {len(j52)}'
    return j52

def make_trailer_lote(lote, qtd_registros, total_centavos):
    tl  = BANCO + lote + '5' + ' ' * 9
    tl += pad_left(str(qtd_registros), 6)
    tl += pad_left(str(total_centavos), 18)
    tl += '0' * 18 + '0' * 5 + ' ' * 176
    assert len(tl) == 240, f'Trailer lote: {len(tl)}'
    return tl

def make_trailer_arquivo(qtd_lotes, qtd_registros):
    ta  = BANCO + '9999' + '9' + ' ' * 9
    ta += pad_left(str(qtd_lotes), 6)
    ta += pad_left(str(qtd_registros), 6)
    ta += ' ' * 211
    assert len(ta) == 240, f'Trailer arquivo: {len(ta)}'
    return ta

def gera_cnab(boletos, data_arq, hora_arq):
    """
    boletos: lista de dicts com chaves:
        cod_barras, cedente_nome, cedente_cnpj15, vencimento, valor_centavos, doc
    Separa em lote 30 (Itaú banco 341) e lote 31 (outros bancos).
    """
    from datetime import datetime
    itau   = [b for b in boletos if b['cod_barras'][:3] == '341']
    outros = [b for b in boletos if b['cod_barras'][:3] != '341']

    linhas = []
    linhas.append(make_header_arquivo(data_arq, hora_arq))

    num_lote = 0
    total_registros = 1  # header arquivo

    for grupo, forma in [(itau, '30'), (outros, '31')]:
        if not grupo:
            continue
        num_lote += 1
        lote_str = pad_left(str(num_lote), 4)
        linhas.append(make_header_lote(lote_str, forma))
        total_centavos_lote = 0
        qtd_reg_lote = 1  # header lote

        for i, b in enumerate(grupo, start=1):
            seq = i * 2 - 1  # J = seq ímpar, J52 = mesmo seq
            linhas.append(make_segmento_j(
                lote_str, seq,
                b['cod_barras'],
                b['cedente_nome'][:30],
                b['vencimento'],
                b['valor_centavos'],
                b.get('doc', 'CNAB')
            ))
            linhas.append(make_segmento_j52(
                lote_str, seq,
                b['cedente_cnpj15'],
                b['cedente_nome'][:40]
            ))
            total_centavos_lote += b['valor_centavos']
            qtd_reg_lote += 2  # J + J52

        qtd_reg_lote += 1  # trailer lote
        linhas.append(make_trailer_lote(lote_str, qtd_reg_lote, total_centavos_lote))
        total_registros += qtd_reg_lote

    total_registros += 1  # trailer arquivo
    linhas.append(make_trailer_arquivo(num_lote, total_registros))

    conteudo = b'\r\n'.join(l.encode('latin-1') for l in linhas) + b'\r\n'
    return conteudo


# ── ROTAS FLASK ──────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/extrair', methods=['POST'])
def extrair():
    """Recebe PDFs e retorna os dados extraídos em JSON."""
    arquivos = request.files.getlist('pdfs')
    if not arquivos:
        return jsonify({'erro': 'Nenhum arquivo enviado.'}), 400

    resultados = []
    for arq in arquivos:
        if not arq.filename.lower().endswith('.pdf'):
            continue
        try:
            dados = extrai_dados_boleto(arq.read())
            dados['nome_arquivo'] = arq.filename
            resultados.append(dados)
        except Exception as e:
            resultados.append({
                'nome_arquivo': arq.filename,
                'erro': str(e)
            })

    return jsonify(resultados)

@app.route('/gerar', methods=['POST'])
def gerar():
    """Recebe JSON com lista de boletos e retorna o arquivo CNAB 240."""
    payload = request.get_json()
    boletos = payload.get('boletos', [])
    data_arq = payload.get('data_arq', '01012000')
    hora_arq = payload.get('hora_arq', '000000')

    if not boletos:
        return jsonify({'erro': 'Nenhum boleto informado.'}), 400

    try:
        cnab = gera_cnab(boletos, data_arq, hora_arq)
        return send_file(
            io.BytesIO(cnab),
            mimetype='text/plain',
            as_attachment=True,
            download_name='remessa_herbalife.txt'
        )
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
