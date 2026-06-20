import os
import glob
import re
import subprocess
import tempfile
import threading
import sys
import webbrowser
import csv
from io import BytesIO
from datetime import datetime

import customtkinter as ctk
from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv
from tkinter import filedialog, messagebox

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image, ImageTk


# ==========================================================
# CONFIGURACION VISUAL
# ==========================================================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ==========================================================
# RENDERIZADO DE FORMULAS LATEX
# ==========================================================
def normalizar_formulas(texto):
    """Convierte todos los formatos de formulas LaTeX a $$...$$ para procesarlos igual."""
    # \(...\) inline -> $...$  (MathJax inline)
    texto = re.sub(r'\\\((.+?)\\\)', r'$\1$', texto)
    # \[...\] display -> $$...$$
    texto = re.sub(r'\\\[(.+?)\\\]', r'$$\1$$', texto, flags=re.DOTALL)
    # [...] sin backslash pero con comandos LaTeX (como \vec, \frac, etc) -> $$...$$
    texto = re.sub(
        r'^\s*\[([^\[\]]*\\[a-zA-Z][^\[\]]*)\]\s*$',
        r'$$\1$$',
        texto,
        flags=re.MULTILINE,
    )
    return texto


# Regex: busca $$...$$ (display) y $...$ (inline)
FORMULA_BLOCK = re.compile(r'\$\$(.+?)\$\$|\$([^\$\n]+?)\$', re.DOTALL)


class FormulaRenderer:

    def __init__(self):
        self._images = []

    def clear(self):
        self._images.clear()

    def render(self, latex_str, fontsize=16, dpi=110):
        fig, ax = plt.subplots(figsize=(0.01, 0.01), dpi=dpi)
        ax.set_axis_off()
        fig.patch.set_facecolor('#2b2b2b')

        t = ax.text(
            0.5, 0.5, f'${latex_str}$',
            fontsize=fontsize, color='#DCE4EE',
            ha='center', va='center', transform=ax.transAxes,
        )

        fig.canvas.draw()
        bbox = t.get_window_extent(fig.canvas.get_renderer())
        fig.set_size_inches((bbox.width + 20) / dpi, (bbox.height + 10) / dpi)

        buf = BytesIO()
        fig.savefig(
            buf, format='png', dpi=dpi, facecolor='#2b2b2b',
            bbox_inches='tight', pad_inches=0.08,
        )
        plt.close(fig)
        buf.seek(0)

        img = Image.open(buf)
        photo = ImageTk.PhotoImage(img)
        self._images.append(photo)
        return photo


def _limpiar_para_texto(texto):
    """Convierte la respuesta en texto limpio para el panel de la app.

    - Quita bloques ```python...``` (los graficos se ejecutan aparte)
    - Convierte formulas LaTeX a texto legible con Unicode
    - Limpia markdown basico
    """
    # Quitar bloques de codigo python (se ejecutan con los botones)
    texto = re.sub(r'```[Pp]ython\s*\n.*?```', '\n[Codigo de grafico - usa los botones para ejecutarlo]\n', texto, flags=re.DOTALL)

    # Quitar otros bloques de codigo
    texto = re.sub(r'```\w*\n.*?```', '', texto, flags=re.DOTALL)

    # Convertir formulas LaTeX a texto legible
    def latex_a_texto(match):
        latex = (match.group(1) or match.group(2) or "").strip()
        # Reemplazos LaTeX -> Unicode
        reemplazos = [
            (r'\\vec\{([^}]+)\}', r'\1⃗'),
            (r'\\hat\{([^}]+)\}', r'\1̂'),
            (r'\\frac\{([^}]+)\}\{([^}]+)\}', r'(\1)/(\2)'),
            (r'\\cdot', '·'),
            (r'\\times', '×'),
            (r'\\sqrt\{([^}]+)\}', r'√(\1)'),
            (r'\\theta', 'θ'),
            (r'\\omega', 'ω'),
            (r'\\alpha', 'α'),
            (r'\\beta', 'β'),
            (r'\\mu', 'μ'),
            (r'\\tau', 'τ'),
            (r'\\phi', 'φ'),
            (r'\\Delta', 'Δ'),
            (r'\\Sigma', 'Σ'),
            (r'\\pi', 'π'),
            (r'\\infty', '∞'),
            (r'\\int', '∫'),
            (r'\\sum', 'Σ'),
            (r'\\pm', '±'),
            (r'\\neq', '≠'),
            (r'\\leq', '≤'),
            (r'\\geq', '≥'),
            (r'\\approx', '≈'),
            (r'\\rightarrow', '→'),
            (r'\\leftarrow', '←'),
            (r'\\text\{([^}]+)\}', r'\1'),
            (r'\\,', ' '),
            (r'\\;', ' '),
            (r'\\quad', '  '),
            (r'\\qquad', '    '),
            (r'\\left', ''),
            (r'\\right', ''),
            (r'\\dot\{([^}]+)\}', r'\1̇'),
            (r'\\ddot\{([^}]+)\}', r'\1̈'),
            (r'\^2', '²'),
            (r'\^3', '³'),
            (r'\^{2}', '²'),
            (r'\^{3}', '³'),
            (r'_\{([^}]+)\}', r'_\1'),
            (r'_0', '₀'),
            (r'_1', '₁'),
            (r'_2', '₂'),
            (r'_3', '₃'),
            (r'\\\\', '\n'),
        ]
        resultado = latex
        for patron, reemplazo in reemplazos:
            resultado = re.sub(patron, reemplazo, resultado)
        # Limpiar backslashes restantes de comandos LaTeX no reconocidos
        resultado = re.sub(r'\\([a-zA-Z]+)', r'\1', resultado)
        # Limpiar llaves restantes
        resultado = resultado.replace('{', '').replace('}', '')
        return f'  {resultado}  ' if match.group(1) is not None else resultado

    # Normalizar delimitadores
    texto = normalizar_formulas(texto)
    # Procesar formulas: $$...$$ (display) y $...$ (inline)
    texto = FORMULA_BLOCK.sub(latex_a_texto, texto)

    # Limpiar lineas vacias multiples
    texto = re.sub(r'\n{4,}', '\n\n\n', texto)

    return texto


def _arreglar_latex_en_codigo(codigo):
    """Convierte LaTeX en labels de matplotlib a Unicode para evitar errores de parseo.

    Gemini genera labels con LaTeX como '$\\vec{v}$' o 'v_\\theta' que causan
    ParseSyntaxException en matplotlib. La solucion mas robusta es convertir
    los comandos LaTeX a caracteres Unicode directamente.

    - Raw strings con $ (r'$\\theta$') se dejan intactas (matplotlib las parsea bien)
    - Strings normales con LaTeX se convierten a Unicode
    """
    # Tabla de reemplazos LaTeX -> Unicode para labels
    reemplazos_latex = [
        # Letras griegas (con 1 o 2 backslashes)
        (r'\\\\theta', 'θ'), (r'\\theta', 'θ'),
        (r'\\\\omega', 'ω'), (r'\\omega', 'ω'),
        (r'\\\\alpha', 'α'), (r'\\alpha', 'α'),
        (r'\\\\beta', 'β'), (r'\\beta', 'β'),
        (r'\\\\mu', 'μ'), (r'\\mu', 'μ'),
        (r'\\\\tau', 'τ'), (r'\\tau', 'τ'),
        (r'\\\\phi', 'φ'), (r'\\phi', 'φ'),
        (r'\\\\rho', 'ρ'), (r'\\rho', 'ρ'),
        (r'\\\\pi', 'π'), (r'\\pi', 'π'),
        (r'\\\\Delta', 'Δ'), (r'\\Delta', 'Δ'),
        # Vectores y acentos
        (r'\\\\vec\{([^}]+)\}', r'\1⃗'), (r'\\vec\{([^}]+)\}', r'\1⃗'),
        (r'\\\\hat\{([^}]+)\}', r'\1̂'), (r'\\hat\{([^}]+)\}', r'\1̂'),
        (r'\\\\dot\{([^}]+)\}', r'\1̇'), (r'\\dot\{([^}]+)\}', r'\1̇'),
        (r'\\\\ddot\{([^}]+)\}', r'\1̈'), (r'\\ddot\{([^}]+)\}', r'\1̈'),
        # Operadores
        (r'\\\\cdot', '·'), (r'\\cdot', '·'),
        (r'\\\\times', '×'), (r'\\times', '×'),
    ]

    lineas = codigo.split('\n')
    resultado = []
    for linea in lineas:
        # NO tocar lineas de import, comentarios, o lineas sin strings
        if linea.strip().startswith(('#', 'import', 'from')) or ('"' not in linea and "'" not in linea):
            resultado.append(linea)
            continue

        # Limpiar LaTeX invalido dentro de raw strings (r'$y\'$' -> r'$y$')
        # \' \" \` no son comandos LaTeX validos y rompen matplotlib
        if re.search(r"""\br(['"])""", linea):
            linea = re.sub(r"""\\(['"`])""", '', linea)
            resultado.append(linea)
            continue

        # Aplicar reemplazos de LaTeX -> Unicode en strings normales
        for patron, reemplazo in reemplazos_latex:
            linea = re.sub(patron, reemplazo, linea)

        # Limpiar $ delimitadores sobrantes en labels normales (no raw)
        # Si quedo algo como "$θ$" en un string normal, sacar los $
        linea = re.sub(r'\$([^$\\]{1,30})\$', r'\1', linea)

        resultado.append(linea)
    return '\n'.join(resultado)


def _sanitizar_codigo_matplotlib(codigo):
    """Arregla errores comunes en codigo matplotlib generado por Gemini.

    Gemini a veces usa argumentos invalidos como head_width, head_length
    dentro de arrowprops de FancyArrowPatch, lo cual causa AttributeError.
    Esta funcion los remueve SOLO dentro de arrowprops=dict(...).
    """
    # Remover argumentos invalidos SOLO dentro de arrowprops=dict(...)
    # head_width y head_length no son validos cuando se usa arrowstyle
    def limpiar_arrowprops(match):
        contenido = match.group(1)
        # Solo remover head_width, head_length dentro de arrowprops
        for arg in ['head_width', 'head_length']:
            contenido = re.sub(rf",?\s*{arg}\s*=\s*[^,\)]+", '', contenido)
        # Limpiar comas sobrantes
        contenido = re.sub(r',\s*,', ',', contenido)
        contenido = re.sub(r',\s*\)', ')', contenido)
        contenido = re.sub(r'\(\s*,', '(', contenido)
        return f'arrowprops=dict({contenido})'

    codigo = re.sub(r'arrowprops\s*=\s*dict\(([^)]*)\)', limpiar_arrowprops, codigo)

    # Si Gemini uso plt.quiver, intentar convertir a plt.annotate
    if 'plt.quiver' in codigo or '.quiver(' in codigo:
        print("[WARN] El codigo usa plt.quiver que puede dar problemas de escala.")

    # Fix: comillas conflictivas en strings.
    # Gemini genera cosas como label='Fricción (f'_e)' donde f' rompe el string.
    # Solucion: buscar parametros con comillas simples problematicas y usar dobles.
    # Detectamos: label='...', set_title('...'), ax.text(..., '...') donde el
    # contenido entre comillas tiene apostrofes o primas (f', N', etc.)
    lineas = codigo.split('\n')
    resultado = []
    for linea in lineas:
        # Contar comillas simples que NO sean parte de r'...' (raw strings)
        # Si hay un numero impar de comillas simples, hay un conflicto
        if "r'" not in linea and "r\"" not in linea:
            # Contar ' fuera de strings dobles
            singles = linea.count("'")
            if singles > 0 and singles % 2 != 0:
                # Numero impar de comillas simples = string roto
                # Reemplazar comillas simples en labels/text por dobles
                # Patron: convertir 'texto con ' adentro' a "texto con ' adentro"
                linea = re.sub(
                    r"(label\s*=\s*|set_title\s*\(\s*|,\s*r?)'(.+?)'(\s*[,\)\n])",
                    lambda m: f'{m.group(1)}"{m.group(2)}"{m.group(3)}',
                    linea
                )

        resultado.append(linea)
    codigo = '\n'.join(resultado)

    # Fix: plt.transforms no existe, debe ser matplotlib.transforms
    if 'plt.transforms' in codigo:
        if 'import matplotlib' not in codigo and 'import matplotlib.transforms' not in codigo:
            codigo = 'import matplotlib.transforms\n' + codigo
        codigo = codigo.replace('plt.transforms', 'matplotlib.transforms')

    # Fix: ax.arc() no existe, usar patches.Arc y add_patch
    if '.arc(' in codigo:
        lineas_arc = codigo.split('\n')
        resultado_arc = []
        for linea in lineas_arc:
            m = re.match(r'^(\s*)(\w+)\.arc\(([^)]+)\)', linea)
            if m:
                indent = m.group(1)
                ax_name = m.group(2)
                args = m.group(3)
                resultado_arc.append(f'{indent}# arc removido - no soportado directamente')
            else:
                resultado_arc.append(linea)
        codigo = '\n'.join(resultado_arc)

    return codigo


def _separar_analisis(texto):
    """Separa la respuesta principal de la seccion de analisis."""
    marcador = "---ANALISIS---"
    fin = "---FIN_ANALISIS---"
    if marcador in texto:
        idx_start = texto.index(marcador)
        idx_end = texto.index(fin) + len(fin) if fin in texto else len(texto)
        respuesta = texto[:idx_start].rstrip()
        analisis_raw = texto[idx_start + len(marcador):idx_end - len(fin) if fin in texto else idx_end].strip()
        return respuesta, analisis_raw
    return texto, ""


def _formatear_analisis_html(analisis_raw):
    """Convierte la seccion de analisis en HTML con estilo."""
    if not analisis_raw:
        return ""

    html_parts = ['<div class="analisis-box">']
    html_parts.append('<h2 class="analisis-titulo">Analisis Didactico</h2>')

    en_glosario = False

    for line in analisis_raw.split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith("TEMA:"):
            tema = line[5:].strip()
            html_parts.append(f'<div class="tema-badge">{tema}</div>')
        elif line.startswith("JUSTIFICACION_TEMA:"):
            just = line[19:].strip()
            html_parts.append(f'<p class="justificacion">{just}</p>')
        elif line.startswith("GLOSARIO:"):
            html_parts.append('<h3>Glosario de Formulas</h3>')
            html_parts.append('<div class="glosario">')
            en_glosario = True
        elif en_glosario and (line.startswith("- $$") or line.startswith("- $")):
            # Linea de glosario: - $$formula$$: explicacion
            contenido = line[2:].strip()
            # Buscar posicion del cierre $$ para separar formula de explicacion
            cierre = contenido.find('$$', 2)  # buscar segundo $$
            if cierre != -1:
                pos_dos_puntos = contenido.find(':', cierre + 2)
                if pos_dos_puntos != -1:
                    formula_part = contenido[:pos_dos_puntos].strip()
                    explicacion = contenido[pos_dos_puntos + 1:].strip()
                else:
                    formula_part = contenido
                    explicacion = ""
            elif ':' in contenido:
                # Para formulas inline $...$
                # Buscar cierre del $ inline
                cierre_inline = contenido.find('$', 1)
                if cierre_inline != -1:
                    pos_dos_puntos = contenido.find(':', cierre_inline + 1)
                    if pos_dos_puntos != -1:
                        formula_part = contenido[:pos_dos_puntos].strip()
                        explicacion = contenido[pos_dos_puntos + 1:].strip()
                    else:
                        formula_part = contenido
                        explicacion = ""
                else:
                    formula_part, explicacion = contenido.split(':', 1)
                    explicacion = explicacion.strip()
            else:
                formula_part = contenido
                explicacion = ""

            html_parts.append(
                f'<div class="glosario-item">'
                f'<span class="glosario-formula">{formula_part}</span>'
            )
            if explicacion:
                html_parts.append(f'<span class="glosario-desc">{explicacion}</span>')
            html_parts.append('</div>')
        elif en_glosario and line.startswith("- "):
            html_parts.append(f'<p class="glosario-item-text">{line[2:]}</p>')
        else:
            html_parts.append(f'<p>{line}</p>')

    if en_glosario:
        html_parts.append('</div>')  # cerrar div.glosario
    html_parts.append('</div>')  # cerrar div.analisis-box
    return '\n'.join(html_parts)


def _markdown_a_html(texto):
    """Convierte markdown basico a HTML limpio, sin bloques de codigo."""

    # 1. Quitar bloques de codigo Python (los graficos se ejecutan en la app)
    def reemplazo_codigo(match):
        codigo = match.group(1) or match.group(2) or ""
        primera = codigo.strip().split('\n')[0] if codigo.strip() else ""
        if "GRAFICO_TIEMPO" in primera:
            nota = "Grafico de magnitudes vs tiempo"
        elif "GRAFICO_VECTORES" in primera:
            nota = "Diagrama de vectores y versores"
        elif "GRAFICO_DCL" in primera:
            nota = "Diagrama de cuerpo libre"
        else:
            nota = "Grafico"
        return f'\n\n[GRAFICO:{nota}]\n\n'

    texto = re.sub(r'```[Pp]ython\s*\n(.*?)```', reemplazo_codigo, texto, flags=re.DOTALL)
    texto = re.sub(r'```\w*\s*\n(.*?)```', reemplazo_codigo, texto, flags=re.DOTALL)

    # 1b. Safety net: limpiar codigo Python suelto que Gemini mando sin ``` markers.
    #     Detecta lineas que contienen codigo matplotlib/numpy tipico y las elimina.
    def es_codigo_python(linea):
        s = linea.strip()
        patrones_codigo = [
            r'^import\s+', r'^from\s+\w+\s+import', r'^fig\s*[,=]', r'^ax\d*[\.\s=]',
            r'^plt\.', r'^np\.', r'^body_\w+\s*=', r'^arrow_', r'^rect',
            r'\.annotate\(', r'\.add_patch\(', r'\.set_aspect\(', r'\.set_xlim\(',
            r'\.set_ylim\(', r'\.set_title\(', r'\.legend\(', r'\.plot\(',
            r'\.text\(', r'arrowprops\s*=', r'arrowstyle\s*=', r'plt\.show\(\)',
            r'\.set_xticks\(', r'\.set_yticks\(', r'plt\.Line2D\(',
            r'plt\.Rectangle\(', r'\.grid\(', r'fontsize\s*=\s*\d',
            r'color\s*=\s*[\'"]', r'lw\s*=\s*\d', r'zorder\s*=\s*\d',
            r'^alpha_\w+\s*=', r'^center_\w+\s*=', r'^block_\w+\s*=',
            r'^\w+_len\s*=', r'^\w+_vec_\w+\s*=', r'^patches\.',
            r'\.subplots\(', r'plt\.tight_layout', r'plt\.subplots\(',
            r'^incline_', r'^normal_', r'^tension_', r'^friction_',
            r'^Mg_', r'^mg_',
        ]
        return any(re.search(p, s) for p in patrones_codigo)

    lineas_texto = texto.split('\n')
    lineas_limpias = []
    bloque_codigo_suelto = False
    for linea in lineas_texto:
        s = linea.strip()
        # Detectar inicio de codigo suelto con marcador
        if s.startswith('# GRAFICO_'):
            bloque_codigo_suelto = True
            if 'TIEMPO' in s:
                lineas_limpias.append('\n[GRAFICO:Grafico de magnitudes vs tiempo]\n')
            elif 'VECTORES' in s:
                lineas_limpias.append('\n[GRAFICO:Diagrama de vectores y versores]\n')
            elif 'DCL' in s:
                lineas_limpias.append('\n[GRAFICO:Diagrama de cuerpo libre]\n')
            continue
        # Detectar inicio de codigo suelto SIN marcador (Gemini omitio # GRAFICO_)
        if not bloque_codigo_suelto and es_codigo_python(linea):
            bloque_codigo_suelto = True
            lineas_limpias.append('\n[GRAFICO:Diagrama de cuerpo libre]\n')
            continue
        # Si estamos en un bloque de codigo suelto, seguir saltando lineas de codigo
        if bloque_codigo_suelto:
            if es_codigo_python(linea) or s == '' or s.startswith('#'):
                continue
            else:
                bloque_codigo_suelto = False
                lineas_limpias.append(linea)
        else:
            if s in ('plt.show()', '') or re.match(r'^import\s+matplotlib', s) or re.match(r'^import\s+numpy', s):
                continue
            lineas_limpias.append(linea)

    texto = '\n'.join(lineas_limpias)

    # 1c. Convertir titulos de PASO en bold a headings markdown
    #     Gemini a veces escribe **PASO 1 - ...** en vez de ## PASO 1 - ...
    lineas_fix_paso = texto.split('\n')
    for i, linea in enumerate(lineas_fix_paso):
        s = linea.strip()
        if re.match(r'^\*\*\s*(PASO\s+\d|ANTES DE EMPEZAR|CASOS ESPECIALES)', s, re.IGNORECASE):
            contenido = re.sub(r'^\*\*\s*', '', s)
            contenido = re.sub(r'\s*\*\*\s*$', '', contenido)
            lineas_fix_paso[i] = f'## {contenido}'
        elif re.match(r'^\*\*(Metodolog[ií]a|Lectura del Enunciado)', s, re.IGNORECASE):
            contenido = re.sub(r'^\*\*\s*', '', s)
            contenido = re.sub(r'\s*\*\*\s*$', '', contenido)
            lineas_fix_paso[i] = f'## {contenido}'
    texto = '\n'.join(lineas_fix_paso)

    # 2. Convertir "falso display" a inline
    #    Gemini usa $$formula$$ para formulas que deberian ser inline.
    #    2A) Formulas cortas embebidas en texto: $$T$$, $$m$$, $$N_m$$ -> $T$, $m$, $N_m$
    def convertir_display_corto(match):
        inner = match.group(1)
        if len(inner) < 30:
            return '$' + inner + '$'
        return match.group(0)
    texto = re.sub(r'[$][$]([^$]+?)[$][$]', convertir_display_corto, texto)

    # 2B) Formulas largas solas en su linea: convertir si estan entre texto
    lineas_pre = texto.split('\n')
    for i, linea in enumerate(lineas_pre):
        stripped = linea.strip()
        if not re.match(r'^[$][$][^$]+[$][$]$', stripped):
            continue

        inner = stripped[2:-2]

        prev = ""
        for j in range(i - 1, -1, -1):
            if lineas_pre[j].strip():
                prev = lineas_pre[j].strip()
                break

        next_l = ""
        for j in range(i + 1, len(lineas_pre)):
            if lineas_pre[j].strip():
                next_l = lineas_pre[j].strip()
                break

        prev_es_texto = (prev
                         and not prev.endswith(':')
                         and not prev.startswith('#')
                         and not prev.startswith('---')
                         and not re.match(r'^\$\$', prev))
        next_continua = (next_l
                         and len(next_l) > 0
                         and (next_l[0].islower()
                              or next_l[0] in ',.;:)]}'))

        if prev_es_texto or next_continua:
            lineas_pre[i] = f'${inner}$'

    texto = '\n'.join(lineas_pre)

    # 2b. Pegar formulas inline sueltas con el texto circundante.
    lineas_raw = texto.split('\n')
    merged = []
    for i_raw, linea in enumerate(lineas_raw):
        s = linea.strip()
        es_formula_inline = bool(re.match(r'^\$[^$]+\$$', s) and not s.startswith('$$'))
        es_fragmento_corto = (0 < len(s) <= 4
                              and not s.startswith('#')
                              and not s.startswith('[')
                              and not s.startswith('*')
                              and not s.startswith('-')
                              and not s.startswith('='))

        if (es_formula_inline or es_fragmento_corto) and merged:
            idx = len(merged) - 1
            while idx >= 0 and not merged[idx].strip():
                idx -= 1
            if idx >= 0:
                merged = merged[:idx + 1]
                merged[idx] = merged[idx].rstrip() + ' ' + s
                continue
        if not s and merged:
            next_nonblank = ""
            for j in range(i_raw + 1, len(lineas_raw)):
                if lineas_raw[j].strip():
                    next_nonblank = lineas_raw[j].strip()
                    break
            es_next_inline = bool(re.match(r'^\$[^$]+\$$', next_nonblank) and not next_nonblank.startswith('$$'))
            es_next_frag = (0 < len(next_nonblank) <= 4
                            and not next_nonblank.startswith('#')
                            and not next_nonblank.startswith('-')
                            and not next_nonblank.startswith('*')
                            and not next_nonblank.startswith('['))
            if es_next_inline or es_next_frag:
                continue

        merged.append(linea)
    texto = '\n'.join(merged)

    # 3. Proteger formulas LaTeX (ahora con display/inline correctos)
    #    Usar caracteres Unicode PUA (Private Use Area) como placeholders
    #    en lugar de NULL bytes que pueden causar problemas en HTML
    formulas = []
    PH_START = '￾'  # Unicode noncharacter (seguro para placeholder)
    PH_END = '￿'    # Unicode noncharacter (seguro para placeholder)

    def guardar_formula(match):
        formulas.append(match.group(0))
        return f'{PH_START}F{len(formulas)-1}{PH_END}'
    # Display primero ($$...$$), luego inline ($...$)
    texto = re.sub(r'\$\$.*?\$\$', guardar_formula, texto, flags=re.DOTALL)
    texto = re.sub(r'\$[^\$\n]+?\$', guardar_formula, texto)

    # 4. Colapsar lineas sueltas en parrafos fluidos
    #    Gemini pone formulas inline ($...$) en su propia linea, generando:
    #      "Para un pendulo de longitud fija\n$L$\n, el radio..."
    #    Esto se ve horrible. Unimos esas lineas sueltas con la anterior.
    #
    #    Indices de formulas display para no colapsarlas
    display_formula_indices = set()
    for i, f in enumerate(formulas):
        if f.startswith('$$'):
            display_formula_indices.add(i)

    def es_linea_display(linea):
        """Verifica si una linea es SOLO un placeholder de formula display."""
        stripped = linea.strip()
        m = re.match(rf'^{re.escape(PH_START)}F(\d+){re.escape(PH_END)}$', stripped)
        if m and int(m.group(1)) in display_formula_indices:
            return True
        return False

    def es_linea_especial(linea):
        """Lineas que NO deben colapsarse con la anterior."""
        stripped = linea.strip()
        if not stripped:
            return True  # linea vacia = separador de parrafo
        if stripped.startswith('#'):
            return True  # heading
        if re.match(r'^[\*\-]\s+', stripped) or re.match(r'^\d+\.\s+', stripped):
            return True  # item de lista
        if stripped.startswith('[GRAFICO:'):
            return True  # placeholder de grafico
        if stripped.startswith('---'):
            return True  # linea horizontal
        if es_linea_display(linea):
            return True  # formula display $$...$$ en su propia linea
        return False

    lineas = texto.split('\n')
    colapsado = []
    for linea in lineas:
        if es_linea_especial(linea):
            colapsado.append(linea)
        elif colapsado and not es_linea_especial(colapsado[-1]):
            colapsado[-1] = colapsado[-1].rstrip() + ' ' + linea.strip()
        else:
            colapsado.append(linea)

    # 4b. Segundo pase: absorber lineas huerfanas muy cortas (como ":", "y", ")", ",")
    #     que quedaron solas entre lineas vacias. Pegarlas a la linea NO vacia mas cercana.
    final = []
    for linea in colapsado:
        stripped = linea.strip()
        # Es huerfana: muy corta (1-3 chars), no es heading, no es placeholder, no esta vacia
        es_huerfana = (0 < len(stripped) <= 3
                       and not stripped.startswith('#')
                       and not stripped.startswith('[')
                       and not re.match(rf'^{re.escape(PH_START)}', stripped))
        if es_huerfana and final:
            # Buscar la ultima linea no vacia hacia atras para pegarla
            idx = len(final) - 1
            while idx >= 0 and not final[idx].strip():
                idx -= 1
            if idx >= 0:
                final[idx] = final[idx].rstrip() + ' ' + stripped
                continue
        final.append(linea)
    colapsado = final

    # 4c. Tercer pase: absorber lineas vacias entre una linea de texto y un placeholder
    #     inline (formula corta). Evita que formulas inline queden en parrafo separado.
    final2 = []
    for i, linea in enumerate(colapsado):
        stripped = linea.strip()
        # Si es linea vacia, ver si esta entre texto y una formula inline
        if not stripped and i > 0 and i < len(colapsado) - 1:
            prev_s = colapsado[i - 1].strip() if i > 0 else ""
            next_s = colapsado[i + 1].strip() if i < len(colapsado) - 1 else ""
            # Chequear si la siguiente es un placeholder de formula inline
            es_ph_inline = (re.match(rf'^{re.escape(PH_START)}F(\d+){re.escape(PH_END)}$', next_s)
                            and not es_linea_display(colapsado[i + 1]))
            # Si la anterior es texto normal y la siguiente es formula inline, saltar la vacia
            if prev_s and not es_linea_especial(colapsado[i - 1]) and es_ph_inline:
                continue
            # Si la anterior es formula inline y la siguiente es texto normal
            prev_es_ph_inline = (re.match(rf'^{re.escape(PH_START)}F(\d+){re.escape(PH_END)}$', prev_s)
                                 and not es_linea_display(colapsado[i - 1]))
            if prev_es_ph_inline and next_s and not es_linea_especial(colapsado[i + 1]):
                continue
        final2.append(linea)
    colapsado = final2

    texto = '\n'.join(colapsado)

    # 4d. Asegurar linea vacia ANTES y DESPUES de headings y separadores
    lineas_final = texto.split('\n')
    con_separacion = []
    for i, linea in enumerate(lineas_final):
        s = linea.strip()
        # Si la linea contiene un heading pegado a texto (## PASO 1 Texto aqui...)
        # separar el heading del texto
        h_inline = re.match(r'^(#{1,4}\s+[^#\n]+?)(\s{2,}|\.\s+)(.+)$', s)
        if h_inline:
            heading_part = h_inline.group(1).strip()
            rest_part = h_inline.group(3).strip()
            if con_separacion and con_separacion[-1].strip():
                con_separacion.append('')
            con_separacion.append(heading_part)
            con_separacion.append('')
            con_separacion.append(rest_part)
            continue

        if (s.startswith('#') or s.startswith('---')) and i > 0:
            if con_separacion and con_separacion[-1].strip():
                con_separacion.append('')
        con_separacion.append(linea)
        # Linea vacia DESPUES de headings
        if s.startswith('#') and not s.startswith('#GRAFICO'):
            con_separacion.append('')
    texto = '\n'.join(con_separacion)

    # 5. Escapar HTML
    texto = texto.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # 6. Procesar markdown por parrafos (separados por lineas en blanco)
    bloques = re.split(r'\n{2,}', texto)
    html_bloques = []

    for bloque in bloques:
        bloque = bloque.strip()
        if not bloque:
            continue

        # Placeholder de grafico
        grafico_match = re.match(r'\[GRAFICO:(.+?)\]', bloque)
        if grafico_match:
            nota = grafico_match.group(1)
            html_bloques.append(
                f'<div class="codigo-nota">\U0001f4ca {nota} '
                f'— ejecutar desde la app con los botones de graficos.</div>'
            )
            continue

        # Encabezados
        h_match = re.match(r'^(#{1,4})\s+(.+)$', bloque)
        if h_match:
            nivel = len(h_match.group(1))
            texto_h = h_match.group(2)
            if any(kw in texto_h.upper() for kw in ['PASO ', 'ANTES DE EMPEZAR', 'CASOS ESPECIALES']):
                html_bloques.append(f'<h{nivel} class="paso-heading">{texto_h}</h{nivel}>')
            else:
                html_bloques.append(f'<h{nivel}>{texto_h}</h{nivel}>')
            continue

        # Linea horizontal
        if re.match(r'^---+\s*$', bloque):
            html_bloques.append('<hr>')
            continue

        # Listas (viñetas o numeradas)
        lineas = bloque.split('\n')
        es_lista = all(
            re.match(r'^\s*[\*\-]\s+', l) or re.match(r'^\s*\d+\.\s+', l) or not l.strip()
            for l in lineas
        )
        if es_lista and any(l.strip() for l in lineas):
            items = []
            for l in lineas:
                l = l.strip()
                if not l:
                    continue
                # Quitar marcador de lista
                l = re.sub(r'^\s*[\*\-]\s+', '', l)
                l = re.sub(r'^\s*\d+\.\s+', '', l)
                l = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', l)
                l = re.sub(r'\*([^\*]+?)\*', r'<em>\1</em>', l)
                items.append(f'<li>{l}</li>')
            html_bloques.append('<ul>' + ''.join(items) + '</ul>')
            continue

        # Parrafo normal: unir lineas consecutivas en un solo flujo
        contenido = ' '.join(l.strip() for l in lineas if l.strip())

        # Negritas e italicas
        contenido = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', contenido)
        contenido = re.sub(r'\*([^\*]+?)\*', r'<em>\1</em>', contenido)

        html_bloques.append(f'<p>{contenido}</p>')

    # 7. Post-procesamiento: fusionar parrafos cortos consecutivos
    #    Gemini a menudo pone cada dato en un parrafo separado (double newline),
    #    lo que genera muchos <p> individuales que se ven "hacia abajo".
    #    Fusionamos parrafos cortos consecutivos (sin headings entre ellos)
    #    en un solo <p> con <br> entre ellos para mantener la compacidad visual.
    fusionados = []
    buffer_cortos = []

    def es_parrafo_fusionable(html_str):
        """Determina si un <p> puede fusionarse con los vecinos."""
        if not html_str.startswith('<p>'):
            return False
        contenido = html_str[3:-4]  # quitar <p> y </p>
        # No fusionar si es solo una formula display (placeholder)
        for idx in display_formula_indices:
            ph = f'{PH_START}F{idx}{PH_END}'
            if contenido.strip() == ph:
                return False
        return True

    def es_inicio_seccion(html_str):
        """Detecta si un parrafo empieza con un encabezado bold (nueva seccion)."""
        if not html_str.startswith('<p>'):
            return False
        contenido = html_str[3:-4].strip()
        # Empieza con <strong> y termina con :</strong> → es titulo de seccion
        if contenido.startswith('<strong>') and ':</strong>' in contenido:
            # Solo si es corto (titulo, no parrafo largo con bold)
            texto_sin_tags = re.sub(r'<[^>]+>', '', contenido)
            if len(texto_sin_tags) < 80:
                return True
        return False

    def flush_buffer():
        if not buffer_cortos:
            return
        if len(buffer_cortos) == 1:
            fusionados.append(buffer_cortos[0])
        else:
            # Extraer contenido de cada <p>...</p> y unir con <br>
            partes = []
            for p in buffer_cortos:
                partes.append(p[3:-4])  # quitar <p> y </p>
            fusionados.append('<p>' + '<br>\n'.join(partes) + '</p>')
        buffer_cortos.clear()

    for bloque_html in html_bloques:
        if es_parrafo_fusionable(bloque_html):
            # Si es inicio de una nueva seccion, cerrar el grupo anterior
            if es_inicio_seccion(bloque_html) and buffer_cortos:
                flush_buffer()
            buffer_cortos.append(bloque_html)
        else:
            flush_buffer()
            fusionados.append(bloque_html)
    flush_buffer()

    resultado = '\n'.join(fusionados)

    # 8. Restaurar formulas
    for i, formula in enumerate(formulas):
        resultado = resultado.replace(f'{PH_START}F{i}{PH_END}', formula)

    return resultado


def abrir_en_navegador(response_text):
    # Normalizar todas las formulas a $$...$$ para MathJax
    normalizado = normalizar_formulas(response_text)

    # Separar respuesta y analisis
    respuesta, analisis_raw = _separar_analisis(normalizado)
    analisis_html = _formatear_analisis_html(analisis_raw)

    # Convertir markdown a HTML limpio
    contenido_html = _markdown_a_html(respuesta)

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Respuesta - Asistente Fisica I</title>
<script>
MathJax = {{
  tex: {{
    inlineMath: [['$', '$']],
    displayMath: [['$$', '$$']],
    processEscapes: true
  }},
  options: {{
    renderActions: {{
      addMenu: [0, '', '']
    }},
    enableEnrichment: false
  }}
}};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<style>
body {{ font-family: 'Segoe UI', Tahoma, sans-serif; max-width: 900px; margin: 40px auto;
       padding: 24px; background: #1a1a2e; color: #e0e0e0; line-height: 1.8; font-size: 16px; }}
h1,h2,h3,h4 {{ color: #4ea8de; margin-top: 0.8em; margin-bottom: 0.3em; }}
.paso-heading {{ color: #e67e22; margin-top: 1.2em; margin-bottom: 0.4em; }}
p {{ margin: 4px 0; }}
strong {{ color: #7ec8e3; }}
em {{ color: #c4c4c4; }}
hr {{ border: none; border-top: 1px solid #2d4a6f; margin: 28px 0; }}
ul {{ padding-left: 24px; }}
li {{ margin: 6px 0; }}

/* MathJax: forzar inline para formulas $...$ y corregir tamanio */
mjx-container {{ display: inline !important; }}
mjx-container[display="true"] {{ display: block !important; text-align: center; margin: 0.8em 0; }}
.MathJax {{ font-size: 115% !important; display: inline !important; }}
mjx-container[display="true"] .MathJax {{ display: block !important; }}

/* Nota de grafico (reemplazo del bloque de codigo) */
.codigo-nota {{
    background: #16213e; border-left: 4px solid #4ea8de; padding: 12px 18px;
    border-radius: 0 8px 8px 0; margin: 16px 0; color: #7ec8e3;
    font-size: 14px; font-style: italic;
}}

/* Estilos para la seccion de analisis */
.analisis-box {{
    margin-top: 40px; padding: 24px; border-radius: 12px;
    background: linear-gradient(135deg, #16213e 0%, #1a1a3e 100%);
    border: 1px solid #2d6a4f;
}}
.analisis-titulo {{
    color: #40916c; margin-top: 0; font-size: 22px;
    border-bottom: 2px solid #2d6a4f; padding-bottom: 10px;
}}
.tema-badge {{
    display: inline-block; background: #2d6a4f; color: #fff; padding: 6px 18px;
    border-radius: 20px; font-weight: bold; font-size: 15px; margin: 10px 0;
}}
.justificacion {{
    color: #a8d5ba; font-style: italic; margin: 8px 0 20px 0;
    padding-left: 12px; border-left: 3px solid #2d6a4f;
}}
.glosario {{ margin-top: 10px; }}
.glosario-item {{
    display: flex; flex-direction: column; background: #0d1b2a; border-radius: 8px;
    padding: 12px 16px; margin: 8px 0; border-left: 4px solid #4ea8de;
}}
.glosario-formula {{ color: #4ea8de; font-size: 17px; margin-bottom: 4px; }}
.glosario-desc {{ color: #b0c4de; font-size: 14px; }}
.glosario-item-text {{ color: #b0c4de; margin: 4px 0; }}
</style>
</head><body>
<h1>Respuesta del Asistente de Fisica I</h1>
<div class="respuesta">
{contenido_html}
</div>
{analisis_html}
</body></html>"""

    with tempfile.NamedTemporaryFile('w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(html)
        ruta_html = f.name
    webbrowser.open('file://' + ruta_html)


# ==========================================================
# LOGICA DEL ASISTENTE DE FISICA
# ==========================================================
class AsistenteFisica:
    def __init__(self):
        self.chat = None
        self.uploaded_files = []
        self.model = None
        self._configurar_modelo()

    def _configurar_modelo(self):
        load_dotenv(override=True)
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise ValueError(
                "No se encontro la API Key. Crea un archivo .env con GEMINI_API_KEY=tu_clave"
            )

        self.client = genai.Client(api_key=api_key)

        system_instruction = """
Eres un Asistente y Tutor Avanzado de Fisica I a nivel universitario.
Tu objetivo es resolver, explicar y analizar problemas de fisica enfocados en las siguientes areas EXCLUSIVAMENTE:
- Cinematica
- Dinamica
- Trabajo y Energia
- Cantidad de movimiento o momento lineal
- Momento Angular

REGLAS ESTRICTAS DE COMPORTAMIENTO:
1. USO DE RECURSOS: Inicialmente, tu conocimiento DEBE limitarse a los documentos, libros, apuntes y ejercicios resueltos proporcionados por el usuario. No debes inventar datos ni usar busquedas web externas por ahora. Aplica el conocimiento de esos textos para resolver las dudas.
2. METODOLOGIA DE 5 PASOS PARA DINAMICA (Catedra Gasaneo - UNS):
   INSTRUCCION GENERAL: NO seguir el orden de los incisos ciegamente. Los incisos a veces piden
   resultados parciales que son incognitas que salen TODAS juntas del mismo sistema de ecuaciones.
   La metodologia se aplica primero de corrido (pasos 1 a 5), y despues se responden los incisos
   con los resultados obtenidos.
   EXCEPCION: si el problema tiene dos situaciones fisicas distintas (ej: "primero sube, despues
   la cuerda se corta"), cada situacion se resuelve con sus propios 5 pasos por separado.

   ANTES DE EMPEZAR: Leer todo el enunciado. Identificar:
   - Cuantos cuerpos hay y si hay vinculos entre ellos (cuerdas, contacto, resortes)
   - Si hay rozamiento (estatico o dinamico)
   - Si el movimiento es lineal, circular o combinado
   - Que sistema de coordenadas conviene (cartesiano para planos inclinados y movimiento recto,
     polares para pendulos y movimiento circular, cilindricas para circular en 3D)

   PASO 1 - DCL (Diagrama de Cuerpo Libre):
   Objetivo: dibujar TODAS las fuerzas que actuan sobre CADA cuerpo.
   - Declarar el sistema de referencia (SR): "SR inercial, fijo al suelo".
   - Declarar el sistema de coordenadas (SC): especificar ejes, origen, sentido positivo.
     Ej: "SC cartesiano (x, y) con origen en el punto de contacto, x positivo a la derecha"
     Ej: "SC polares (e_r, e_theta) con origen en O, e_r radial hacia afuera"
   - Aislar cada cuerpo: dibujarlo como punto o bloque, SIN los otros cuerpos.
   - Dibujar TODAS las fuerzas sobre ese cuerpo. Lista de fuerzas posibles:
     * Peso (mg, siempre vertical hacia abajo)
     * Normal (N, perpendicular a la superficie, alejandose de ella)
     * Tension (T, a lo largo de la cuerda, tirando del cuerpo)
     * Rozamiento (f_RE o f_RD, paralelo a la superficie, opuesto al movimiento o tendencia)
     * Fuerza elastica (F_e = -kx, a lo largo del resorte)
     * Fuerza aplicada (F, segun indique el problema)
     * Fuerza viscosa (F_v = -bv o -kv^2, opuesta a la velocidad)
   - Para cada fuerza indicar nombre, direccion y sentido con flecha. Si no se conoce el sentido,
     asumir uno y si sale negativo al resolver, significa que va al reves.
   - Si hay mas de un cuerpo, hacer un DCL separado para cada uno. Las fuerzas de interaccion
     aparecen en ambos DCL con sentidos opuestos (3ra ley de Newton).
   - Dibujar los ejes explicitamente en el diagrama.

   PASO 2 - Segunda Ley de Newton (Sigma F = ma):
   Objetivo: escribir las ecuaciones de Newton en cada direccion.
   - Para CADA cuerpo, escribir Sigma F = ma separando en componentes.
     UNA ecuacion por cada direccion del SC (2 ecuaciones en 2D, 3 en 3D).
   - Recorrer todas las fuerzas del DCL: "esta fuerza tiene componente en esta direccion?
     Es positiva o negativa segun mis ejes?"
   - Descomponer fuerzas inclinadas: el coseno va con el angulo ENTRE la fuerza y la direccion.
     El seno va con la otra direccion.
   - NO poner la aceleracion centripeta como fuerza. En movimiento circular,
     el lado derecho de la ecuacion radial es mv^2/R (o m*omega^2*r). Esa NO es una fuerza.
   - Si el cuerpo no se mueve en alguna direccion, poner a = 0 (queda Sigma F = 0).
   - En coordenadas polares:
     e_r: a_r = r'' - r*theta'^2
     e_theta: a_theta = r*theta'' + 2*r'*theta'
     Si r = cte (pendulo, circular): r'' = 0, r' = 0, queda a_r = -r*theta'^2, a_theta = r*theta''
   - Numerar cada ecuacion (1), (2), (3)... para referenciar despues.

   PASO 3 - PAR (Pares de Accion-Reaccion):
   Objetivo: identificar que cuerpo o agente ejerce cada fuerza y relacionar fuerzas entre cuerpos.
   - Para CADA fuerza del DCL, escribir quien la ejerce: "N <- superficie", "T <- cuerda",
     "mg <- Tierra", "f_RE <- superficie del otro cuerpo".
   - Si hay dos o mas cuerpos, identificar pares accion-reaccion entre ellos:
     N_A/B = N_B/A (misma magnitud), f_roz A/B = f_roz B/A (misma magnitud).
   - Estas igualdades se numeran como ecuaciones adicionales.

   PASO 4 - Fisica (leyes de fuerza, vinculos, aproximaciones):
   Objetivo: reemplazar cada fuerza por su expresion matematica y agregar condiciones.
   - Leyes de fuerza:
     * Peso: P = mg
     * Normal: N = ? (incognita, sale de las ecuaciones)
     * Tension: T = ? (incognita. Si cuerda inextensible y masa despreciable, misma T en ambos extremos)
     * Rozamiento estatico (no desliza): f_RE <= mu_e * N. En limite (CMI): f_RE = mu_e * N
     * Rozamiento dinamico (desliza): f_RD = mu_d * N
     * Fuerza elastica: F_e = -k * Delta_x (Hooke, restauradora)
     * Fuerza viscosa: F_v = -bv o F_v = -kv^2
   - Vinculos del sistema:
     * Cuerda inextensible: a_A = a_B (si se mueven juntos) o |a_A| = |a_B| (polea)
     * Movimiento conjunto (CMI): a_A = a_B
     * Radio constante: r = cte, r' = 0, r'' = 0
     * Superficie: el cuerpo no se despega -> N >= 0
   - Aproximaciones (si corresponde): angulo pequenio: sen(theta) ~ theta, cos(theta) ~ 1
   - CONTEO: Listar incognitas y ecuaciones. Verificar #ecuaciones = #incognitas.

   PASO 5 - Resolver:
   Objetivo: despejar las incognitas y obtener valores numericos.
   - Primero resolver SIMBOLICAMENTE (con letras), sin reemplazar numeros.
   - Estrategia: eliminar incognitas que no se piden primero (despejar Normal de una ecuacion
     y reemplazar en otra, sumar ecuaciones para cancelar fuerzas internas, etc.)
   - Reemplazar datos numericos AL FINAL.
   - Verificar: unidades correctas? Signo tiene sentido? Orden de magnitud razonable?
   - Si una incognita sale negativa y se asumio una direccion, aclarar que va en sentido opuesto.
   - Responder cada inciso referenciando las ecuaciones y resultados.

   CASOS ESPECIALES:
   - Movimiento circular uniforme: ecuacion radial tiene mv^2/R del lado derecho. a_t = 0.
   - MAS: si la ecuacion se reduce a x'' + omega_0^2 * x = 0, el sistema hace MAS.
     Solucion: x(t) = A * sen(omega_0 * t + phi). Condiciones iniciales determinan A y phi.
   - Dos cuerpos que pueden deslizar: suponer primero que se mueven juntos (a_A = a_B).
     Resolver. Verificar si f_RE necesario supera mu_e * N. Si lo supera, rehacer con
     a_A != a_B y f_RD = mu_d * N.

   COMO DETECTAR SI ES DINAMICA O CINEMATICA:
   - Si el problema menciona fuerzas, masas, pesos, tensiones, normales, rozamiento,
     resortes, o pide hallar fuerzas -> es DINAMICA, usar los 5 pasos de arriba.
   - Si el problema solo habla de posicion, velocidad, aceleracion, trayectoria,
     tiempos, distancias, sin mencionar fuerzas ni masas -> es CINEMATICA, usar la
     metodologia de cinematica de abajo.
   - Si tiene AMBOS (primero cinematica para describir el movimiento y luego dinamica
     para hallar fuerzas), resolver cinematica primero y luego dinamica con los 5 pasos.

   METODOLOGIA PARA CINEMATICA (Catedra Gasaneo - UNS):

   PASO 1 - Identificar el tipo de movimiento:
   - Rectilineo uniforme (v = cte, a = 0)
   - Rectilineo uniformemente acelerado (a = cte)
   - Circular uniforme (omega = cte, a_t = 0)
   - Circular no uniforme (omega variable, a_t != 0)
   - Curvilíneo general

   PASO 2 - Elegir sistema de coordenadas:
   - Cartesiano (x, y): para movimiento rectilineo o parabolico (tiro oblicuo)
   - Polares (r, theta): para movimiento circular o espiral, donde el radio o el angulo son datos
   - Intrinsecas (t, n): cuando se conoce la trayectoria y se pide descomponer en tangencial/normal
   Declarar explicitamente: origen, ejes, sentido positivo.

   PASO 3 - Escribir las ecuaciones de movimiento:
   Usar EXCLUSIVAMENTE las formulas de la catedra segun el SC elegido:

   En CARTESIANAS:
     r(t) = x(t) x_hat + y(t) y_hat
     v(t) = x'(t) x_hat + y'(t) y_hat
     a(t) = x''(t) x_hat + y''(t) y_hat

   En POLARES:
     r(t) = r(t) e_r
     v_r = r'          ,  v_theta = r * theta'
     a_r = r'' - r * theta'^2    ,   a_theta = r * theta'' + 2 * r' * theta'
     Si r = cte: v_r = 0, a_r = -r * theta'^2, a_theta = r * theta''

   En INTRINSECAS:
     v = s'(t)  (rapidez = derivada del arco)
     a_t = s''(t) = v'  (componente tangencial)
     a_n = v^2 / rho    (componente normal, rho = radio de curvatura)

   NO usar otras formulas que no sean estas. NO inventar formulas.

   PASO 4 - Aplicar condiciones iniciales y datos:
   - Reemplazar las condiciones iniciales (x(0), v(0), theta(0), etc.)
   - Usar los datos del problema para determinar constantes de integracion
   - Si dan la trayectoria (ej: y = f(x)), derivar para obtener v y a

   PASO 5 - Resolver y verificar:
   - Resolver simbolicamente primero, numericamente despues
   - Verificar unidades, signos y orden de magnitud
   - Si piden graficar: generar bloques de codigo Python con matplotlib

3. ADAPTACION: Debes adaptar el nivel de tu explicacion segun el nivel del usuario si este te lo pide. Explica conceptos teoricos con tono cientifico pero accesible e intuitivo.
4. GRAFICOS (Solo en Cinematica): Si el usuario te pide explicitamente grafica o generarme un grafico de posicion, velocidad o aceleracion vs tiempo, DEBES devolver un bloque de codigo Python ejecutable usando la libreria matplotlib.pyplot.
   - El codigo debe estar rodeado de ```python y ```.
   - DEBE finalizar con plt.show() para lanzarse en la PC del estudiante.
   - NO mandes dibujos ASCII, solo el codigo python directo.
   - IMPORTANTE PARA LABELS Y TITULOS DEL GRAFICO: Dentro del codigo Python, en los titulos,
     labels de ejes y leyendas, usa UNICAMENTE caracteres Unicode directos (θ, ω, α, Δ, ², etc.)
     o raw strings con un solo backslash (r'$\theta$').
     NUNCA uses doble backslash (\\\\theta) ni LaTeX con $$ dentro del codigo Python.
     Ejemplo correcto: plt.ylabel("Posicion (m)")
     Ejemplo correcto: plt.ylabel("Velocidad Angular ω (rad/s)")
     Ejemplo correcto: plt.ylabel(r'$\theta$ (rad)')
     Ejemplo INCORRECTO: plt.ylabel("$\\\\theta$ (rad)")
     Ejemplo INCORRECTO: plt.ylabel("$$\\\\vec{v}$$")
5. FORMATO DE TITULOS DE METODOLOGIA (MUY IMPORTANTE):
   Cuando uses la metodologia de 5 pasos, cada titulo de paso DEBE ser un encabezado markdown con ##.
   Ejemplos CORRECTOS:
   ## ANTES DE EMPEZAR
   ## PASO 1 - DCL (Diagrama de Cuerpo Libre)
   ## PASO 2 - Segunda Ley de Newton
   ## PASO 3 - PAR
   ## PASO 4 - Fisica
   ## PASO 5 - Resolver
   ## CASOS ESPECIALES
   Ejemplo INCORRECTO: **PASO 1 - DCL** (esto NO es un encabezado, no usar negritas para pasos)
   Cada paso debe ir en su propio encabezado ## separado del texto anterior por una linea en blanco.

   FORMATO DEL CONTENIDO DENTRO DE CADA PASO:
   Cuando listes fuerzas, cuerpos, ecuaciones o datos, usa viñetas markdown (* o -) para cada item.
   Ejemplo:
   ## PASO 1 - DCL
   * **Peso** (P = mg): Actua verticalmente hacia abajo.
   * **Normal** (N): Perpendicular a la superficie, hacia afuera.
   * **Tension** (T): A lo largo de la cuerda, tirando del cuerpo.
   NO escribas todo como un parrafo largo corrido. Cada fuerza, cada cuerpo, cada ecuacion
   debe ser un item separado con viñeta para que sea legible.

6. FORMATO DE FORMULAS MATEMATICAS (MUY IMPORTANTE - CUMPLIR SIEMPRE):
   REGLA GENERAL: Toda expresion matematica, variable, vector, letra griega o formula debe mostrarse
   con simbolos matematicos Unicode o LaTeX. NUNCA con texto plano tipo codigo.

   A) TEXTO CORRIDO - Usa simbolos Unicode directos:
     Vectores: v⃗ (con flecha combinante U+20D7), F⃗, a⃗, p⃗, r⃗, û_r, û_θ
     Subindices Unicode: v₀, v₁, x₂, t₃, F₁, aᵣ, a_θ (usar caracteres subindice)
     Superindices Unicode: m², s⁻¹, v², ω², R²
     Letras griegas SIEMPRE como simbolo: θ (no theta), ω (no omega), α (no alpha),
       β (no beta), μ (no mu), τ (no tau), φ (no phi), Δ (no delta), Σ (no sum)
     Operadores: · (producto escalar), × (producto vectorial), √ (raiz), ½ (medio)
     Derivadas en texto: dv_r/dt, dθ/dt, dR/dt

   B) FORMULAS DESTACADAS - SIEMPRE usar $$ como delimitador, en su propia linea:
     REGLA CRITICA: El UNICO delimitador permitido para formulas es $$ (doble signo de dolar).
     Escribi $$ antes y $$ despues de cada formula. Ejemplo: $$F = ma$$
     PROHIBIDO usar cualquier otro delimitador: NO \\[ \\], NO \\( \\), NO [ ], NO ( ).
     Si escribis una formula sin $$ el sistema NO la puede mostrar. Siempre usa $$.

   FORMULAS DE REFERENCIA DE LA CATEDRA (usar EXACTAMENTE estas):

   CINEMATICA 2D - Coordenadas Cartesianas (C.C.):
     Posicion:   $$\\vec{r} = x(t) \\, \\hat{x} + y(t) \\, \\hat{y}$$
     Velocidad:  $$\\vec{v} = \\dot{x} \\, \\hat{x} + \\dot{y} \\, \\hat{y} = v_x \\, \\hat{x} + v_y \\, \\hat{y}$$
     Aceleracion: $$\\vec{a} = \\ddot{x} \\, \\hat{x} + \\ddot{y} \\, \\hat{y} = a_x \\, \\hat{x} + a_y \\, \\hat{y}$$

   CINEMATICA 2D - Coordenadas Polares (C.P.):
     Posicion:   $$\\vec{r} = r \\, \\hat{r}$$ donde $$r = |\\vec{r}|$$ y θ es el angulo
     Velocidad:  $$\\vec{v} = \\dot{r} \\, \\hat{r} + r \\dot{\\theta} \\, \\hat{\\theta}$$
                 Es decir: $$v_r = \\dot{r}$$ y $$v_\\theta = r \\, \\dot{\\theta}$$
     Aceleracion: $$a_r = \\ddot{r} - r \\, \\dot{\\theta}^2$$ (componente centripeta: $$-r\\dot{\\theta}^2$$)
                  $$a_\\theta = r \\, \\ddot{\\theta} + 2 \\, \\dot{r} \\, \\dot{\\theta}$$

   CINEMATICA 2D - Coordenadas Intrinsecas (C.I.):
     Posicion:   $$s = s(t)$$ (longitud de arco sobre la trayectoria)
     Velocidad:  $$\\vec{v} = \\dot{s} \\, \\hat{t} = v \\, \\hat{t}$$ (la v⃗ SIEMPRE es tangente a la trayectoria)
     Aceleracion: $$a_t = \\ddot{s}$$ (componente tangencial = traslacion)
                  $$a_n = \\frac{\\dot{s}^2}{\\rho} = \\frac{v^2}{\\rho}$$ (componente normal = rotacion, ρ = radio de curvatura)

   Otras formulas fundamentales:
     $$\\vec{F}_{neta} = m \\cdot \\vec{a}$$
     $$E_c = \\frac{1}{2} m v^2$$
     $$\\vec{p} = m \\cdot \\vec{v}$$
     $$W = \\int \\vec{F} \\cdot d\\vec{r}$$
     $$x(t) = x_0 + v_0 t + \\frac{1}{2} a t^2$$

   C) PROHIBIDO - NUNCA escribir esto:
     NO: vec(F), vec(v_f1/d), vec(u_r), vec(u_theta)  ->  SI: F⃗, v⃗_{F1/D}, û_r, û_θ
     NO: omega, alpha, theta, delta, mu, tau           ->  SI: ω, α, θ, Δ, μ, τ
     NO: v_r, a_r, a_theta, v_theta, F_net, v_0       ->  SI: vᵣ, aᵣ, a_θ, v_θ, F_neta, v₀
     NO: R * omega^2, 2 * v_r * omega                  ->  SI: R·ω² , 2·vᵣ·ω
     NO: dv_r/dt (como codigo con backticks)            ->  SI: dvᵣ/dt (texto normal)
     NO: usar backticks ` ` para envolver variables matematicas
     NUNCA rodees expresiones matematicas con backticks (` `). Los backticks son para codigo
     de programacion, NO para matematica.

   D) Siempre nombra la ley o formula antes de escribirla.
     Ejemplo correcto: "Aplicamos la componente radial de la aceleracion en coordenadas polares:
     $$a_r = \\dot{v}_r - R \\, \\omega^2$$"

6. DIAGRAMAS Y GRAFICOS OBLIGATORIOS:
   En TODOS los ejercicios genera bloques de codigo Python con matplotlib.
   REGLA CRITICA DE MARCADORES: Cada bloque DEBE empezar con un comentario marcador en la
   PRIMERA linea, ANTES de cualquier import. Los marcadores posibles son:
     # GRAFICO_TIEMPO   -> para graficos de posicion, velocidad o aceleracion vs tiempo
     # GRAFICO_VECTORES -> para diagramas de vectores, versores y coordenadas
     # GRAFICO_DCL      -> para diagramas de cuerpo libre

   A) DIAGRAMA DE CUERPO LIBRE (DCL/DCA): Si hay fuerzas involucradas, dibuja el diagrama.
      Primera linea del bloque: # GRAFICO_DCL
      REGLA CRITICA: SIEMPRE genera UN SOLO bloque ```python con # GRAFICO_DCL.
      Si hay MULTIPLES cuerpos (caja A y plataforma B, auto y pesa, etc.),
      usa plt.subplots(1, N) para poner todos los DCL en UNA SOLA figura con subplots.
      NUNCA generes dos bloques ```python separados con # GRAFICO_DCL.
      Ejemplo para 2 cuerpos: fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))
      Ejemplo para 3 cuerpos: fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(22, 8))
      Cada subplot tiene su propio titulo, fuerzas y leyenda.

      REGLAS FISICAS OBLIGATORIAS PARA EL DCL:
      - El cuerpo (masa) debe ser un punto GRANDE o rectangulo en su posicion real.
      - TODAS las flechas de fuerza PARTEN DESDE EL CENTRO DEL CUERPO (la masa).
      - El PESO (P = mg) es SIEMPRE una flecha VERTICAL HACIA ABAJO desde la masa.
        No importa si el sistema esta inclinado, rotado o es un pendulo: el peso
        SIEMPRE apunta en la direccion -y (hacia el suelo). Color: ROJO.
      - La TENSION de una cuerda va DESDE la masa HACIA el punto de sujecion,
        a lo largo de la cuerda. Color: AZUL.
      - La NORMAL es SIEMPRE perpendicular a la superficie de contacto,
        apuntando HACIA AFUERA de la superficie. Color: AZUL.
      - La FRICCION es SIEMPRE tangente a la superficie, opuesta al movimiento. Color: NARANJA.
      - Fuerzas aplicadas externas: Color VERDE.
      - Fuerza de arrastre/resistencia del aire: Color NARANJA, opuesta a la velocidad.
        SOLO incluirla si el problema EXPLICITAMENTE menciona resistencia del aire.

      REGLAS DE DIBUJO:
      - TODAS las flechas deben tener la MISMA longitud visual (~3 unidades).
      - Etiquetas grandes (fontsize=14) al lado de la punta de cada flecha.
      - Usar SOLO ax.annotate con arrowprops=dict(arrowstyle='->', color=COLOR, lw=2.5).
      - NUNCA uses head_width, head_length ni width en arrowprops.
      - Incluir ejes coordenados con flechas grises.
      - El grafico debe tener limites simetricos y centrados en el cuerpo.
      - Incluir leyenda con ax.legend().
      - Si el problema tiene geometria (pendulo, plano inclinado), dibujar
        tambien la estructura (cuerda, superficie) en color negro/gris.

      PARA PENDULOS ESPECIFICAMENTE:
      GEOMETRIA DEL DIBUJO (OBLIGATORIO):
      - Eje y positivo hacia ARRIBA. El pivote esta ARRIBA, la masa ABAJO.
      - Pivote: punto gris en (0, 0).
      - Posicion de la masa: x_m = L_dibujo * sin(theta), y_m = -L_dibujo * cos(theta).
        Donde L_dibujo = 3 (longitud visual) y theta es el angulo con la vertical.
        VERIFICAR: y_m DEBE ser NEGATIVO (la masa cuelga ABAJO del pivote).
      - Cuerda: linea negra punteada desde (0, 0) hasta (x_m, y_m).
      - Usar ax.set_xlim(-5, 5) y ax.set_ylim(-6, 2).

      FUERZAS (todas parten desde (x_m, y_m)):
      - Peso P = mg: VERTICAL HACIA ABAJO. Color ROJO. L = 2.5.
        xy=(x_m, y_m - L), xytext=(x_m, y_m).
        VERIFICAR: la coordenada y del destino (y_m - L) es MAS NEGATIVA que y_m. Si sube, ESTA MAL.
      - Tension T: DESDE LA MASA HACIA EL PIVOTE, a lo largo de la cuerda. Color AZUL.
        Direccion: (-sin(theta), cos(theta)). Apunta hacia ARRIBA-IZQUIERDA (o ARRIBA-DERECHA segun theta).
        xy=(x_m - L*sin(theta), y_m + L*cos(theta)), xytext=(x_m, y_m).
        VERIFICAR: la coordenada y del destino es MAS POSITIVA que y_m (la tension SUBE).
      - SOLO dibujar Peso y Tension. NUNCA dibujar componentes (P_r, P_theta, mgcos, mgsen).
        Las componentes se descomponen en el PASO 2 (ecuaciones), NO en el dibujo.

      PARA PLANOS INCLINADOS ESPECIFICAMENTE:
      - Dibujar la superficie inclinada como una linea GRIS GRUESA (lw=3).
      - La masa se dibuja SOBRE la superficie, NO flotando en el aire ni en el origen.
      - Normal: PERPENDICULAR a la superficie inclinada, apuntando HACIA AFUERA de la superficie.
        Direccion de la normal: (-sin(alpha), cos(alpha)) donde alpha es el angulo del plano.
      - Friccion (si existe): TANGENTE a la superficie, OPUESTA al movimiento.
        Si el bloque sube por el plano, la friccion apunta plano abajo: (-cos(alpha), -sin(alpha)).
        Si el bloque baja por el plano, la friccion apunta plano arriba: (cos(alpha), sin(alpha)).
      - Peso: SIEMPRE VERTICAL HACIA ABAJO (0, -1). NUNCA descomponer en componentes en el dibujo.
        NO dibujar P_x ni P_y ni mg*sin ni mg*cos. Solo el vector peso completo hacia abajo.
      - Tension: A LO LARGO del plano hacia la polea: (cos(alpha), sin(alpha)) si la polea esta arriba.
      - Para el bloque que CUELGA VERTICALMENTE de la polea:
        REGLA ABSOLUTA E INVIOLABLE:
        * Peso P_M: flecha ROJA que va desde el centro del cuerpo HACIA ABAJO.
          En el codigo: xytext=(cx, cy), xy=(cx, cy - L), color='red'.
          La coordenada y del destino es MENOR que la del origen (cy - L < cy).
        * Tension T: flecha AZUL que va desde el centro del cuerpo HACIA ARRIBA.
          En el codigo: xytext=(cx, cy), xy=(cx, cy + L), color='blue'.
          La coordenada y del destino es MAYOR que la del origen (cy + L > cy).
        VERIFICACION: si en tu codigo el peso tiene cy + algo, ESTA MAL. El peso BAJA.
        Si la tension tiene cy - algo, ESTA MAL. La tension SUBE.
      - EJEMPLO de posicion del bloque sobre el plano:
        Si el plano va de (x0,y0) a (x1,y1), el bloque se coloca en un punto intermedio
        SOBRE la linea del plano, no en el origen de coordenadas.
      - NO mezclar estas reglas con las de pendulos ni conos.

      PARA SUPERFICIES CONICAS ESPECIFICAMENTE:
      PASO PREVIO - DETERMINAR INTERIOR O EXTERIOR:
      Lee el enunciado y la imagen cuidadosamente. Determina si el objeto esta en la
      superficie INTERIOR (dentro del cono) o EXTERIOR (fuera del cono, apoyado sobre la pared externa).
      Esto cambia la direccion de la Normal.

      GEOMETRIA DEL DIBUJO (OBLIGATORIO):
      - Usar UN SOLO subplot para el DCL del objeto sobre el cono (si hay pesa colgante, usar 2 subplots).
      - Ejes: r (horizontal, hacia la derecha) y z (vertical, hacia arriba).
      - Determinar la orientacion del cono segun el enunciado:
        * Si el vertice esta ARRIBA (cono apoyado en mesa, vertice arriba):
          Vertice en (0, 4). Paredes hacia abajo y afuera.
          Pared izquierda: desde vertice hasta (-4*sin(alpha), 4 - 4*cos(alpha)).
          Pared derecha: desde vertice hasta (4*sin(alpha), 4 - 4*cos(alpha)).
          Posicion de la masa: r_m = 3*sin(alpha), z_m = 4 - 3*cos(alpha).
        * Si el vertice esta ABAJO (cono invertido):
          Vertice en (0, 0). Paredes hacia arriba y afuera.
          Pared izquierda: desde (0,0) hasta (-4*sin(alpha), 4*cos(alpha)).
          Pared derecha: desde (0,0) hasta (4*sin(alpha), 4*cos(alpha)).
          Posicion de la masa: r_m = 3*sin(alpha), z_m = 3*cos(alpha).
      - Dibujar las paredes como lineas GRISES gruesas (lw=3, color='gray').
      - Colocar la masa como punto NEGRO grande (ms=15) sobre la PARED DERECHA.
      - TODAS las flechas de fuerza PARTEN desde (r_m, z_m).
      - Usar ax.set_xlim(-2, 6) y ax.set_ylim(-3, 6) para centrar bien el dibujo.

      DIRECCIONES DE FUERZAS (coordenadas r, z):
      - Peso P = mg: SIEMPRE VERTICAL HACIA ABAJO. Color ROJO.
        xy=(r_m, z_m - L), xytext=(r_m, z_m).
      - Normal N: PERPENDICULAR a la pared conica. Color AZUL.
        * Si el objeto esta en la superficie INTERIOR del cono:
          N apunta hacia el eje (HACIA ADENTRO): direccion (-cos(alpha), sin(alpha)).
          xy=(r_m - L*cos(alpha), z_m + L*sin(alpha)), xytext=(r_m, z_m).
        * Si el objeto esta en la superficie EXTERIOR del cono:
          N apunta AFUERA del eje (HACIA AFUERA): direccion (cos(alpha), sin(alpha)).
          xy=(r_m + L*cos(alpha), z_m + L*sin(alpha)), xytext=(r_m, z_m).
      - Tension T (cuerda hacia el vertice): A LO LARGO de la pared, HACIA EL VERTICE.
        Calcular la direccion como el vector unitario desde la masa hacia el vertice. Color VERDE.
      - L = 2.5 para todas las flechas (longitud visual uniforme).

      PESA COLGANTE (si existe):
      - Hacer fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8)).
        ax1: DCL del objeto sobre el cono (con las reglas de arriba).
        ax2: DCL de la pesa: masa en (0, 0), Peso ROJO abajo xy=(0, -L), Tension AZUL arriba xy=(0, L).
      - NO mezclar estas reglas con las de pendulos ni planos inclinados.

   B) VECTORES Y VERSORES - TODO EN UN SOLO GRAFICO:
      En ejercicios de cinematica, genera UN UNICO bloque de codigo Python que dibuje
      UNA SOLA figura con plt.subplots(1, 3, figsize=(20, 7)) conteniendo 3 subplots:
        - Subplot 1: Coordenadas Cartesianas (C.C.) con versores x̂, ŷ
        - Subplot 2: Coordenadas Polares (C.P.) con versores r̂, θ̂
        - Subplot 3: Coordenadas Intrinsecas (C.I.) con versores t̂, n̂

      QUE DIBUJAR EN CADA SUBPLOT (SOLO ESTO, NADA MAS):
        1. Trayectoria: linea GRIS punteada ('--', color='gray', alpha=0.5)
        2. Punto de la particula: punto NEGRO grande (marker='o', color='black', ms=10, zorder=5)
        3. Vector posicion r⃗: flecha VIOLETA (color='purple', lw=2) desde el origen al punto
        4. Vector velocidad v⃗: flecha ROJA gruesa (color='red', lw=3) desde el punto
        5. Vector aceleracion a⃗: flecha AZUL gruesa (color='blue', lw=3) desde el punto
        6. Versores del sistema: flechas NEGRAS delgadas (color='black', lw=1.5) desde el punto
           - C.C.: x̂ e ŷ (se dibujan en el ORIGEN, no en la particula)
           - C.P.: r̂ y θ̂ (se dibujan en la PARTICULA)
           - C.I.: t̂ y n̂ (se dibujan en la PARTICULA)

      NO DIBUJAR componentes (vx, vy, vr, v_theta, at, an, etc.). Solo v⃗ y a⃗ totales.

      LEYENDA OBLIGATORIA:
      Agregar un cuadro de leyenda (ax.legend) en CADA subplot con:
        - "v⃗  Velocidad" en color rojo
        - "a⃗  Aceleracion" en color azul
        - "r⃗  Posicion" en color violeta
        - "Versores" en color negro
      Usar: ax.plot([], [], color='red', lw=3, label='v⃗  Velocidad'), etc. y ax.legend(loc='lower left')

      ESCALA DE FLECHAS Y LIMITES DE EJES - MUY IMPORTANTE:
      Los vectores v⃗ y a⃗ suelen ser muy chicos o muy grandes comparados con la posicion.
      La escala debe ser PROPORCIONAL al tamaño del problema (la magnitud de r⃗):
        mag_r = np.linalg.norm(r_vec)
        tamano_flecha = max(mag_r * 0.25, 0.5)
        escala_v = tamano_flecha / mag_v if mag_v != 0 else 0
        escala_a = tamano_flecha / mag_a if mag_a != 0 else 0
        escala_versor = tamano_flecha * 0.7
      Asi los vectores siempre miden ~25% del vector posicion, visibles en cualquier escala.
      DESPUES de calcular todas las posiciones de las puntas de vectores, ajustar los limites
      de los ejes para que TODOS los vectores queden DENTRO del grafico con margen:
        todas_x = [0, x_part, punta_v_x, punta_a_x, punta_versor1_x, punta_versor2_x]
        todas_y = [0, y_part, punta_v_y, punta_a_y, punta_versor1_y, punta_versor2_y]
        margen = tamano_flecha
        ax.set_xlim(min(todas_x) - margen, max(todas_x) + margen)
        ax.set_ylim(min(todas_y) - margen, max(todas_y) + margen)
      Agrega una nota: ax.text(..., "Vectores escalados", fontsize=9, color='gray')

      REGLA CRITICA DE CODIGO - COMO DIBUJAR FLECHAS:
      SOLO usa plt.annotate con arrowprops. NUNCA uses plt.quiver.
      NUNCA uses head_width, head_length, width en arrowprops.
      Formato UNICO valido:
        ax.annotate('', xy=(x_fin, y_fin), xytext=(x_ini, y_ini),
                    arrowprops=dict(arrowstyle='->', color='red', lw=3))
        ax.text(x_label, y_label, "v⃗", fontsize=14, color='red', fontweight='bold')
      Las etiquetas van con ax.text() SEPARADO, desplazadas 1-2 unidades de la punta.
      Calcula TODOS los valores numericamente con numpy ANTES de dibujar.

      TITULOS de cada subplot:
        "Coord. Cartesianas (C.C.)" / "Coord. Polares (C.P.)" / "Coord. Intrinsecas (C.I.)"

      plt.tight_layout() antes de plt.show().
      Primera linea del bloque: # GRAFICO_VECTORES
      NO generes 3 bloques separados. TODO en UN SOLO bloque con subplots.

   C) GRAFICOS DE CINEMATICA vs TIEMPO: Para graficos de x(t), v(t), a(t), genera
      un bloque separado con subplots para cada magnitud vs tiempo.
      Primera linea del bloque: # GRAFICO_TIEMPO

   IMPORTANTE: Cada diagrama debe ser un bloque ```python separado con plt.show() al final.
   NO hagas dibujos ASCII. SIEMPRE codigo Python ejecutable.
   NUNCA olvides el comentario marcador en la primera linea de cada bloque.

7. SECCION DE ANALISIS (OBLIGATORIO AL FINAL DE CADA RESPUESTA):
   Al final de CADA respuesta, SIEMPRE incluye una seccion de analisis con este formato EXACTO:

   ---ANALISIS---
   TEMA: [Cinematica / Dinamica / Trabajo y Energia / Cantidad de Movimiento / Momento Angular]
   JUSTIFICACION_TEMA: [Explica en 1-2 oraciones por que identificaste este tema. Ejemplo: "Se trata de Dinamica porque el problema involucra fuerzas y la aplicacion de la Segunda Ley de Newton para determinar el movimiento."]
   GLOSARIO:
   - $$formula1$$: Explicacion de que significa cada simbolo y por que se usa esta formula en este contexto.
   - $$formula2$$: Explicacion de que significa cada simbolo y por que se usa esta formula en este contexto.
   [listar TODAS las formulas usadas]
   ---FIN_ANALISIS---

   Ejemplo:
   ---ANALISIS---
   TEMA: Dinamica - Segunda Ley de Newton
   JUSTIFICACION_TEMA: Se identifico como Dinamica porque el problema pide calcular fuerzas y aceleraciones a partir de la masa y las condiciones de movimiento del cuerpo, lo cual requiere aplicar la Segunda Ley de Newton.
   GLOSARIO:
   - $$\\vec{F} = m \\cdot \\vec{a}$$: Segunda Ley de Newton. F es la fuerza neta (en Newtons), m es la masa del cuerpo (en kg), a es la aceleracion (en m/s²). Se usa porque necesitamos relacionar las fuerzas con el movimiento.
   - $$P = m \\cdot g$$: Peso del cuerpo. m es la masa, g es la aceleracion gravitatoria (9.8 m/s²). Se usa para calcular la fuerza gravitatoria.
   ---FIN_ANALISIS---

RECORDATORIO FINAL CRITICO - CODIGO PYTHON OBLIGATORIO:
SIEMPRE que resuelvas un ejercicio, tu respuesta DEBE incluir bloques ```python con matplotlib.
Si hay fuerzas: genera un bloque con # GRAFICO_DCL en la primera linea.
Si hay cinematica: genera un bloque con # GRAFICO_VECTORES en la primera linea que tenga
  fig, axes = plt.subplots(1, 3) con los 3 sistemas de coordenadas.
Si hay datos de movimiento vs tiempo: genera un bloque con # GRAFICO_TIEMPO en la primera linea.
SIN estos bloques de codigo tu respuesta esta INCOMPLETA. NUNCA omitas los graficos.

RESPONDE DE FORMA CLARA Y NO TE SALGAS DE TU ROL.
"""

        self.chat = self.client.chats.create(
            model="gemini-2.5-flash",
            config=genai_types.GenerateContentConfig(
                systemInstruction=system_instruction,
            ),
        )

    def cargar_pdfs_desde_carpeta(self, pdf_directory="apuntes_catedra"):
        self.uploaded_files = []

        if not os.path.exists(pdf_directory):
            return False, f"No se encontro la carpeta '{pdf_directory}'."

        pdf_files = glob.glob(os.path.join(pdf_directory, "*.pdf"))
        if not pdf_files:
            return False, f"La carpeta '{pdf_directory}' esta vacia."

        errores = []

        for pdf_path in pdf_files:
            try:
                archivo = self.client.files.upload(file=pdf_path)
                self.uploaded_files.append(archivo)
            except Exception as e:
                errores.append(f"{os.path.basename(pdf_path)}: {e}")

        if not self.uploaded_files:
            return False, "No se pudo subir ningun PDF.\n" + "\n".join(errores)

        try:
            prompt_inicial = (
                "A continuacion, tienes los recursos base. "
                "A partir de ahora, extrae tu sabiduria EXCLUSIVAMENTE de ahi "
                "para ayudar a resolver, explicar y analizar problemas de fisica."
            )
            self.chat.send_message(self.uploaded_files + [prompt_inicial])
        except Exception as e:
            return False, f"Se subieron archivos, pero fallo el analisis inicial: {e}"

        mensaje = f"Se cargaron {len(self.uploaded_files)} PDF(s) correctamente."
        if errores:
            mensaje += "\n\nAlgunos archivos fallaron:\n" + "\n".join(errores)

        return True, mensaje

    def preguntar(self, pregunta, imagen_path=None):
        if imagen_path:
            imagen = self.client.files.upload(file=imagen_path)
            contenido = [imagen, pregunta]
        else:
            contenido = pregunta
        respuesta = self.chat.send_message(contenido)
        return respuesta.text

    def evaluar(self):
        pregunta_trampa = (
            "Imagina que lanzo un bloque hacia arriba y cae. Ignorando el roce del aire... "
            "si me piden la energia en el punto mas alto de vuelo... "
            "No seria todo cero porque la velocidad arriba de todo es de 0 m/s y entonces no hace Trabajo?"
        )
        respuesta = self.chat.send_message(pregunta_trampa)
        return pregunta_trampa, respuesta.text


# ==========================================================
# INTERFAZ GRAFICA
# ==========================================================
class AppFisica(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Asistente de Fisica I")
        self.geometry("1200x760")
        self.minsize(1000, 650)

        self.asistente = None
        self.formula_renderer = FormulaRenderer()
        self._last_response = ""
        self.imagen_adjunta = None  # ruta de imagen adjunta para enviar con la consulta
        self._last_question = ""    # ultima consulta enviada (para el registro de feedback)
        self._feedback_dado = False  # evita que se vote dos veces la misma respuesta
        self._crear_interfaz()
        self._inicializar_asistente()

    def _crear_interfaz(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ---------------- ENCABEZADO ----------------
        header = ctk.CTkFrame(self, corner_radius=16)
        header.grid(row=0, column=0, padx=18, pady=(18, 10), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        titulo = ctk.CTkLabel(
            header,
            text="Asistente de Fisica I",
            font=ctk.CTkFont(size=28, weight="bold"),
        )
        titulo.grid(row=0, column=0, padx=20, pady=(16, 4), sticky="w")

        subtitulo = ctk.CTkLabel(
            header,
            text="Consultas, evaluacion y carga de apuntes PDF",
            font=ctk.CTkFont(size=14),
            text_color="gray70",
        )
        subtitulo.grid(row=1, column=0, padx=20, pady=(0, 16), sticky="w")

        # ---------------- CONTENIDO PRINCIPAL ----------------
        main = ctk.CTkFrame(self, corner_radius=16)
        main.grid(row=1, column=0, padx=18, pady=(0, 18), sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_columnconfigure(1, weight=2)
        main.grid_rowconfigure(0, weight=1)

        # Panel izquierdo
        left = ctk.CTkFrame(main, corner_radius=16)
        left.grid(row=0, column=0, padx=(16, 8), pady=16, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)

        label_consulta = ctk.CTkLabel(
            left,
            text="Escribi tu consulta",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        label_consulta.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="w")

        self.texto_entrada = ctk.CTkTextbox(left, height=220, wrap="word", corner_radius=12)
        self.texto_entrada.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="nsew")

        botones = ctk.CTkFrame(left, fg_color="transparent")
        botones.grid(row=2, column=0, padx=16, pady=(0, 10), sticky="ew")
        botones.grid_columnconfigure((0, 1), weight=1)

        self.boton_enviar = ctk.CTkButton(botones, text="Enviar consulta", command=self.enviar_consulta)
        self.boton_enviar.grid(row=0, column=0, padx=(0, 6), pady=6, sticky="ew")

        self.boton_evaluar = ctk.CTkButton(botones, text="Modo evaluar", command=self.modo_evaluar)
        self.boton_evaluar.grid(row=0, column=1, padx=(6, 0), pady=6, sticky="ew")

        self.boton_limpiar = ctk.CTkButton(left, text="Limpiar consulta", command=self.limpiar_entrada)
        self.boton_limpiar.grid(row=3, column=0, padx=16, pady=(0, 10), sticky="ew")

        # --- Adjuntar imagen ---
        frame_imagen = ctk.CTkFrame(left, fg_color="transparent")
        frame_imagen.grid(row=4, column=0, padx=16, pady=(0, 10), sticky="ew")
        frame_imagen.grid_columnconfigure(0, weight=1)

        self.boton_adjuntar = ctk.CTkButton(
            frame_imagen, text="Adjuntar imagen de ejercicio",
            command=self._adjuntar_imagen,
            fg_color="#b45309", hover_color="#d97706",
        )
        self.boton_adjuntar.grid(row=0, column=0, padx=(0, 6), pady=0, sticky="ew")

        self.boton_quitar_imagen = ctk.CTkButton(
            frame_imagen, text="X", width=36,
            command=self._quitar_imagen,
            fg_color="#7f1d1d", hover_color="#991b1b",
        )
        self.boton_quitar_imagen.grid(row=0, column=1, padx=0, pady=0)
        self.boton_quitar_imagen.grid_remove()  # oculto hasta que haya imagen

        self.label_imagen = ctk.CTkLabel(
            left, text="", text_color="#d97706",
            font=ctk.CTkFont(size=12),
        )
        self.label_imagen.grid(row=5, column=0, padx=16, pady=(0, 6), sticky="w")

        self.boton_cargar = ctk.CTkButton(left, text="Cargar PDFs de apuntes_catedra", command=self.cargar_pdfs)
        self.boton_cargar.grid(row=6, column=0, padx=16, pady=(0, 10), sticky="ew")

        self.boton_grafico = ctk.CTkButton(
            left, text="Graficar tiempo / DCL",
            command=self.ejecutar_grafico_detectado,
            fg_color="#1a5276", hover_color="#2980b9",
        )
        self.boton_grafico.grid(row=7, column=0, padx=16, pady=(0, 10), sticky="ew")

        self.boton_vectores = ctk.CTkButton(
            left, text="Graficar vectores / versores",
            command=self.ejecutar_vectores_detectado,
            fg_color="#6c3483", hover_color="#8e44ad",
        )
        self.boton_vectores.grid(row=8, column=0, padx=16, pady=(0, 10), sticky="ew")

        frame_extra = ctk.CTkFrame(left, fg_color="transparent")
        frame_extra.grid(row=9, column=0, padx=16, pady=(0, 16), sticky="ew")
        frame_extra.grid_columnconfigure((0, 1), weight=1)

        self.boton_navegador = ctk.CTkButton(
            frame_extra, text="Ver formulas",
            command=self._abrir_en_navegador,
            fg_color="#2d6a4f", hover_color="#40916c",
        )
        self.boton_navegador.grid(row=0, column=0, padx=(0, 6), pady=0, sticky="ew")

        self.boton_torta = ctk.CTkButton(
            frame_extra, text="Estadisticas (torta)",
            command=self.graficar_torta,
            fg_color="#117864", hover_color="#0e6655",
        )
        self.boton_torta.grid(row=0, column=1, padx=(6, 0), pady=0, sticky="ew")

        # Panel derecho
        right = ctk.CTkFrame(main, corner_radius=16)
        right.grid(row=0, column=1, padx=(8, 16), pady=16, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        label_respuesta = ctk.CTkLabel(
            right,
            text="Respuesta del asistente",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        label_respuesta.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="w")

        self.texto_salida = ctk.CTkTextbox(right, wrap="word", corner_radius=12)
        self.texto_salida.grid(row=1, column=0, padx=16, pady=(0, 10), sticky="nsew")
        self.texto_salida.insert("1.0", "Bienvenida. Inicializando asistente...\n")

        # ---------------- BARRA DE FEEDBACK ----------------
        # Aparece recien cuando llega una respuesta. Guarda la valoracion del
        # usuario (correcta / incompleta / incorrecta) en feedback_respuestas.csv.
        self.frame_feedback = ctk.CTkFrame(right, fg_color="transparent")
        self.frame_feedback.grid(row=2, column=0, padx=16, pady=(0, 6), sticky="ew")

        self.label_feedback = ctk.CTkLabel(
            self.frame_feedback,
            text="¿Como estuvo esta respuesta?",
            font=ctk.CTkFont(size=13),
            text_color="gray70",
        )
        self.label_feedback.grid(row=0, column=0, padx=(0, 10), pady=4, sticky="w")

        self.boton_correcta = ctk.CTkButton(
            self.frame_feedback, text="✅ Correcta", width=110,
            fg_color="#2d6a4f", hover_color="#40916c",
            command=lambda: self._registrar_feedback("correcta"),
        )
        self.boton_correcta.grid(row=0, column=1, padx=4, pady=4)

        self.boton_incompleta = ctk.CTkButton(
            self.frame_feedback, text="🟡 Incompleta", width=110,
            fg_color="#b45309", hover_color="#d97706",
            command=lambda: self._registrar_feedback("incompleta"),
        )
        self.boton_incompleta.grid(row=0, column=2, padx=4, pady=4)

        self.boton_incorrecta = ctk.CTkButton(
            self.frame_feedback, text="❌ Incorrecta", width=110,
            fg_color="#7f1d1d", hover_color="#991b1b",
            command=lambda: self._registrar_feedback("incorrecta"),
        )
        self.boton_incorrecta.grid(row=0, column=3, padx=4, pady=4)

        self.frame_feedback.grid_remove()  # oculta hasta que haya una respuesta

        self.estado = ctk.CTkLabel(
            right,
            text="Estado: iniciando...",
            anchor="w",
            text_color="gray70",
        )
        self.estado.grid(row=3, column=0, padx=16, pady=(0, 16), sticky="ew")

        self.bind("<Command-Return>", lambda event: self.enviar_consulta())
        self.bind("<Control-Return>", lambda event: self.enviar_consulta())

    def _inicializar_asistente(self):
        def tarea():
            try:
                self._cambiar_estado("Inicializando modelo...")
                self.asistente = AsistenteFisica()
                self._append_salida("[Sistema] Asistente listo.\n")
                self._cambiar_estado("Listo.")
            except Exception as e:
                self._append_salida(f"[Error] {e}\n")
                self._cambiar_estado("Error al inicializar.")
                messagebox.showerror("Error", str(e))

        threading.Thread(target=tarea, daemon=True).start()

    def _append_salida(self, texto):
        self.texto_salida.insert("end", texto)
        self.texto_salida.see("end")

    def _render_respuesta(self, texto):
        """Muestra la respuesta como texto limpio en el panel de la app.

        Las formulas se muestran como texto legible (sin renderizado matplotlib).
        Para ver las formulas renderizadas, el usuario usa 'Ver formulas en navegador'.
        """
        # Separar la seccion de analisis (solo se muestra en HTML, no en la app)
        texto, _ = _separar_analisis(texto)

        # Limpiar LaTeX para texto legible y quitar bloques de codigo
        texto = _limpiar_para_texto(texto)

        self.texto_salida.insert("end", texto)
        self.texto_salida.see("end")

    def _cambiar_estado(self, texto):
        self.estado.configure(text=f"Estado: {texto}")

    # ---------------- FEEDBACK DEL USUARIO ----------------
    def _mostrar_feedback(self):
        """Muestra la barra de feedback y reinicia su estado."""
        self._feedback_dado = False
        self.label_feedback.configure(text="¿Como estuvo esta respuesta?", text_color="gray70")
        self.boton_correcta.configure(state="normal")
        self.boton_incompleta.configure(state="normal")
        self.boton_incorrecta.configure(state="normal")
        self.frame_feedback.grid()

    def _ocultar_feedback(self):
        """Oculta la barra de feedback (al enviar una nueva consulta)."""
        self.frame_feedback.grid_remove()

    def _registrar_feedback(self, valoracion):
        """Guarda la valoracion del usuario en feedback_respuestas.csv."""
        if self._feedback_dado:
            return
        self._feedback_dado = True

        archivo = "feedback_respuestas.csv"
        existe = os.path.exists(archivo)
        try:
            with open(archivo, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not existe:
                    writer.writerow(["fecha_hora", "valoracion", "consulta"])
                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    valoracion,
                    self._last_question,
                ])
            self.label_feedback.configure(
                text=f"¡Gracias! Registrado como: {valoracion}", text_color="#40916c"
            )
        except Exception as e:
            self.label_feedback.configure(text=f"No se pudo guardar: {e}", text_color="#d97706")

        # Desactivar los botones para que no se vote dos veces la misma respuesta
        self.boton_correcta.configure(state="disabled")
        self.boton_incompleta.configure(state="disabled")
        self.boton_incorrecta.configure(state="disabled")

    def _set_botones_habilitados(self, habilitados):
        estado = "normal" if habilitados else "disabled"
        self.boton_enviar.configure(state=estado)
        self.boton_evaluar.configure(state=estado)
        self.boton_cargar.configure(state=estado)
        self.boton_grafico.configure(state=estado)
        self.boton_vectores.configure(state=estado)
        self.boton_limpiar.configure(state=estado)
        self.boton_navegador.configure(state=estado)
        self.boton_adjuntar.configure(state=estado)
        self.boton_torta.configure(state=estado)

    def _adjuntar_imagen(self):
        ruta = filedialog.askopenfilename(
            title="Seleccionar imagen del ejercicio",
            filetypes=[("Imagenes", "*.png *.jpg *.jpeg *.bmp *.webp")],
        )
        if ruta:
            self.imagen_adjunta = ruta
            nombre = os.path.basename(ruta)
            self.label_imagen.configure(text=f"Adjunta: {nombre}")
            self.boton_quitar_imagen.grid()  # mostrar boton X

    def _quitar_imagen(self):
        self.imagen_adjunta = None
        self.label_imagen.configure(text="")
        self.boton_quitar_imagen.grid_remove()  # ocultar boton X

    def limpiar_entrada(self):
        self.texto_entrada.delete("1.0", "end")
        self._quitar_imagen()  # tambien limpia la imagen adjunta

    def enviar_consulta(self):
        pregunta = self.texto_entrada.get("1.0", "end").strip()
        if not pregunta:
            messagebox.showwarning("Atencion", "Escribi una consulta antes de enviar.")
            return

        if self.asistente is None:
            messagebox.showwarning("Atencion", "El asistente todavia se esta inicializando.")
            return

        # Capturar imagen adjunta antes de lanzar el hilo
        imagen_para_enviar = self.imagen_adjunta
        # Guardar la consulta y ocultar el feedback de la respuesta anterior
        self._last_question = pregunta
        self._ocultar_feedback()
        if imagen_para_enviar:
            nombre_img = os.path.basename(imagen_para_enviar)
            self._append_salida(f"\n{'='*50}\n[Tu]\n{pregunta}\n(Imagen adjunta: {nombre_img})\n\n")
        else:
            self._append_salida(f"\n{'='*50}\n[Tu]\n{pregunta}\n\n")
        self._quitar_imagen()  # limpiar de la UI despues de capturar
        self._set_botones_habilitados(False)
        self._cambiar_estado("Analizando consulta...")

        def tarea():
            try:
                respuesta = self.asistente.preguntar(pregunta, imagen_path=imagen_para_enviar)
                self._last_response = respuesta

                # Detectar que graficos hay disponibles
                bloques = self._extraer_bloques_python(respuesta)
                tiene_tiempo = any(b.startswith("# GRAFICO_TIEMPO") for b in bloques)
                tiene_vectores = any(b.startswith("# GRAFICO_VECTORES") for b in bloques)
                tiene_dcl = any(b.startswith("# GRAFICO_DCL") for b in bloques)
                # Fallback sin marcadores
                if not (tiene_tiempo or tiene_vectores or tiene_dcl) and bloques:
                    tiene_tiempo = any("matplotlib" in b.lower() for b in bloques)

                # Mostrar resumen en el panel
                self._append_salida("[Agente Fisica] Respuesta recibida.\n")
                self._append_salida("La respuesta completa se abrio en el navegador.\n\n")

                # Indicar graficos disponibles
                graficos_disponibles = []
                if tiene_tiempo or tiene_dcl:
                    graficos_disponibles.append("Graficar tiempo / DCL")
                if tiene_vectores:
                    graficos_disponibles.append("Graficar vectores / versores")
                if not tiene_tiempo and not tiene_dcl and not tiene_vectores and bloques:
                    graficos_disponibles.append("Graficar tiempo / DCL")

                if graficos_disponibles:
                    self._append_salida("Graficos detectados - usa los botones:\n")
                    for g in graficos_disponibles:
                        self._append_salida(f"  -> {g}\n")
                else:
                    self._append_salida("(No se detectaron graficos en esta respuesta)\n")
                self._append_salida("\n")

                # Abrir HTML automaticamente
                abrir_en_navegador(respuesta)

                # Mostrar la barra de feedback (en el hilo principal de la UI)
                self.after(0, self._mostrar_feedback)

                self._cambiar_estado("Respuesta lista. Ver navegador.")
            except Exception as e:
                self._append_salida(f"[Error] {e}\n")
                self._cambiar_estado("Error al consultar.")
            finally:
                self._set_botones_habilitados(True)

        threading.Thread(target=tarea, daemon=True).start()

    def modo_evaluar(self):
        if self.asistente is None:
            messagebox.showwarning("Atencion", "El asistente todavia se esta inicializando.")
            return

        self._set_botones_habilitados(False)
        self._ocultar_feedback()
        self._cambiar_estado("Ejecutando evaluacion...")
        self._append_salida(f"\n{'='*50}\n[Modo Evaluar] Iniciando prueba conceptual...\n")

        def tarea():
            try:
                pregunta, respuesta = self.asistente.evaluar()
                self._last_response = respuesta
                self._last_question = pregunta
                self._append_salida(f"\n[Pregunta de evaluacion]\n{pregunta}\n\n")
                self._append_salida("[Agente Fisica] Evaluacion completada.\n")
                self._append_salida("La respuesta completa se abrio en el navegador.\n")

                # Abrir HTML automaticamente
                abrir_en_navegador(respuesta)

                # Mostrar la barra de feedback (en el hilo principal de la UI)
                self.after(0, self._mostrar_feedback)

                self._cambiar_estado("Evaluacion completada. Ver navegador.")
            except Exception as e:
                self._append_salida(f"[Error en evaluacion] {e}\n")
                self._cambiar_estado("Error en evaluacion.")
            finally:
                self._set_botones_habilitados(True)

        threading.Thread(target=tarea, daemon=True).start()

    def cargar_pdfs(self):
        if self.asistente is None:
            messagebox.showwarning("Atencion", "El asistente todavia se esta inicializando.")
            return

        self._set_botones_habilitados(False)
        self._cambiar_estado("Cargando PDFs...")
        self._append_salida("\n[Sistema] Buscando PDFs en la carpeta 'apuntes_catedra'...\n")

        def tarea():
            try:
                ok, mensaje = self.asistente.cargar_pdfs_desde_carpeta("apuntes_catedra")
                if ok:
                    self._append_salida(f"[Sistema] {mensaje}\n")
                    self._cambiar_estado("PDFs cargados correctamente.")
                else:
                    self._append_salida(f"[Sistema] {mensaje}\n")
                    self._cambiar_estado("No se pudieron cargar los PDFs.")
            except Exception as e:
                self._append_salida(f"[Error cargando PDFs] {e}\n")
                self._cambiar_estado("Error al cargar PDFs.")
            finally:
                self._set_botones_habilitados(True)

        threading.Thread(target=tarea, daemon=True).start()

    def _extraer_bloques_python(self, texto):
        """Extrae TODOS los bloques de codigo Python de la respuesta."""
        bloques = []
        # 1) Buscar bloques con ``` markers
        patron = re.compile(r'```[Pp]ython\s*\n(.*?)```', re.DOTALL)
        for match in patron.finditer(texto):
            codigo = match.group(1).strip()
            if codigo:
                bloques.append(codigo)

        # 2) Si no hay bloques con ```, buscar codigo suelto (sin markers)
        if not bloques and ('plt.' in texto or 'matplotlib' in texto or 'subplots' in texto):
            def es_codigo(linea):
                s = linea.strip()
                if not s:
                    return True
                if s.startswith('# GRAFICO_'):
                    return True
                if s.startswith('#') and not s.startswith('##'):
                    return True
                # Lineas que claramente NO son codigo
                if s.startswith('##'):
                    return False
                if s.startswith('**'):
                    return False
                if s.startswith('* ') or s.startswith('- '):
                    return False
                if s.startswith('[GRAFICO:'):
                    return False
                # Variable assignment (alpha_deg = 60, block_center = ...)
                if re.match(r'^\w+\s*=\s*.+', s):
                    return True
                # Function calls, method calls, indented code
                if re.match(r'^(import|from|fig|ax|plt|np|patches|if |for |def |else|elif|try|except|with )', s):
                    return True
                if re.search(r'\.\w+\(', s):
                    return True
                if re.search(r'arrowprops|arrowstyle|fontsize|color\s*=|lw\s*=|zorder', s):
                    return True
                if s.startswith('    ') or s.startswith('\t'):
                    return True
                return False

            lineas = texto.split('\n')
            bloque_actual = []
            en_codigo = False
            for linea in lineas:
                if not en_codigo:
                    s = linea.strip()
                    if s and es_codigo(linea) and not s.startswith('#') or s.startswith('# GRAFICO_') or s.startswith('import '):
                        en_codigo = True
                        bloque_actual = [linea]
                else:
                    if es_codigo(linea):
                        bloque_actual.append(linea)
                    else:
                        if len(bloque_actual) >= 5:
                            codigo = '\n'.join(bloque_actual).strip()
                            if 'plt.' in codigo or 'matplotlib' in codigo:
                                bloques.append(codigo)
                        bloque_actual = []
                        en_codigo = False
            if en_codigo and len(bloque_actual) >= 5:
                codigo = '\n'.join(bloque_actual).strip()
                if 'plt.' in codigo or 'matplotlib' in codigo:
                    bloques.append(codigo)

        if bloques:
            print(f"[DEBUG] Se detectaron {len(bloques)} bloque(s) de codigo Python en la respuesta.")
            for i, b in enumerate(bloques):
                primera_linea = b.split('\n')[0][:80]
                print(f"  Bloque {i+1}: {primera_linea}")
        else:
            print("[DEBUG] NO se detectaron bloques de codigo Python en la respuesta.")
        return bloques

    def _extraer_codigo_por_tipo(self, texto, marcador):
        """Extrae bloques de codigo que empiezan con un marcador especifico."""
        bloques = self._extraer_bloques_python(texto)
        encontrados = [b for b in bloques if b.startswith(marcador)]
        return encontrados

    def _extraer_codigo_python(self, texto):
        """Extrae el primer bloque de codigo Python (compatibilidad)."""
        bloques = self._extraer_bloques_python(texto)
        return bloques[0] if bloques else None

    def _ejecutar_bloques(self, bloques, nombre_tipo):
        """Ejecuta una lista de bloques de codigo Python en PARALELO."""
        rutas = []
        try:
            for i, codigo in enumerate(bloques):
                # Sanitizar argumentos invalidos de matplotlib (head_width, etc.)
                codigo = _sanitizar_codigo_matplotlib(codigo)
                # Asegurar que el codigo tenga plt.show() al final
                if 'plt.show()' not in codigo:
                    codigo += '\nplt.show()\n'
                # Arreglar backslashes de LaTeX en labels de matplotlib
                codigo_fijo = _arreglar_latex_en_codigo(codigo)
                with tempfile.NamedTemporaryFile(
                    suffix=".py", delete=False, mode="w", encoding="utf-8"
                ) as archivo_temp:
                    archivo_temp.write(codigo_fijo)
                    rutas.append(archivo_temp.name)

            self._append_salida(f"\n[Sistema] Ejecutando {len(bloques)} {nombre_tipo}...\n")
            self._cambiar_estado(f"Ejecutando {nombre_tipo}...")

            # Debug: guardar copia del codigo para inspeccion
            for i, codigo in enumerate(bloques):
                debug_path = os.path.join(tempfile.gettempdir(), f"debug_bloque_{i+1}.py")
                with open(debug_path, 'w', encoding='utf-8') as df:
                    df.write(codigo)
                print(f"[DEBUG] Codigo bloque {i+1} guardado en: {debug_path}")

            # Lanzar TODOS los graficos en paralelo (Popen no bloquea)
            procesos = []
            for ruta in rutas:
                proc = subprocess.Popen(
                    [sys.executable, ruta],
                    stderr=subprocess.PIPE, text=True,
                )
                procesos.append(proc)

            # Esperar a que todos terminen y recolectar errores
            errores_totales = []
            for i, proc in enumerate(procesos):
                _, stderr_output = proc.communicate(timeout=60)
                if proc.returncode != 0:
                    error_msg = (stderr_output or "").strip()
                    lineas_error = error_msg.split('\n')
                    resumen = '\n'.join(lineas_error[-5:]) if len(lineas_error) > 5 else error_msg
                    if not resumen:
                        resumen = f"Codigo de salida: {proc.returncode}"
                    errores_totales.append(f"Bloque {i+1}:\n{resumen}")
                    print(f"[DEBUG] Error en bloque {i+1}:\n{error_msg}")

            if errores_totales:
                self._append_salida(
                    f"\n[Error en {nombre_tipo}]\n" + "\n".join(errores_totales) + "\n"
                )
                self._cambiar_estado(f"Error al ejecutar {nombre_tipo}.")
            else:
                self._cambiar_estado(f"{nombre_tipo} ejecutado(s).")
        except Exception as e:
            self._append_salida(f"[Error al ejecutar {nombre_tipo}] {e}\n")
            self._cambiar_estado(f"Error al ejecutar {nombre_tipo}.")
        finally:
            for ruta in rutas:
                try:
                    if os.path.exists(ruta):
                        os.unlink(ruta)
                except Exception:
                    pass

    def ejecutar_grafico_detectado(self):
        """Ejecuta graficos de tiempo (x(t), v(t), a(t)) y DCL."""
        if not self._last_response:
            messagebox.showinfo("Sin respuesta", "No hay respuesta del agente todavia.")
            return

        todos = self._extraer_bloques_python(self._last_response)

        # Buscar bloques de tiempo y DCL por marcador
        bloques_tiempo = [b for b in todos if b.startswith("# GRAFICO_TIEMPO")]
        bloques_dcl = [b for b in todos if b.startswith("# GRAFICO_DCL")]
        bloques = bloques_tiempo + bloques_dcl

        # Si no hay bloques con marcador, buscar bloques con matplotlib
        # que NO sean de vectores (compatibilidad con respuestas sin marcador)
        if not bloques:
            keywords_vectores = ["quiver", "u_r", "u_theta", "Cartesian", "Polar", "Intrins"]
            bloques = [b for b in todos
                       if "matplotlib" in b.lower()
                       and not b.startswith("# GRAFICO_VECTORES")
                       and not any(kw in b for kw in keywords_vectores)]

        if not bloques:
            total = len(todos)
            msg = "No se detecto un grafico de tiempo o DCL en la respuesta."
            if total == 0:
                msg += "\n\nGemini no genero ningun bloque de codigo Python."
                msg += "\nProba volviendo a enviar la consulta."
            else:
                msg += f"\n\nSe encontraron {total} bloque(s) de codigo, pero ninguno es de tiempo/DCL."
                msg += "\nSi buscas vectores/versores, usa el boton 'Graficar vectores'."
            messagebox.showinfo("Sin grafico", msg)
            return

        confirmar = messagebox.askyesno(
            "Confirmacion",
            f"Se detectaron {len(bloques)} grafico(s) de tiempo/DCL. Deseas ejecutarlos?",
        )
        if confirmar:
            threading.Thread(
                target=self._ejecutar_bloques,
                args=(bloques, "grafico(s) de tiempo/DCL"),
                daemon=True,
            ).start()

    def ejecutar_vectores_detectado(self):
        """Ejecuta el diagrama de vectores y versores en todas las coordenadas."""
        if not self._last_response:
            messagebox.showinfo("Sin respuesta", "No hay respuesta del agente todavia.")
            return

        # Buscar bloques de vectores por marcador
        # Debug
        bloques = self._extraer_codigo_por_tipo(self._last_response, "# GRAFICO_VECTORES")

        # Fallback: buscar bloques que mencionen quiver, versores, etc.
        if not bloques:
            todos = self._extraer_bloques_python(self._last_response)
            # Keywords especificos de diagramas de vectores (no de graficos de tiempo)
            keywords_vectores = ["quiver", "u_r", "u_theta", "u_t", "u_n",
                                 "versor", "Cartesian", "Polar", "Intrins",
                                 "cartesian", "polar", "intrins"]
            bloques = [b for b in todos
                       if "matplotlib" in b.lower()
                       and any(kw in b for kw in keywords_vectores)
                       and not b.startswith("# GRAFICO_TIEMPO")
                       and not b.startswith("# GRAFICO_DCL")]

        if not bloques:
            total = len(self._extraer_bloques_python(self._last_response))
            msg = "No se detecto un diagrama de vectores/versores en la respuesta."
            if total == 0:
                msg += "\n\nGemini no genero ningun bloque de codigo Python."
                msg += "\nProba volviendo a enviar la consulta."
            else:
                msg += f"\n\nSe encontraron {total} bloque(s) de codigo, pero ninguno es de vectores."
                msg += "\nProba con el boton 'Graficar tiempo / DCL'."
            messagebox.showinfo("Sin diagrama de vectores", msg)
            return

        confirmar = messagebox.askyesno(
            "Confirmacion",
            f"Se detecto {len(bloques)} diagrama(s) de vectores/versores. Deseas ejecutarlo?",
        )
        if confirmar:
            threading.Thread(
                target=self._ejecutar_bloques,
                args=(bloques, "diagrama(s) de vectores"),
                daemon=True,
            ).start()

    def _abrir_en_navegador(self):
        if not self._last_response:
            messagebox.showinfo("Sin respuesta", "No hay respuesta para mostrar en el navegador.")
            return
        abrir_en_navegador(self._last_response)

    def graficar_torta(self):
        """Lee feedback_respuestas.csv y muestra un grafico de torta con los porcentajes."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        archivo = os.path.join(base_dir, "feedback_respuestas.csv")

        if not os.path.exists(archivo):
            messagebox.showinfo(
                "Sin datos",
                "Todavia no hay feedback registrado.\n"
                "Vota algunas respuestas y volve a intentar."
            )
            return

        # Contar las valoraciones del CSV
        conteo = {"correcta": 0, "incompleta": 0, "incorrecta": 0}
        try:
            with open(archivo, "r", newline="", encoding="utf-8") as f:
                for fila in csv.DictReader(f):
                    val = (fila.get("valoracion") or "").strip().lower()
                    if val in conteo:
                        conteo[val] += 1
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo leer el CSV:\n{e}")
            return

        total = sum(conteo.values())
        if total == 0:
            messagebox.showinfo("Sin datos", "El archivo de feedback esta vacio.")
            return

        # Armar listas solo con las categorias que tienen al menos 1 voto
        etiquetas_base = {
            "correcta": ("Correctas", "#2d6a4f"),
            "incompleta": ("Incompletas", "#d97706"),
            "incorrecta": ("Incorrectas", "#991b1b"),
        }
        labels, valores, colores = [], [], []
        for clave, (nombre, color) in etiquetas_base.items():
            if conteo[clave] > 0:
                labels.append(f"{nombre} ({conteo[clave]})")
                valores.append(conteo[clave])
                colores.append(color)

        # Generar el codigo del grafico y ejecutarlo en un proceso aparte
        codigo = f"""# GRAFICO_TORTA
import matplotlib.pyplot as plt

labels = {labels}
valores = {valores}
colores = {colores}

fig, ax = plt.subplots(figsize=(7, 7))
ax.pie(valores, labels=labels, colors=colores, autopct='%1.1f%%',
       startangle=90, textprops=dict(color='black', fontsize=12))
ax.set_title('Feedback de respuestas (Total: {total})', fontsize=15, fontweight='bold')
ax.axis('equal')
plt.tight_layout()
plt.show()
"""

        threading.Thread(
            target=self._ejecutar_bloques,
            args=([codigo], "grafico de torta"),
            daemon=True,
        ).start()


if __name__ == "__main__":
    app = AppFisica()
    app.mainloop()