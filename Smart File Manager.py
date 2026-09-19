"""
Gerenciador de Arquivos Inteligente
-----------------------------------
BY: Alves Renan
"""

import hashlib
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

# ----------------------------------------------------------------------------
# Configurações
# ----------------------------------------------------------------------------
TITULO = "Gerenciador Inteligente"
MB = 1024 * 1024
ARQ_HISTORICO = ".gerenciador_historico.json"
PASTA_DUPLICADOS = "_Duplicados_Revisar"
PASTA_REVISAR = "_Revisar"
PASTAS_IGNORADAS = {"node_modules", "__pycache__", "venv", "$RECYCLE.BIN",
                    "System Volume Information", PASTA_DUPLICADOS, PASTA_REVISAR}

MODOS = ("Por tipo", "Por tipo e data (ano/mês)", "Por data (ano/mês)")
CRITERIOS = ("Grandes ou antigos", "Somente grandes", "Somente antigos", "Grandes e antigos")

CATEGORIAS = {
    "Imagens": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".tiff", ".heic", ".ico", ".raw"},
    "Vídeos": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "Áudios": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"},
    "Documentos": {".pdf", ".doc", ".docx", ".txt", ".odt", ".rtf", ".md", ".epub"},
    "Planilhas": {".xls", ".xlsx", ".csv", ".ods"},
    "Apresentações": {".ppt", ".pptx", ".odp", ".key"},
    "Compactados": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Código": {".py", ".js", ".ts", ".html", ".css", ".java", ".c", ".cpp", ".cs", ".go",
               ".rs", ".php", ".json", ".xml", ".yml", ".yaml", ".sql", ".sh", ".bat", ".ipynb"},
    "Instaladores": {".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".apk", ".iso"},
    "Fontes": {".ttf", ".otf", ".woff", ".woff2"},
}
EXT_PARA_CATEGORIA = {ext: cat for cat, exts in CATEGORIAS.items() for ext in exts}

CORES = {"Imagens": "#38bdf8", "Vídeos": "#a78bfa", "Áudios": "#f472b6", "Documentos": "#22c55e",
         "Planilhas": "#84cc16", "Apresentações": "#f59e0b", "Compactados": "#fb923c",
         "Código": "#2dd4bf", "Instaladores": "#ef4444", "Fontes": "#c084fc", "Outros": "#64748b"}

# (categoria, regex no nome do arquivo, subpasta)
REGRAS_INTELIGENTES = [
    ("Imagens", r"screenshot|captura de tela|screen ?shot", "Capturas de tela"),
    ("Imagens", r"^(img|dsc|dscn|pxl|photo)[-_ ]?\d", "Fotos"),
    ("Documentos", r"nota fiscal|nf-?e|fatura|invoice|boleto|recibo|comprovante", "Financeiro"),
    ("Documentos", r"curr[ií]culo|\bcv\b|resume", "Currículos"),
    ("Documentos", r"contrato", "Contratos"),
]

# Paleta
BG, CARD, CARD_ALT, GRADE = "#0f172a", "#1e293b", "#152033", "#2b3a52"
TEXTO, TEXTO2 = "#e2e8f0", "#94a3b8"
AZUL, VERDE, AMARELO, VERMELHO = "#38bdf8", "#22c55e", "#eab308", "#ef4444"
FONTE = ("Segoe UI", 10)
FONTE_TITULO = ("Segoe UI", 10, "bold")


# ----------------------------------------------------------------------------
# Modelo e funções de apoio
# ----------------------------------------------------------------------------
@dataclass
class Arq:
    caminho: str
    nome: str
    ext: str
    tamanho: int
    mtime: float
    categoria: str

    @property
    def pasta(self) -> str:
        return os.path.dirname(self.caminho)


def fmt_bytes(n: float) -> str:
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{int(n)} B" if unidade == "B" else f"{n:.1f} {unidade}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_data(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")


def varrer(raiz, recursivo, progresso=None):
    """Percorre a pasta e devolve a lista de arquivos (ignora ocultos e links)."""
    arquivos = []
    for pasta, subs, nomes in os.walk(raiz):
        subs[:] = [s for s in subs if not s.startswith(".") and s not in PASTAS_IGNORADAS]
        if not recursivo:
            subs.clear()
        for nome in nomes:
            if nome.startswith("."):
                continue
            caminho = os.path.join(pasta, nome)
            try:
                if os.path.islink(caminho):
                    continue
                st = os.stat(caminho)
            except OSError:
                continue
            ext = os.path.splitext(nome)[1].lower()
            arquivos.append(Arq(caminho, nome, ext, st.st_size, st.st_mtime,
                                EXT_PARA_CATEGORIA.get(ext, "Outros")))
        if progresso:
            progresso(f"Lendo arquivos... {len(arquivos)} encontrados")
    return arquivos


def hash_arquivo(caminho, parcial=False):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        if parcial:
            h.update(f.read(65536))
        else:
            for bloco in iter(lambda: f.read(MB), b""):
                h.update(bloco)
    return h.hexdigest()


def achar_duplicados(arquivos, progresso=None):
    """Tamanho -> hash dos primeiros 64 KB -> hash completo (evita ler tudo à toa)."""
    por_tamanho = defaultdict(list)
    for a in arquivos:
        if a.tamanho > 0:
            por_tamanho[a.tamanho].append(a)
    candidatos = [g for g in por_tamanho.values() if len(g) > 1]
    total = sum(len(g) for g in candidatos)
    feitos = 0
    grupos = []
    for grupo in candidatos:
        por_parcial = defaultdict(list)
        for a in grupo:
            try:
                por_parcial[hash_arquivo(a.caminho, True)].append(a)
            except OSError:
                pass
            feitos += 1
            if progresso:
                progresso(f"Comparando conteúdo... {feitos}/{total}")
        for sub in por_parcial.values():
            if len(sub) < 2:
                continue
            por_hash = defaultdict(list)
            for a in sub:
                try:
                    por_hash[hash_arquivo(a.caminho)].append(a)
                except OSError:
                    pass
            for g in por_hash.values():
                if len(g) > 1:
                    grupos.append(sorted(g, key=lambda x: x.mtime))  # mais antigo primeiro
    grupos.sort(key=lambda g: g[0].tamanho * (len(g) - 1), reverse=True)
    return grupos


def subcategoria(a):
    nome = a.nome.lower()
    for cat, padrao, sub in REGRAS_INTELIGENTES:
        if a.categoria == cat and re.search(padrao, nome):
            return sub
    return None


def destino_para(a, raiz, modo):
    partes = []
    if modo != MODOS[2]:
        partes.append(a.categoria)
        sub = subcategoria(a)
        if sub:
            partes.append(sub)
    if modo != MODOS[0]:
        d = datetime.fromtimestamp(a.mtime)
        partes += [str(d.year), f"{d.month:02d}"]
    return os.path.join(raiz, *partes, a.nome)


def caminho_livre(destino):
    if not os.path.exists(destino):
        return destino
    base, ext = os.path.splitext(destino)
    i = 1
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def mover_seguro(origem, destino):
    """Move sem sobrescrever nada (renomeia com (1), (2)... se necessário)."""
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    destino = caminho_livre(destino)
    shutil.move(origem, destino)
    return destino


def remover_vazias(pasta, raiz):
    raiz, pasta = os.path.abspath(raiz), os.path.abspath(pasta)
    while pasta != raiz and pasta.startswith(raiz + os.sep):
        try:
            os.rmdir(pasta)          # só remove se estiver vazia
        except OSError:
            break
        pasta = os.path.dirname(pasta)


# ---- Histórico (desfazer) ---------------------------------------------------
def ler_historico(raiz):
    try:
        with open(os.path.join(raiz, ARQ_HISTORICO), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def salvar_historico(raiz, hist):
    with open(os.path.join(raiz, ARQ_HISTORICO), "w", encoding="utf-8") as f:
        json.dump(hist[-50:], f, ensure_ascii=False, indent=1)


def registrar_operacao(raiz, tipo, movimentos):
    hist = ler_historico(raiz)
    hist.append({"data": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                 "tipo": tipo, "movimentos": movimentos})
    salvar_historico(raiz, hist)


def desfazer_ultima(raiz):
    hist = ler_historico(raiz)
    if not hist:
        return None
    op = hist.pop()
    ok = 0
    for origem, destino in reversed(op["movimentos"]):
        if os.path.exists(destino):
            try:
                mover_seguro(destino, origem)
                ok += 1
                remover_vazias(os.path.dirname(destino), raiz)
            except OSError:
                pass
    salvar_historico(raiz, hist)
    return op["tipo"], ok, len(op["movimentos"])


def abrir_local(caminho):
    try:
        sistema = platform.system()
        if sistema == "Windows":
            subprocess.Popen(f'explorer /select,"{os.path.normpath(caminho)}"')
        elif sistema == "Darwin":
            subprocess.Popen(["open", "-R", caminho])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(caminho)])
    except OSError as erro:
        messagebox.showerror(TITULO, f"Não foi possível abrir a pasta:\n{erro}")


# ----------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------
class GerenciadorInteligente(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITULO)
        self.geometry("1120x740")
        self.minsize(980, 620)
        self.configure(bg=BG)

        self.raiz = ""
        self.arquivos: list[Arq] = []
        self.plano = []
        self.dup_arq, self.limp_arq, self.busca_arq = {}, {}, {}
        self.ocupado = False
        self.progresso_txt = ""
        self._stats_barra = []

        self._estilos()
        self._barra_topo()
        self._barra_status()

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        nomes = ("  Visão geral  ", "  Organizar  ", "  Duplicados  ", "  Limpeza  ", "  Buscar  ")
        self.abas = []
        for nome in nomes:
            aba = tk.Frame(self.nb, bg=BG)
            self.nb.add(aba, text=nome)
            self.abas.append(aba)

        self._aba_geral(self.abas[0])
        self._aba_organizar(self.abas[1])
        self._aba_duplicados(self.abas[2])
        self._aba_limpeza(self.abas[3])
        self._aba_busca(self.abas[4])

    # -------------------------------------------------------------- helpers UI
    def _estilos(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=CARD, foreground=TEXTO2,
                    padding=(14, 7), borderwidth=0, font=FONTE_TITULO)
        s.map("TNotebook.Tab", background=[("selected", AZUL)], foreground=[("selected", BG)])
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXTO,
                    rowheight=24, borderwidth=0, font=FONTE)
        s.configure("Treeview.Heading", background=CARD_ALT, foreground=AZUL,
                    relief="flat", font=FONTE_TITULO, padding=6)
        s.map("Treeview", background=[("selected", "#334155")], foreground=[("selected", "#ffffff")])
        s.map("Treeview.Heading", background=[("active", GRADE)])
        s.configure("Vertical.TScrollbar", background=GRADE, troughcolor=CARD,
                    bordercolor=CARD, arrowcolor=TEXTO2)
        s.configure("Azul.Horizontal.TProgressbar", troughcolor=CARD_ALT, background=AZUL,
                    lightcolor=AZUL, darkcolor=AZUL, bordercolor=CARD)
        s.configure("TCombobox", fieldbackground=CARD, background=CARD_ALT,
                    foreground=TEXTO, arrowcolor=TEXTO, bordercolor=GRADE)
        s.map("TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", TEXTO)],
              selectbackground=[("readonly", CARD)], selectforeground=[("readonly", TEXTO)])
        self.option_add("*TCombobox*Listbox.background", CARD)
        self.option_add("*TCombobox*Listbox.foreground", TEXTO)
        self.option_add("*TCombobox*Listbox.selectBackground", AZUL)
        self.option_add("*TCombobox*Listbox.selectForeground", BG)

    def _botao(self, master, texto, comando, cor=AZUL, fg=BG):
        return tk.Button(master, text=texto, command=comando, bg=cor, fg=fg,
                         activebackground=cor, activeforeground=fg, relief="flat", bd=0,
                         font=FONTE_TITULO, padx=12, pady=5, cursor="hand2")

    def _label(self, master, texto, fg=TEXTO2, **kw):
        return tk.Label(master, text=texto, bg=BG, fg=fg, font=FONTE, **kw)

    def _check(self, master, texto, var):
        return tk.Checkbutton(master, text=texto, variable=var, bg=BG, fg=TEXTO,
                              selectcolor=CARD, activebackground=BG,
                              activeforeground=TEXTO, font=FONTE)

    def _entrada(self, master, var, largura):
        return tk.Entry(master, textvariable=var, width=largura, bg=CARD, fg=TEXTO,
                        insertbackground=TEXTO, relief="flat", font=FONTE)

    def _tabela(self, master, colunas, titulos, larguras, ancoras=None,
                mostrar="headings", selecao="extended"):
        quadro = tk.Frame(master, bg=BG)
        quadro.pack(fill="both", expand=True)
        tree = ttk.Treeview(quadro, columns=colunas, show=mostrar, selectmode=selecao)
        sb = ttk.Scrollbar(quadro, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        for c, t, w in zip(colunas, titulos, larguras):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor=(ancoras or {}).get(c, "w"))
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return tree

    def _barra_topo(self):
        f = tk.Frame(self, bg=BG)
        f.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(f, text="🗂  Gerenciador de Arquivos Inteligente", bg=BG, fg=TEXTO,
                 font=("Segoe UI", 18, "bold")).pack(side="left")
        self._botao(f, "↩ Desfazer última", self._desfazer, "#334155", TEXTO).pack(side="right")
        self._botao(f, "🔄 Reescanear", self._escanear, "#334155", TEXTO).pack(side="right", padx=8)

        g = tk.Frame(self, bg=BG)
        g.pack(fill="x", padx=16, pady=(0, 10))
        self._botao(g, "📁 Escolher pasta", self._escolher_pasta).pack(side="left")
        self.lbl_pasta = self._label(g, "Nenhuma pasta selecionada")
        self.lbl_pasta.pack(side="left", padx=12)
        self.var_sub = tk.BooleanVar(value=True)
        self._check(g, "Incluir subpastas na análise", self.var_sub).pack(side="right")

    def _barra_status(self):
        f = tk.Frame(self, bg=CARD)
        f.pack(side="bottom", fill="x")
        self.lbl_status = tk.Label(f, text="Pronto.", bg=CARD, fg=TEXTO2, font=FONTE,
                                   anchor="w", padx=12, pady=6)
        self.lbl_status.pack(side="left", fill="x", expand=True)
        self.barra = ttk.Progressbar(f, mode="indeterminate", length=140,
                                     style="Azul.Horizontal.TProgressbar")
        self.barra.pack(side="right", padx=12)

    # ------------------------------------------------------ execução assíncrona
    def _progresso(self, texto):
        self.progresso_txt = texto

    def _async(self, tarefa, ao_concluir, status):
        if self.ocupado:
            messagebox.showinfo(TITULO, "Aguarde a operação atual terminar.")
            return
        self.ocupado = True
        self.progresso_txt = status
        self.barra.start(12)
        fila = queue.Queue()

        def trabalhador():
            try:
                fila.put(("ok", tarefa()))
            except Exception as erro:
                fila.put(("erro", erro))

        def verificar():
            try:
                tipo, valor = fila.get_nowait()
            except queue.Empty:
                self.lbl_status.config(text=self.progresso_txt)
                self.after(120, verificar)
                return
            self.barra.stop()
            self.ocupado = False
            if tipo == "ok":
                ao_concluir(valor)
            else:
                self.lbl_status.config(text="Erro.")
                messagebox.showerror(TITULO, f"Ocorreu um erro:\n{valor}")

        threading.Thread(target=trabalhador, daemon=True).start()
        verificar()

    def _tem_pasta(self):
        if not self.raiz:
            messagebox.showinfo(TITULO, "Escolha uma pasta primeiro.")
            return False
        return True

    # ------------------------------------------------------------ pasta / scan
    def _escolher_pasta(self):
        pasta = filedialog.askdirectory(title="Escolha a pasta para analisar")
        if not pasta:
            return
        pasta = os.path.abspath(pasta)
        if os.path.dirname(pasta) == pasta:
            messagebox.showwarning(TITULO, "Escolha uma pasta, não a raiz do disco.")
            return
        self.raiz = pasta
        self.lbl_pasta.config(text=pasta)
        self._escanear()

    def _escanear(self):
        if not self._tem_pasta():
            return
        recursivo = self.var_sub.get()
        raiz = self.raiz
        self._async(lambda: varrer(raiz, recursivo, self._progresso),
                    self._apos_escanear, "Lendo arquivos...")

    def _apos_escanear(self, arquivos):
        self.arquivos = arquivos
        self._limpar_resultados()
        self._atualizar_visao_geral()
        self._filtrar_busca()
        self.lbl_status.config(text=f"Pronto — {len(arquivos)} arquivos analisados.")

    def _limpar_resultados(self):
        for tree in (self.tree_org, self.tree_dup, self.tree_limp):
            tree.delete(*tree.get_children())
        self.plano = []
        self.dup_arq.clear()
        self.limp_arq.clear()
        for lbl in (self.lbl_previa, self.lbl_dup, self.lbl_limp):
            lbl.config(text="")

    # -------------------------------------------------------------- visão geral
    def _aba_geral(self, f):
        self.lbl_resumo = tk.Label(f, text="Escolha uma pasta para começar.", bg=BG, fg=TEXTO,
                                   font=("Segoe UI", 16, "bold"), anchor="w")
        self.lbl_resumo.pack(fill="x", pady=(10, 4))
        self.lbl_sugestoes = tk.Label(f, text="", bg=BG, fg=AMARELO, font=FONTE,
                                      justify="left", anchor="w")
        self.lbl_sugestoes.pack(fill="x", pady=(0, 8))
        self.canvas_barra = tk.Canvas(f, height=26, bg=CARD, highlightthickness=0)
        self.canvas_barra.pack(fill="x", pady=(0, 8))
        self.canvas_barra.bind("<Configure>", lambda _e: self._desenhar_barra())
        self.tree_cat = self._tabela(
            f, ("cat", "qtd", "tam", "pct"),
            ("Categoria", "Arquivos", "Tamanho", "% do espaço"), (240, 110, 130, 110),
            {"qtd": "e", "tam": "e", "pct": "e"}, selecao="browse")
        for cat, cor in CORES.items():
            self.tree_cat.tag_configure(cat, foreground=cor)

    def _desenhar_barra(self):
        c = self.canvas_barra
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        total = sum(t for _, t in self._stats_barra)
        if total <= 0 or w < 10:
            return
        x = 0
        for cat, tam in self._stats_barra:
            largura = w * tam / total
            c.create_rectangle(x, 0, x + largura, h, fill=CORES.get(cat, "#64748b"), outline="")
            x += largura

    def _sugestoes(self):
        linhas = []
        agora = time.time()
        soltos = [a for a in self.arquivos if a.pasta == self.raiz]
        if soltos:
            linhas.append(f"💡 {len(soltos)} arquivos soltos na pasta principal podem ser organizados.")
        grandes = [a for a in self.arquivos if a.tamanho >= 100 * MB]
        if grandes:
            linhas.append(f"💡 {len(grandes)} arquivos com 100 MB ou mais ocupam "
                          f"{fmt_bytes(sum(a.tamanho for a in grandes))} (aba Limpeza).")
        antigos = [a for a in self.arquivos if agora - a.mtime > 365 * 86400]
        if antigos:
            linhas.append(f"💡 {len(antigos)} arquivos não são modificados há mais de 1 ano.")
        inst = [a for a in self.arquivos if a.categoria == "Instaladores"]
        if inst:
            linhas.append(f"💡 {len(inst)} instaladores ({fmt_bytes(sum(a.tamanho for a in inst))}) "
                          "— geralmente podem ser removidos depois de usados.")
        contagem = defaultdict(int)
        for a in self.arquivos:
            if a.tamanho >= MB:
                contagem[a.tamanho] += 1
        provaveis = sum(c for c in contagem.values() if c > 1)
        if provaveis:
            linhas.append(f"💡 {provaveis} arquivos (≥ 1 MB) têm tamanho idêntico a outros "
                          "— confira a aba Duplicados.")
        return "\n".join(linhas)

    def _atualizar_visao_geral(self):
        stats = defaultdict(lambda: [0, 0])
        for a in self.arquivos:
            stats[a.categoria][0] += 1
            stats[a.categoria][1] += a.tamanho
        total = sum(v[1] for v in stats.values())
        ordenado = sorted(stats.items(), key=lambda kv: kv[1][1], reverse=True)
        self.tree_cat.delete(*self.tree_cat.get_children())
        for cat, (qtd, tam) in ordenado:
            pct = tam / total * 100 if total else 0
            self.tree_cat.insert("", "end", tags=(cat,),
                                 values=(cat, qtd, fmt_bytes(tam), f"{pct:.1f}%"))
        self._stats_barra = [(cat, v[1]) for cat, v in ordenado]
        self._desenhar_barra()
        nome = os.path.basename(self.raiz) or self.raiz
        self.lbl_resumo.config(text=f"{len(self.arquivos):,} arquivos  •  {fmt_bytes(total)}  "
                                    f"em “{nome}”".replace(",", "."))
        self.lbl_sugestoes.config(text=self._sugestoes())

    # ---------------------------------------------------------------- organizar
    def _aba_organizar(self, f):
        topo = tk.Frame(f, bg=BG)
        topo.pack(fill="x", pady=10)
        self._label(topo, "Modo:").pack(side="left")
        self.var_modo = tk.StringVar(value=MODOS[0])
        ttk.Combobox(topo, textvariable=self.var_modo, values=MODOS,
                     state="readonly", width=28).pack(side="left", padx=8)
        self.var_org_sub = tk.BooleanVar(value=False)
        self._check(topo, "Incluir arquivos de subpastas", self.var_org_sub).pack(side="left", padx=8)
        self._botao(topo, "Aplicar organização", self._aplicar_organizacao, VERDE).pack(side="right")
        self._botao(topo, "Gerar prévia", self._gerar_previa).pack(side="right", padx=8)
        self.lbl_previa = self._label(f, "", anchor="w")
        self.lbl_previa.pack(fill="x", pady=(0, 6))
        self.tree_org = self._tabela(f, ("nome", "de", "para"),
                                     ("Arquivo", "Pasta atual", "Destino"), (280, 300, 380))

    def _gerar_previa(self):
        if not self._tem_pasta():
            return
        modo = self.var_modo.get()
        self.plano = []
        self.tree_org.delete(*self.tree_org.get_children())
        for a in self.arquivos:
            if not self.var_org_sub.get() and a.pasta != self.raiz:
                continue
            destino = destino_para(a, self.raiz, modo)
            if os.path.normcase(os.path.dirname(destino)) == os.path.normcase(a.pasta):
                continue  # já está no lugar certo
            self.plano.append((a, destino))
            self.tree_org.insert("", "end", values=(
                a.nome, os.path.relpath(a.pasta, self.raiz),
                os.path.relpath(os.path.dirname(destino), self.raiz)))
        self.lbl_previa.config(
            text=f"{len(self.plano)} arquivos serão movidos. Nada acontece até você clicar em “Aplicar”.")

    def _aplicar_organizacao(self):
        if not self.plano:
            messagebox.showinfo(TITULO, "Gere a prévia primeiro.")
            return
        if not messagebox.askyesno(
                TITULO, f"Mover {len(self.plano)} arquivos conforme a prévia?\n\n"
                        "Você poderá desfazer depois em “Desfazer última”."):
            return
        plano, raiz = list(self.plano), self.raiz

        def tarefa():
            movs, erros = [], []
            for a, destino in plano:
                try:
                    movs.append([a.caminho, mover_seguro(a.caminho, destino)])
                except OSError as e:
                    erros.append(f"{a.nome}: {e}")
            if movs:
                registrar_operacao(raiz, "Organização", movs)
            return len(movs), erros

        self._async(tarefa, self._apos_organizar, "Organizando arquivos...")

    def _apos_organizar(self, resultado):
        n, erros = resultado
        msg = f"{n} arquivos organizados."
        if erros:
            msg += f"\n\n{len(erros)} falharam:\n" + "\n".join(erros[:5])
        messagebox.showinfo(TITULO, msg)
        self._escanear()

    # --------------------------------------------------------------- duplicados
    def _aba_duplicados(self, f):
        topo = tk.Frame(f, bg=BG)
        topo.pack(fill="x", pady=10)
        self._botao(topo, "🔍 Procurar duplicados", self._procurar_duplicados).pack(side="left")
        self._botao(topo, "Marcar cópias (manter a mais antiga)", self._marcar_copias,
                    "#334155", TEXTO).pack(side="left", padx=8)
        self._botao(topo, "Mover selecionadas p/ quarentena", self._mover_duplicados,
                    VERDE).pack(side="right")
        self.lbl_dup = self._label(f, "", anchor="w")
        self.lbl_dup.pack(fill="x", pady=(0, 6))
        self.tree_dup = self._tabela(f, ("tam", "data", "pasta"), ("Tamanho", "Modificado", "Pasta"),
                                     (100, 100, 480), {"tam": "e"}, mostrar="tree headings")
        self.tree_dup.heading("#0", text="Arquivo")
        self.tree_dup.column("#0", width=380)
        self.tree_dup.bind("<Double-1>", lambda _e: self._abrir_selecionado(self.tree_dup, self.dup_arq))

    def _procurar_duplicados(self):
        if not self._tem_pasta():
            return
        if not self.arquivos:
            messagebox.showinfo(TITULO, "Nenhum arquivo na pasta.")
            return
        arqs = list(self.arquivos)
        self._async(lambda: achar_duplicados(arqs, self._progresso),
                    self._mostrar_duplicados, "Comparando conteúdo dos arquivos...")

    def _mostrar_duplicados(self, grupos):
        self.tree_dup.delete(*self.tree_dup.get_children())
        self.dup_arq.clear()
        desperdicio = 0
        for n, g in enumerate(grupos, 1):
            sobra = g[0].tamanho * (len(g) - 1)
            desperdicio += sobra
            pai = self.tree_dup.insert(
                "", "end", iid=f"g{n}", open=True,
                text=f"Grupo {n} · {len(g)} cópias · {fmt_bytes(sobra)} desperdiçados")
            for j, a in enumerate(g):
                iid = f"g{n}a{j}"
                self.dup_arq[iid] = a
                self.tree_dup.insert(pai, "end", iid=iid, text=a.nome,
                                     values=(fmt_bytes(a.tamanho), fmt_data(a.mtime), a.pasta))
        self.lbl_dup.config(text=f"{len(grupos)} grupos de duplicados • "
                                 f"{fmt_bytes(desperdicio)} podem ser recuperados.")
        self.lbl_status.config(text="Busca de duplicados concluída.")

    def _marcar_copias(self):
        sel = []
        for pai in self.tree_dup.get_children():
            sel.extend(self.tree_dup.get_children(pai)[1:])   # mantém o 1º (mais antigo)
        self.tree_dup.selection_set(sel)

    def _mover_duplicados(self):
        sel = {i for i in self.tree_dup.selection() if i in self.dup_arq}
        for pai in self.tree_dup.get_children():
            filhos = self.tree_dup.get_children(pai)
            if filhos and all(f in sel for f in filhos):
                messagebox.showwarning(TITULO, "Todas as cópias de um mesmo grupo estão selecionadas.\n"
                                               "Deixe pelo menos uma de cada grupo.")
                return
        self._mover_para([self.dup_arq[i] for i in sel], PASTA_DUPLICADOS,
                         "Duplicados", self._apos_mover_dup)

    def _apos_mover_dup(self, movidos):
        self._remover_da_tabela(self.tree_dup, self.dup_arq, movidos)
        for pai in self.tree_dup.get_children():
            filhos = self.tree_dup.get_children(pai)
            if len(filhos) < 2:                # grupo resolvido
                for f in filhos:
                    self.dup_arq.pop(f, None)
                self.tree_dup.delete(pai)

    # ------------------------------------------------------------------ limpeza
    def _aba_limpeza(self, f):
        topo = tk.Frame(f, bg=BG)
        topo.pack(fill="x", pady=10)
        self.var_mb = tk.StringVar(value="100")
        self.var_dias = tk.StringVar(value="365")
        self.var_criterio = tk.StringVar(value=CRITERIOS[0])
        self._label(topo, "Tamanho mín. (MB):").pack(side="left")
        self._entrada(topo, self.var_mb, 7).pack(side="left", padx=(6, 12), ipady=3)
        self._label(topo, "Sem modificar há (dias):").pack(side="left")
        self._entrada(topo, self.var_dias, 7).pack(side="left", padx=(6, 12), ipady=3)
        ttk.Combobox(topo, textvariable=self.var_criterio, values=CRITERIOS,
                     state="readonly", width=20).pack(side="left", padx=(0, 12))
        self._botao(topo, "Buscar", self._buscar_limpeza).pack(side="left")
        self._botao(topo, "Mover selecionados p/ _Revisar", self._mover_limpeza,
                    VERDE).pack(side="right")
        self._botao(topo, "Abrir local", lambda: self._abrir_selecionado(self.tree_limp, self.limp_arq),
                    "#334155", TEXTO).pack(side="right", padx=8)
        self.lbl_limp = self._label(f, "", anchor="w")
        self.lbl_limp.pack(fill="x", pady=(0, 6))
        self.tree_limp = self._tabela(
            f, ("nome", "cat", "tam", "data", "pasta"),
            ("Arquivo", "Categoria", "Tamanho", "Modificado", "Pasta"), (260, 110, 90, 100, 380),
            {"tam": "e"})
        self.tree_limp.bind("<Double-1>", lambda _e: self._abrir_selecionado(self.tree_limp, self.limp_arq))

    def _buscar_limpeza(self):
        if not self._tem_pasta():
            return
        try:
            min_bytes = float(self.var_mb.get().replace(",", ".")) * MB
            min_dias = float(self.var_dias.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning(TITULO, "Informe números válidos para tamanho e dias.")
            return
        limite = time.time() - min_dias * 86400
        crit = self.var_criterio.get()

        def escolhido(a):
            grande, antigo = a.tamanho >= min_bytes, a.mtime <= limite
            if crit == CRITERIOS[0]:
                return grande or antigo
            if crit == CRITERIOS[1]:
                return grande
            if crit == CRITERIOS[2]:
                return antigo
            return grande and antigo

        achados = sorted((a for a in self.arquivos if escolhido(a)),
                         key=lambda a: a.tamanho, reverse=True)
        self.tree_limp.delete(*self.tree_limp.get_children())
        self.limp_arq.clear()
        for i, a in enumerate(achados[:2000]):
            iid = f"l{i}"
            self.limp_arq[iid] = a
            self.tree_limp.insert("", "end", iid=iid, values=(
                a.nome, a.categoria, fmt_bytes(a.tamanho), fmt_data(a.mtime), a.pasta))
        self.lbl_limp.config(text=f"{len(achados)} arquivos • {fmt_bytes(sum(a.tamanho for a in achados))}"
                                  + (" (exibindo os 2000 maiores)" if len(achados) > 2000 else ""))

    def _mover_limpeza(self):
        arqs = [self.limp_arq[i] for i in self.tree_limp.selection() if i in self.limp_arq]
        self._mover_para(arqs, PASTA_REVISAR, "Limpeza",
                         lambda movidos: self._remover_da_tabela(self.tree_limp, self.limp_arq, movidos))

    # -------------------------------------------------------------------- busca
    def _aba_busca(self, f):
        topo = tk.Frame(f, bg=BG)
        topo.pack(fill="x", pady=10)
        self._label(topo, "Nome contém:").pack(side="left")
        self.var_busca = tk.StringVar()
        self._entrada(topo, self.var_busca, 30).pack(side="left", padx=8, ipady=4)
        self.var_busca.trace_add("write", lambda *_: self._filtrar_busca())
        self._label(topo, "Categoria:").pack(side="left", padx=(12, 0))
        self.var_cat_busca = tk.StringVar(value="Todas")
        cb = ttk.Combobox(topo, textvariable=self.var_cat_busca, values=("Todas", *CORES),
                          state="readonly", width=16)
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._filtrar_busca())
        self.lbl_busca = self._label(f, "", anchor="w")
        self.lbl_busca.pack(fill="x", pady=(0, 6))
        self.tree_busca = self._tabela(
            f, ("nome", "cat", "tam", "data", "pasta"),
            ("Arquivo", "Categoria", "Tamanho", "Modificado", "Pasta"), (280, 110, 90, 100, 380),
            {"tam": "e"})
        self.tree_busca.bind("<Double-1>", lambda _e: self._abrir_selecionado(self.tree_busca, self.busca_arq))

    def _filtrar_busca(self):
        termo = self.var_busca.get().strip().lower()
        cat = self.var_cat_busca.get()
        res = [a for a in self.arquivos
               if (cat == "Todas" or a.categoria == cat) and termo in a.nome.lower()]
        res.sort(key=lambda a: a.mtime, reverse=True)
        self.tree_busca.delete(*self.tree_busca.get_children())
        self.busca_arq.clear()
        for i, a in enumerate(res[:1000]):
            iid = f"b{i}"
            self.busca_arq[iid] = a
            self.tree_busca.insert("", "end", iid=iid, values=(
                a.nome, a.categoria, fmt_bytes(a.tamanho), fmt_data(a.mtime), a.pasta))
        if self.arquivos:
            self.lbl_busca.config(text=f"{len(res)} resultados"
                                       + (" (exibindo 1000)" if len(res) > 1000 else "")
                                       + " • dê dois cliques para abrir o local do arquivo")

    # ------------------------------------------------ mover / desfazer (comuns)
    def _abrir_selecionado(self, tree, mapa):
        sel = [mapa[i] for i in tree.selection() if i in mapa]
        if sel:
            abrir_local(sel[0].caminho)

    def _remover_da_tabela(self, tree, mapa, movidos):
        for iid, a in list(mapa.items()):
            if a.caminho in movidos:
                if tree.exists(iid):
                    tree.delete(iid)
                del mapa[iid]

    def _mover_para(self, arqs, nome_pasta, tipo, ao_concluir):
        """Move arquivos para uma pasta de revisão dentro da pasta analisada (sem apagar nada)."""
        if not self._tem_pasta():
            return
        if not arqs:
            messagebox.showinfo(TITULO, "Selecione arquivos na lista.")
            return
        total = sum(a.tamanho for a in arqs)
        if not messagebox.askyesno(
                TITULO, f"Mover {len(arqs)} arquivos ({fmt_bytes(total)}) para a pasta “{nome_pasta}”?\n\n"
                        "Nada será apagado — você pode desfazer depois."):
            return
        raiz = self.raiz
        base = os.path.join(raiz, nome_pasta)

        def tarefa():
            movs, erros = [], []
            for a in arqs:
                try:
                    movs.append([a.caminho, mover_seguro(a.caminho, os.path.join(base, a.nome))])
                except OSError as e:
                    erros.append(f"{a.nome}: {e}")
            if movs:
                registrar_operacao(raiz, tipo, movs)
            return movs, erros

        def concluir(resultado):
            movs, erros = resultado
            movidos = {m[0] for m in movs}
            self.arquivos = [a for a in self.arquivos if a.caminho not in movidos]
            self._atualizar_visao_geral()
            self._filtrar_busca()
            ao_concluir(movidos)
            msg = f"{len(movs)} arquivos movidos para “{nome_pasta}”."
            if erros:
                msg += f"\n\n{len(erros)} falharam:\n" + "\n".join(erros[:5])
            messagebox.showinfo(TITULO, msg)

        self._async(tarefa, concluir, "Movendo arquivos...")

    def _desfazer(self):
        if not self._tem_pasta() or self.ocupado:
            return
        hist = ler_historico(self.raiz)
        if not hist:
            messagebox.showinfo(TITULO, "Não há operações para desfazer nesta pasta.")
            return
        op = hist[-1]
        if not messagebox.askyesno(
                TITULO, f"Desfazer a última operação?\n\n{op['tipo']} — {op['data']}\n"
                        f"{len(op['movimentos'])} arquivos voltarão ao local original."):
            return
        raiz = self.raiz
        self._async(lambda: desfazer_ultima(raiz), self._apos_desfazer, "Desfazendo operação...")

    def _apos_desfazer(self, resultado):
        if resultado:
            _tipo, ok, total = resultado
            messagebox.showinfo(TITULO, f"{ok} de {total} arquivos restaurados.")
        self._escanear()


if __name__ == "__main__":
    GerenciadorInteligente().mainloop()