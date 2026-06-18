#!/usr/bin/env python3
"""
CNAB 240 — Pagamento de Fornecedores — Herbalife
Arquivo único executável. Rode com: python cnab_herbalife.py
Acesse em: http://localhost:5000
"""

# =====================================================
# DEPENDÊNCIAS — instala automaticamente se necessário
# =====================================================
import subprocess, sys

def instalar(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--break-system-packages", "-q"])

try:
    import flask
except ImportError:
    print("Instalando Flask..."); instalar("flask")

try:
    import pdfplumber
except ImportError:
    print("Instalando pdfplumber..."); instalar("pdfplumber")

try:
    import fitz
except ImportError:
    print("Instalando PyMuPDF..."); instalar("PyMuPDF")

# =====================================================
# IMPORTS
# =====================================================
from flask import Flask, request, jsonify, send_file, Response
import pdfplumber
import fitz
import re, os, io
from datetime import datetime, date

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# =====================================================
# DADOS DA EMPRESA
# =====================================================
EMPRESA = {
    "nome":      "HERBALIFE INTERNATIONAL B LTDA",
    "cnpj":      "00292858000177",
    "agencia":   "0786",
    "conta":     "128553",
    "conta_dac": "3",
}

# =====================================================
# EXTRATOR DE PDF
# =====================================================

def limpar_numero(texto):
    return re.sub(r'\D', '', texto or '')

def converter_linha_para_barras(linha):
    nums = limpar_numero(linha)
    if len(nums) == 44:
        return nums
    if len(nums) < 44:
        return None
    try:
        banco_moeda  = nums[0:4]
        campo_livre1 = nums[4:9]
        campo_livre2 = nums[10:20]
        campo_livre3 = nums[21:31]
        dac_geral    = nums[32]
        venc_valor   = nums[33:47]
        codigo = banco_moeda + dac_geral + venc_valor + campo_livre1 + campo_livre2 + campo_livre3
        return codigo if len(codigo) == 44 else None
    except:
        return None

def extrair_codigo_barras(texto):
    # Linha digitável formatada
    p = re.findall(r'\d{5}\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14}', texto)
    if p:
        cod = converter_linha_para_barras(limpar_numero(p[0]))
        if cod: return cod, p[0].strip()

    # Código de barras direto 44 dígitos
    p = re.findall(r'\b\d{44}\b', texto)
    if p: return p[0], p[0]

    # Linha digitável sem formatação (47-48 dígitos)
    p = re.findall(r'\b\d{47,48}\b', texto)
    if p:
        cod = converter_linha_para_barras(p[0])
        if cod: return cod, p[0]

    # Sequências longas
    for seq in re.findall(r'[\d\s\.]{40,60}', texto):
        nums = limpar_numero(seq)
        if len(nums) in (47, 48):
            cod = converter_linha_para_barras(nums)
            if cod: return cod, seq.strip()
        if len(nums) == 44:
            return nums, seq.strip()

    return None, None

def extrair_valor(texto):
    padroes = [
        r'(?:valor\s+(?:do\s+)?(?:documento|cobrado|total|boleto|titulo)[:\s]+)R?\$?\s*([\d\.,]+)',
        r'R\$\s*([\d]{1,3}(?:\.[\d]{3})*(?:,[\d]{2}))',
        r'(?:valor)[:\s]+R?\$?\s*([\d]{1,3}(?:\.[\d]{3})*,[\d]{2})',
    ]
    for p in padroes:
        for m in re.findall(p, texto, re.IGNORECASE):
            try:
                v = float(m.replace('.','').replace(',','.'))
                if 0.01 <= v <= 9999999.99: return v
            except: pass

    candidatos = []
    for v in re.findall(r'R?\$?\s*([\d]{1,3}(?:\.[\d]{3})*,[\d]{2})', texto, re.IGNORECASE):
        try:
            n = float(v.replace('.','').replace(',','.'))
            if 1.0 <= n <= 999999.99: candidatos.append(n)
        except: pass
    return max(candidatos) if candidatos else None

def extrair_vencimento(texto):
    padroes = [
        r'(?:vencimento|venc\.?)[:\s]+(\d{2}[/\-\.]\d{2}[/\-\.]\d{4})',
        r'(?:data\s+de\s+vencimento)[:\s]+(\d{2}[/\-\.]\d{2}[/\-\.]\d{4})',
        r'(\d{2}[/\-\.]\d{2}[/\-\.]\d{4})',
    ]
    for p in padroes:
        for m in re.findall(p, texto, re.IGNORECASE):
            try:
                dt = datetime.strptime(re.sub(r'[-\.]','/',m), '%d/%m/%Y')
                if dt.year >= 2020: return dt.strftime('%Y-%m-%d')
            except: pass
    return None

def extrair_beneficiario(texto):
    for p in [
        r'(?:benefici[aá]rio|cedente|favorecido)[:\s]+([A-ZÀ-Ú\s\.,&]{5,60})',
        r'(?:nome)[:\s]+([A-ZÀ-Ú\s\.,&]{5,60})',
    ]:
        m = re.findall(p, texto, re.IGNORECASE)
        if m and len(m[0].strip()) > 3: return m[0].strip()[:40]
    return "FORNECEDOR"

def extrair_dados_pdf(caminho):
    texto = ""
    try:
        with pdfplumber.open(caminho) as pdf:
            for pg in pdf.pages:
                texto += (pg.extract_text() or "") + "\n"
    except: pass

    if len(texto.strip()) < 50:
        try:
            doc = fitz.open(caminho)
            for pg in doc: texto += pg.get_text() + "\n"
            doc.close()
        except: pass

    if not texto.strip():
        return {"erro": "Não foi possível extrair texto do PDF"}

    cod, linha = extrair_codigo_barras(texto)
    return {
        "codigo_barras":   cod,
        "linha_digitavel": linha,
        "valor":           extrair_valor(texto),
        "vencimento":      extrair_vencimento(texto),
        "beneficiario":    extrair_beneficiario(texto),
    }

# =====================================================
# GERADOR CNAB 240
# =====================================================

def fn(value, length):
    return str(int(value) if isinstance(value, float) else value).zfill(length)[:length]

def ft(value, length):
    v = re.sub(r'[^A-Z0-9 /\-.]', '', str(value or "").upper())
    return v[:length].ljust(length)

def header_arquivo():
    now = datetime.now()
    L  = "341" + "0000" + "0" + " "*9
    L += "2" + fn(EMPRESA["cnpj"],14) + " "*20 + "0"
    L += fn(EMPRESA["agencia"],4) + " " + "0"*7
    L += fn(EMPRESA["conta"],5) + " " + EMPRESA["conta_dac"]
    L += ft(EMPRESA["nome"],30) + ft("BANCO ITAU SA",30) + " "*10
    L += "1" + now.strftime("%d%m%Y") + now.strftime("%H%M%S")
    L += "000001" + "040" + "00000" + " "*20
    return L[:240].ljust(240)

def header_lote(lote, data_pag):
    L  = "341" + fn(lote,4) + "1" + "C" + "98" + "00" + "040" + " "
    L += "2" + fn(EMPRESA["cnpj"],15) + " "*20 + "0"
    L += fn(EMPRESA["agencia"],4) + " " + "0"*7
    L += fn(EMPRESA["conta"],5) + " " + EMPRESA["conta_dac"]
    L += ft(EMPRESA["nome"],30) + " "*40
    L += "00000001" + datetime.now().strftime("%d%m%Y")
    L += data_pag.replace("-","")[:8].ljust(8,"0") + " "*33
    return L[:240].ljust(240)

def seg_j(reg, lote, b):
    try:
        dv = datetime.strptime(b["vencimento"],"%Y-%m-%d").strftime("%d%m%Y")
    except:
        dv = "00000000"
    try:
        dp = datetime.strptime(b["data_pagamento"],"%Y-%m-%d").strftime("%d%m%Y")
    except:
        dp = "00000000"
    vc = int(round(float(b.get("valor",0))*100))
    L  = "341" + fn(lote,4) + "3" + fn(reg,5) + "J" + "0" + "00"
    L += fn(b.get("codigo_barras","0"),44)
    L += ft(b.get("beneficiario","FORNECEDOR"),30)
    L += dv + fn(vc,15) + fn(0,15) + fn(0,15)
    L += dp + fn(vc,15)
    L += ft(b.get("num_doc",""),20) + "09" + "  " + "000"
    return L[:240].ljust(240)

def seg_j52(reg, lote, b):
    L  = "341" + fn(lote,4) + "3" + fn(reg,5) + "J" + "0" + "00" + "52"
    L += "2" + fn(EMPRESA["cnpj"],15) + ft(EMPRESA["nome"],30)
    L += "0" + "0"*15 + ft(b.get("beneficiario","FORNECEDOR"),30)
    L += "0" + "0"*15 + " "*30
    L += fn(0,15) + " "*10
    return L[:240].ljust(240)

def trailer_lote(lote, qtd, soma):
    L  = "341" + fn(lote,4) + "5" + " "*9
    L += fn(qtd,6) + fn(0,6) + fn(int(soma*100),17)
    L += fn(0,6) + fn(0,17) + "0"*46 + " "*8 + " "*117
    return L[:240].ljust(240)

def trailer_arquivo(qtd_lotes, qtd_regs):
    L  = "341" + "9999" + "9" + " "*9
    L += fn(qtd_lotes,6) + fn(qtd_regs,6) + "0"*6 + " "*205
    return L[:240].ljust(240)

def gerar_cnab(boletos, data_pag_padrao):
    if isinstance(data_pag_padrao, date):
        data_pag_padrao = data_pag_padrao.strftime("%Y-%m-%d")

    linhas = [header_arquivo(), header_lote(1, data_pag_padrao)]
    reg = 1
    soma = 0.0

    for b in boletos:
        b["data_pagamento"] = b.get("data_pagamento") or data_pag_padrao
        linhas.append(seg_j(reg, 1, b));   reg += 1
        linhas.append(seg_j52(reg, 1, b)); reg += 1
        soma += float(b.get("valor", 0))

    qtd_lote = len(linhas) - 1 + 1 + 1   # header(1) + detalhes + trailer_lote + trailer_arq
    linhas.append(trailer_lote(1, qtd_lote, soma))
    linhas.append(trailer_arquivo(1, len(linhas)+1))
    return "\r\n".join(linhas) + "\r\n"

# =====================================================
# HTML EMBUTIDO
# =====================================================
HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CNAB 240 — Herbalife</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
:root{--verde:#00A651;--verde-esc:#007A3D;--verde-clr:#E6F6EE;--cinza-900:#111418;--cinza-700:#2C3340;--cinza-500:#6B7585;--cinza-200:#E4E8EE;--cinza-100:#F4F6F9;--branco:#FFFFFF;--erro:#D93025;--radius:10px;--sombra:0 2px 12px rgba(0,0,0,.08);}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Inter',sans-serif;background:var(--cinza-100);color:var(--cinza-900);min-height:100vh;}
header{background:var(--cinza-900);padding:0 32px;height:60px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;}
.logo{display:flex;align-items:center;gap:10px;color:var(--branco);font-weight:700;font-size:15px;}
.logo-dot{width:8px;height:8px;background:var(--verde);border-radius:50%;}
.badge{font-size:11px;color:var(--cinza-500);font-family:'JetBrains Mono',monospace;}
main{max-width:1100px;margin:0 auto;padding:32px 24px 64px;}
.steps{display:flex;background:var(--branco);border-radius:var(--radius);box-shadow:var(--sombra);overflow:hidden;margin-bottom:36px;}
.step{flex:1;padding:16px 20px;display:flex;align-items:center;gap:12px;border-right:1px solid var(--cinza-200);transition:background .2s;}
.step:last-child{border-right:none;}
.step.active{background:var(--verde-clr);}
.step.done{background:var(--cinza-100);}
.step-num{width:28px;height:28px;border-radius:50%;background:var(--cinza-200);color:var(--cinza-500);font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:all .2s;}
.step.active .step-num,.step.done .step-num{background:var(--verde);color:var(--branco);}
.step-label{font-size:13px;font-weight:500;color:var(--cinza-700);}
.step.active .step-label{color:var(--verde-esc);font-weight:600;}
.card{background:var(--branco);border-radius:var(--radius);box-shadow:var(--sombra);padding:28px;margin-bottom:20px;}
.card-title{font-size:15px;font-weight:600;margin-bottom:4px;}
.card-sub{font-size:13px;color:var(--cinza-500);margin-bottom:20px;}
.upload-zone{border:2px dashed var(--cinza-200);border-radius:var(--radius);padding:48px 24px;text-align:center;cursor:pointer;transition:all .2s;background:var(--cinza-100);}
.upload-zone:hover,.upload-zone.dragover{border-color:var(--verde);background:var(--verde-clr);}
.upload-zone input{display:none;}
.upload-icon{font-size:40px;margin-bottom:12px;display:block;}
.upload-title{font-size:15px;font-weight:600;color:var(--cinza-700);margin-bottom:4px;}
.upload-hint{font-size:13px;color:var(--cinza-500);}
.pag-global{display:flex;align-items:flex-end;gap:16px;padding:20px;background:var(--cinza-100);border-radius:var(--radius);margin-bottom:20px;border:1px solid var(--cinza-200);}
.fg{display:flex;flex-direction:column;gap:6px;flex:1;}
.fg label{font-size:12px;font-weight:600;color:var(--cinza-500);text-transform:uppercase;letter-spacing:.5px;}
.fg input{padding:10px 14px;border:1.5px solid var(--cinza-200);border-radius:8px;font-size:14px;font-family:'Inter',sans-serif;color:var(--cinza-900);background:var(--branco);outline:none;transition:border-color .15s;}
.fg input:focus{border-color:var(--verde);}
.btn{display:inline-flex;align-items:center;gap:8px;padding:10px 20px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;border:none;transition:all .15s;font-family:'Inter',sans-serif;white-space:nowrap;}
.btn-p{background:var(--verde);color:var(--branco);}
.btn-p:hover{background:var(--verde-esc);}
.btn-p:disabled{background:var(--cinza-200);color:var(--cinza-500);cursor:not-allowed;}
.btn-o{background:transparent;color:var(--cinza-700);border:1.5px solid var(--cinza-200);}
.btn-o:hover{border-color:var(--cinza-500);}
.btn-d{background:transparent;color:var(--erro);border:1.5px solid transparent;padding:6px 10px;font-size:12px;}
.btn-d:hover{background:#FFF0EE;}
.tabela-wrap{overflow-x:auto;border-radius:var(--radius);border:1px solid var(--cinza-200);}
table{width:100%;border-collapse:collapse;font-size:13px;}
thead th{background:var(--cinza-100);padding:12px 14px;text-align:left;font-size:11px;font-weight:600;color:var(--cinza-500);text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--cinza-200);white-space:nowrap;}
tbody tr{border-bottom:1px solid var(--cinza-200);transition:background .1s;}
tbody tr:last-child{border-bottom:none;}
tbody tr:hover{background:var(--cinza-100);}
tbody td{padding:12px 14px;vertical-align:middle;}
.td-arq{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--cinza-500);max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.td-bar{font-family:'JetBrains Mono',monospace;font-size:11px;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--cinza-700);}
input.te{border:1.5px solid transparent;background:transparent;padding:6px 8px;border-radius:6px;font-size:13px;font-family:'Inter',sans-serif;color:var(--cinza-900);width:100%;min-width:90px;outline:none;transition:all .15s;}
input.te:hover{background:var(--cinza-100);border-color:var(--cinza-200);}
input.te:focus{background:var(--branco);border-color:var(--verde);box-shadow:0 0 0 3px rgba(0,166,81,.1);}
.status{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;white-space:nowrap;}
.s-ok{background:var(--verde-clr);color:var(--verde-esc);}
.s-av{background:#FFF8E6;color:#A0650A;}
.s-er{background:#FFF0EE;color:var(--erro);}
.acoes{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:20px;}
.resumo{font-size:13px;color:var(--cinza-500);}
.resumo strong{color:var(--cinza-900);font-size:16px;}
.loading{display:none;flex-direction:column;align-items:center;gap:16px;padding:48px;}
.spinner{width:36px;height:36px;border:3px solid var(--cinza-200);border-top-color:var(--verde);border-radius:50%;animation:spin .7s linear infinite;}
@keyframes spin{to{transform:rotate(360deg);}}
.alerta{display:none;padding:14px 18px;border-radius:var(--radius);font-size:13px;font-weight:500;margin-bottom:16px;}
.al-er{background:#FFF0EE;color:var(--erro);border:1px solid #FFCCC7;}
.al-ok{background:var(--verde-clr);color:var(--verde-esc);border:1px solid #B3E6CC;}
.sucesso-box{display:none;flex-direction:column;align-items:center;gap:12px;padding:40px;text-align:center;}
.suc-icon{font-size:56px;}
.suc-titulo{font-size:20px;font-weight:700;color:var(--verde-esc);}
.suc-sub{font-size:14px;color:var(--cinza-500);}
.secao{display:none;}
.secao.ativa{display:block;}
.dica{background:#FFFDF0;border:1px solid #F5E6A0;border-radius:var(--radius);padding:16px 20px;font-size:13px;color:#6B5500;font-weight:500;}
@media(max-width:700px){.steps{flex-direction:column;}.step{border-right:none;border-bottom:1px solid var(--cinza-200);}.pag-global{flex-direction:column;}.acoes{flex-direction:column;align-items:stretch;}}
</style>
</head>
<body>
<header>
  <div class="logo"><div class="logo-dot"></div>CNAB 240 — Pagamento de Fornecedores</div>
  <span class="badge">Herbalife · AG 0786 · CC 12855-3</span>
</header>
<main>
  <div class="steps">
    <div class="step active" id="step1"><div class="step-num">1</div><div class="step-label">Anexar PDFs</div></div>
    <div class="step" id="step2"><div class="step-num">2</div><div class="step-label">Revisar boletos</div></div>
    <div class="step" id="step3"><div class="step-num">3</div><div class="step-label">Gerar CNAB</div></div>
  </div>

  <!-- UPLOAD -->
  <div class="secao ativa" id="secao-upload">
    <div class="card">
      <div class="card-title">Anexar boletos em PDF</div>
      <div class="card-sub">Selecione um ou mais PDFs de boletos de fornecedores.</div>
      <div class="upload-zone" id="uploadZone">
        <span class="upload-icon">📄</span>
        <div class="upload-title">Clique ou arraste os PDFs aqui</div>
        <div class="upload-hint">Aceita múltiplos arquivos · Formato PDF · Máx. 50 MB total</div>
        <input type="file" id="inputPdf" multiple accept=".pdf">
      </div>
      <div id="listaArqs" style="margin-top:16px;display:none;">
        <div style="font-size:12px;font-weight:600;color:var(--cinza-500);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">Arquivos selecionados</div>
        <div id="arquivosLista"></div>
      </div>
      <div style="margin-top:24px;display:flex;justify-content:flex-end;">
        <button class="btn btn-p" id="btnProcessar" disabled onclick="processarPdfs()">⚡ Processar boletos</button>
      </div>
    </div>
    <div class="dica">💡 <strong>Como funciona:</strong> O sistema lê automaticamente o código de barras, valor e vencimento de cada PDF. Você revisa e ajusta antes de gerar o CNAB.</div>
  </div>

  <!-- LOADING -->
  <div class="loading" id="loading">
    <div class="spinner"></div>
    <div style="font-size:14px;color:var(--cinza-500);">Lendo boletos, aguarde...</div>
  </div>

  <!-- REVISÃO -->
  <div class="secao" id="secao-revisao">
    <div id="alertaErro" class="alerta al-er"></div>
    <div id="alertaOk" class="alerta al-ok"></div>
    <div class="pag-global">
      <div class="fg">
        <label>📅 Data de pagamento para todos os boletos</label>
        <input type="date" id="dataPagGlobal" onchange="aplicarDataGlobal()">
      </div>
      <button class="btn btn-o" onclick="aplicarDataGlobal()">Aplicar a todos</button>
    </div>
    <div class="card">
      <div class="card-title">Revisar dados extraídos</div>
      <div class="card-sub">Confira e edite os campos se necessário antes de gerar o CNAB.</div>
      <div class="tabela-wrap">
        <table>
          <thead><tr>
            <th>Arquivo</th><th>Beneficiário</th><th>Cód. barras</th>
            <th>Valor (R$)</th><th>Vencimento</th><th>Data pagamento</th><th>Status</th><th></th>
          </tr></thead>
          <tbody id="tabelaCorpo"></tbody>
        </table>
      </div>
      <div class="acoes">
        <div class="resumo" id="resumo"></div>
        <div style="display:flex;gap:10px;">
          <button class="btn btn-o" onclick="voltarUpload()">← Voltar</button>
          <button class="btn btn-p" id="btnGerar" disabled onclick="gerarCnab()">📥 Gerar CNAB 240</button>
        </div>
      </div>
    </div>
  </div>

  <!-- SUCESSO -->
  <div class="secao" id="secao-sucesso">
    <div class="card">
      <div class="sucesso-box" id="sucessoBox">
        <div class="suc-icon">✅</div>
        <div class="suc-titulo">Arquivo CNAB 240 gerado!</div>
        <div class="suc-sub" id="sucessoSub"></div>
        <button class="btn btn-o" style="margin-top:8px;" onclick="novoProcesso()">+ Novo processo</button>
      </div>
    </div>
  </div>
</main>

<script>
let boletos = [], arqsSel = [];
const uploadZone = document.getElementById('uploadZone');
const inputPdf   = document.getElementById('inputPdf');

uploadZone.addEventListener('click', () => inputPdf.click());
uploadZone.addEventListener('dragover',  e => { e.preventDefault(); uploadZone.classList.add('dragover'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
uploadZone.addEventListener('drop', e => {
  e.preventDefault(); uploadZone.classList.remove('dragover');
  const f = Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (f.length) setArqs(f);
});
inputPdf.addEventListener('change', () => setArqs(Array.from(inputPdf.files)));

function setArqs(files) {
  arqsSel = files;
  document.getElementById('listaArqs').style.display = 'block';
  document.getElementById('arquivosLista').innerHTML = files.map(f =>
    `<div style="padding:8px 12px;background:var(--cinza-100);border-radius:6px;margin-bottom:6px;font-size:13px;display:flex;align-items:center;gap:8px;">
      <span>📄</span><strong>${f.name}</strong>
      <span style="color:var(--cinza-500);margin-left:auto;">${(f.size/1024).toFixed(0)} KB</span>
    </div>`).join('');
  document.getElementById('btnProcessar').disabled = false;
}

async function processarPdfs() {
  if (!arqsSel.length) return;
  mostrarLoading(true);
  const form = new FormData();
  arqsSel.forEach(f => form.append('pdfs', f));
  try {
    const r = await fetch('/extrair-pdfs', { method:'POST', body:form });
    const d = await r.json();
    boletos = d.map((x,i) => ({
      id:i, arquivo:x.arquivo, beneficiario:x.beneficiario||'',
      codigo_barras:x.codigo_barras||'', valor:x.valor||'',
      vencimento:x.vencimento||'', data_pagamento:'', erro:x.erro||null
    }));
    mostrarLoading(false);
    irSecao('revisao');
    renderTabela();
    atualizarResumo();
  } catch(e) {
    mostrarLoading(false);
    alert('Erro ao processar: ' + e.message);
  }
}

function renderTabela() {
  document.getElementById('tabelaCorpo').innerHTML = boletos.map(b => `
    <tr id="row-${b.id}">
      <td class="td-arq" title="${b.arquivo}">${b.arquivo}</td>
      <td><input class="te" value="${b.beneficiario}" onchange="upd(${b.id},'beneficiario',this.value)" style="min-width:150px;"></td>
      <td class="td-bar" title="${b.codigo_barras}">${b.codigo_barras ? b.codigo_barras.substring(0,20)+'…' : '<span style="color:var(--erro)">Não encontrado</span>'}</td>
      <td><input class="te" type="number" step="0.01" value="${b.valor}" onchange="upd(${b.id},'valor',this.value)" oninput="atualizarResumo()" style="min-width:100px;"></td>
      <td><input class="te" type="date" value="${b.vencimento}" onchange="upd(${b.id},'vencimento',this.value)"></td>
      <td><input class="te" type="date" value="${b.data_pagamento}" onchange="upd(${b.id},'data_pagamento',this.value)" id="dp-${b.id}"></td>
      <td>${badge(b)}</td>
      <td><button class="btn btn-d" onclick="remover(${b.id})">✕</button></td>
    </tr>`).join('');
}

function badge(b) {
  if (b.erro)              return '<span class="status s-er">⚠ Erro</span>';
  if (!b.codigo_barras)    return '<span class="status s-av">⚠ Sem código</span>';
  if (!b.valor)            return '<span class="status s-av">⚠ Sem valor</span>';
  if (!b.data_pagamento)   return '<span class="status s-av">⚠ Sem data pag.</span>';
  return '<span class="status s-ok">✓ Pronto</span>';
}

function upd(id, campo, val) {
  const b = boletos.find(x => x.id===id);
  if (b) { b[campo]=val; document.querySelector(`#row-${id} td:nth-child(7)`).innerHTML=badge(b); atualizarResumo(); }
}

function remover(id) {
  boletos = boletos.filter(b => b.id!==id);
  renderTabela(); atualizarResumo();
}

function aplicarDataGlobal() {
  const d = document.getElementById('dataPagGlobal').value;
  if (!d) return;
  boletos.forEach(b => { b.data_pagamento=d; const el=document.getElementById(`dp-${b.id}`); if(el) el.value=d; document.querySelector(`#row-${b.id} td:nth-child(7)`).innerHTML=badge(b); });
  atualizarResumo();
}

function atualizarResumo() {
  const total   = boletos.reduce((s,b)=>s+(parseFloat(b.valor)||0),0);
  const prontos = boletos.filter(b=>b.codigo_barras&&b.valor&&b.data_pagamento).length;
  document.getElementById('resumo').innerHTML = `${boletos.length} boleto(s) · ${prontos} prontos · Total: <strong>R$ ${total.toLocaleString('pt-BR',{minimumFractionDigits:2})}</strong>`;
  document.getElementById('btnGerar').disabled = prontos===0;
}

async function gerarCnab() {
  const prontos = boletos.filter(b=>b.codigo_barras&&b.valor&&b.data_pagamento);
  if (!prontos.length) { alerta('Nenhum boleto pronto.','er'); return; }
  const dataPag = document.getElementById('dataPagGlobal').value || prontos[0].data_pagamento;
  try {
    const r = await fetch('/gerar-cnab',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({boletos:prontos,data_pagamento:dataPag})});
    if (!r.ok) { const e=await r.json(); alerta('Erro: '+(e.erro||'desconhecido'),'er'); return; }
    const blob = await r.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href=url; a.download=`remessa_cnab240_${new Date().toISOString().slice(0,10).replace(/-/g,'')}.txt`;
    a.click(); URL.revokeObjectURL(url);
    const total = prontos.reduce((s,b)=>s+(parseFloat(b.valor)||0),0);
    document.getElementById('sucessoSub').textContent = `${prontos.length} boleto(s) · Total R$ ${total.toLocaleString('pt-BR',{minimumFractionDigits:2})} · Arquivo baixado automaticamente`;
    document.getElementById('sucessoBox').style.display='flex';
    irSecao('sucesso');
  } catch(e) { alerta('Erro de conexão: '+e.message,'er'); }
}

function mostrarLoading(s) {
  document.getElementById('loading').style.display=s?'flex':'none';
  if(s) document.getElementById('secao-upload').classList.remove('ativa');
}
function irSecao(nome) {
  ['upload','revisao','sucesso'].forEach(s=>document.getElementById(`secao-${s}`).classList.toggle('ativa',s===nome));
  ['step1','step2','step3'].forEach((s,i)=>{const el=document.getElementById(s);el.classList.remove('active','done');const idx=['upload','revisao','sucesso'].indexOf(nome);if(i<idx)el.classList.add('done');if(i===idx)el.classList.add('active');});
}
function voltarUpload(){irSecao('upload');}
function novoProcesso(){boletos=[];arqsSel=[];document.getElementById('inputPdf').value='';document.getElementById('listaArqs').style.display='none';document.getElementById('btnProcessar').disabled=true;document.getElementById('dataPagGlobal').value='';document.getElementById('sucessoBox').style.display='none';irSecao('upload');}
function alerta(msg,tipo){const el=document.getElementById(tipo==='er'?'alertaErro':'alertaOk');el.textContent=msg;el.style.display='block';setTimeout(()=>el.style.display='none',5000);}
document.getElementById('dataPagGlobal').min=new Date().toISOString().split('T')[0];
</script>
</body>
</html>"""

# =====================================================
# ROTAS
# =====================================================

@app.route('/')
def index():
    return Response(HTML, mimetype='text/html')

@app.route('/extrair-pdfs', methods=['POST'])
def extrair_pdfs():
    if 'pdfs' not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    resultados = []
    for arq in request.files.getlist('pdfs'):
        if not arq.filename.lower().endswith('.pdf'):
            continue
        tmp = f"/tmp/cnab_{arq.filename}"
        arq.save(tmp)
        try:
            dados = extrair_dados_pdf(tmp)
            dados["arquivo"] = arq.filename
            resultados.append(dados)
        except Exception as e:
            resultados.append({"arquivo": arq.filename, "erro": str(e)})
        finally:
            if os.path.exists(tmp): os.remove(tmp)
    return jsonify(resultados)

@app.route('/gerar-cnab', methods=['POST'])
def gerar_cnab_route():
    dados     = request.json
    boletos   = dados.get("boletos", [])
    data_pag  = dados.get("data_pagamento")
    if not boletos:  return jsonify({"erro": "Nenhum boleto"}), 400
    if not data_pag: return jsonify({"erro": "Data de pagamento obrigatória"}), 400
    try:
        conteudo = gerar_cnab(boletos, data_pag)
        buf = io.BytesIO()
        buf.write(conteudo.encode('ascii', errors='replace'))
        buf.seek(0)
        nome = f"remessa_cnab240_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        return send_file(buf, as_attachment=True, download_name=nome, mimetype='text/plain')
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# =====================================================
# MAIN
# =====================================================
# RENDER ENTRY
    import webbrowser, threading
    print("=" * 50)
    print("  CNAB 240 — Herbalife — Pagamento Fornecedores")
    print("=" * 50)
    print("  Acesse: http://localhost:5000")
    print("  Para encerrar: Ctrl+C")
    print("=" * 50)
    threading.Timer(1.5, lambda: webbrowser.open('http://localhost:5000')).start()
    app.run(host='0.0.0.0', port=5000, debug=False)

# =====================================================
# ENTRY POINT — local e Render.com
# =====================================================
if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
