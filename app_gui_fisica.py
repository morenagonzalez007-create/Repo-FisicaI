import os
import glob
import re
import subprocess
import tempfile
import threading
import sys
import webbrowser
from io import BytesIO

import customtkinter as ctk
import google.generativeai as genai
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
        # NO tocar raw strings (r'...' o r"...") - matplotlib las maneja bien
        if re.search(r"""\br(['"])""", linea):
            resultado.append(linea)
            continue

        # NO tocar lineas de import, comentarios, o lineas sin strings
        if linea.strip().startswith(('#', 'import', 'from')) or ('"' not in linea and "'" not in linea):
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
    # (quiver da problemas de escala frecuentemente)
    # No hacemos conversion automatica porque es complejo, pero si advertimos
    if 'plt.quiver' in codigo or '.quiver(' in codigo:
        print("[WARN] El codigo usa plt.quiver que puede dar problemas de escala.")

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

    # 2. Convertir "falso display" a inline
    #    Gemini usa $$formula$$ (display/centrado) para formulas que deberian ser
    #    inline $formula$ cuando estan dentro de una oracion. Detectamos esto
    #    mirando las lineas anterior y siguiente: si hay texto normal alrededor,
    #    la formula deberia ser inline.
    lineas_pre = texto.split('\n')
    for i, linea in enumerate(lineas_pre):
        stripped = linea.strip()
        # Solo procesar lineas que son UNICAMENTE una formula $$...$$
        if not re.match(r'^\$\$[^$]+\$\$$', stripped):
            continue

        # Buscar linea anterior no vacia
        prev = ""
        for j in range(i - 1, -1, -1):
            if lineas_pre[j].strip():
                prev = lineas_pre[j].strip()
                break

        # Buscar linea siguiente no vacia
        next_l = ""
        for j in range(i + 1, len(lineas_pre)):
            if lineas_pre[j].strip():
                next_l = lineas_pre[j].strip()
                break

        # Es "falso display" si:
        # - La linea anterior es texto normal (no termina en : ni es heading/vacia)
        # - O la linea siguiente empieza con minuscula, coma, punto, parentesis, etc.
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
            # Convertir $$...$$ a $...$ (inline)
            inner = stripped[2:-2]
            lineas_pre[i] = f'${inner}$'

    texto = '\n'.join(lineas_pre)

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
            # Mantener en su propia linea
            colapsado.append(linea)
        elif colapsado and not es_linea_especial(colapsado[-1]):
            # Unir con la linea anterior (agregar espacio)
            colapsado[-1] = colapsado[-1].rstrip() + ' ' + linea.strip()
        else:
            colapsado.append(linea)
    texto = '\n'.join(colapsado)

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
            html_bloques.append(f'<h{nivel}>{h_match.group(2)}</h{nivel}>')
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
h1,h2,h3,h4 {{ color: #4ea8de; margin-top: 1.2em; }}
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
        webbrowser.open(f.name)


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

        genai.configure(api_key=api_key)

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
2. ESTRUCTURA DE RESPUESTA PARA EJERCICIOS NUMERICOS:
   - Paso 1: Analisis Teorico y Deteccion de Datos. Explica claramente la situacion fisica y los datos disponibles.
   - Paso 2: Leyes y Formulas. Menciona (si aplica) las fuerzas actuantes y las leyes o teoremas de conservacion (energia, momento lineal/angular) que aplican al caso.
   - Paso 3: Resolucion paso a paso explicando el por que de cada operacion matematica.
   - Paso 4: Justificacion del resultado y deteccion de posibles errores conceptuales o trampas comunes de los estudiantes en la formulacion de este tipo de ejercicios.
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
5. FORMATO DE FORMULAS MATEMATICAS (MUY IMPORTANTE - CUMPLIR SIEMPRE):
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
      Usa fig, ax = plt.subplots(figsize=(10, 8)) con ax.set_aspect('equal').

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
      - Dibujar el punto de suspension (pivote) como un punto gris arriba.
      - Dibujar la cuerda como una linea negra desde el pivote hasta la masa.
      - La masa esta en el extremo inferior de la cuerda.
      - El peso va RECTO HACIA ABAJO desde la masa (no a lo largo de la cuerda).
      - La tension va DESDE LA MASA HACIA EL PIVOTE (a lo largo de la cuerda).

      PARA PLANOS INCLINADOS ESPECIFICAMENTE:
      - Dibujar la superficie inclinada como una linea GRIS GRUESA (lw=3).
      - La masa se dibuja SOBRE la superficie, NO flotando en el aire.
      - Normal: PERPENDICULAR a la superficie inclinada, apuntando HACIA AFUERA de la superficie.
        Si el plano tiene angulo alpha con la horizontal, la Normal forma angulo alpha con la vertical.
      - Friccion (si existe): TANGENTE a la superficie, OPUESTA al movimiento o tendencia de movimiento.
      - Peso: SIEMPRE VERTICAL HACIA ABAJO, sin importar la inclinacion del plano.
      - Si hay cuerda: dibujar la cuerda y la tension va A LO LARGO de ella hacia el punto de sujecion.
      - NO mezclar estas reglas con las de pendulos ni conos.

      PARA SUPERFICIES CONICAS ESPECIFICAMENTE:
      - Dibujar el PERFIL del cono (las dos paredes inclinadas) como lineas GRISES gruesas (lw=3)
        formando una V invertida o V segun la orientacion del cono.
      - La masa se dibuja SOBRE la pared del cono, NO en el centro ni en el vertice.
      - Normal: PERPENDICULAR a la PARED CONICA, apuntando hacia el INTERIOR del cono (hacia el eje).
        En un cono con semiangulo alpha, la Normal forma angulo alpha con la horizontal.
      - Tension (si hay cuerda que pasa por el vertice): va DESDE LA MASA HACIA EL VERTICE del cono,
        A LO LARGO de la cuerda. NO apunta hacia arriba ni al aire.
      - Peso: SIEMPRE VERTICAL HACIA ABAJO desde la masa.
      - Si hay una pesa colgando del otro extremo de la cuerda, hacer DOS subplots separados:
        uno para el objeto sobre el cono y otro para la pesa colgante (con su propio DCL).
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

      ESCALA DE FLECHAS - MUY IMPORTANTE:
      Los vectores v⃗ y a⃗ suelen ser muy chicos comparados con la posicion.
      SIEMPRE escala las flechas para que midan entre 4 y 6 unidades en el grafico:
        escala_v = 5.0 / abs(v_magnitud)
        escala_a = 5.0 / abs(a_magnitud)
      Los versores deben medir 3 unidades (escala fija = 3).
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

        self.model = genai.GenerativeModel(
            "gemini-2.5-flash",
            system_instruction=system_instruction,
        )

        self.chat = self.model.start_chat(history=[])

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
                archivo = genai.upload_file(path=pdf_path)
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

    def preguntar(self, pregunta):
        respuesta = self.chat.send_message(pregunta)
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

        self.boton_cargar = ctk.CTkButton(left, text="Cargar PDFs de apuntes_catedra", command=self.cargar_pdfs)
        self.boton_cargar.grid(row=4, column=0, padx=16, pady=(0, 10), sticky="ew")

        self.boton_grafico = ctk.CTkButton(
            left, text="Graficar tiempo / DCL",
            command=self.ejecutar_grafico_detectado,
            fg_color="#1a5276", hover_color="#2980b9",
        )
        self.boton_grafico.grid(row=5, column=0, padx=16, pady=(0, 10), sticky="ew")

        self.boton_vectores = ctk.CTkButton(
            left, text="Graficar vectores / versores",
            command=self.ejecutar_vectores_detectado,
            fg_color="#6c3483", hover_color="#8e44ad",
        )
        self.boton_vectores.grid(row=6, column=0, padx=16, pady=(0, 10), sticky="ew")

        self.boton_navegador = ctk.CTkButton(
            left, text="Ver formulas en navegador",
            command=self._abrir_en_navegador,
            fg_color="#2d6a4f", hover_color="#40916c",
        )
        self.boton_navegador.grid(row=7, column=0, padx=16, pady=(0, 16), sticky="ew")

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

        self.estado = ctk.CTkLabel(
            right,
            text="Estado: iniciando...",
            anchor="w",
            text_color="gray70",
        )
        self.estado.grid(row=2, column=0, padx=16, pady=(0, 16), sticky="ew")

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

    def _set_botones_habilitados(self, habilitados):
        estado = "normal" if habilitados else "disabled"
        self.boton_enviar.configure(state=estado)
        self.boton_evaluar.configure(state=estado)
        self.boton_cargar.configure(state=estado)
        self.boton_grafico.configure(state=estado)
        self.boton_vectores.configure(state=estado)
        self.boton_limpiar.configure(state=estado)
        self.boton_navegador.configure(state=estado)

    def limpiar_entrada(self):
        self.texto_entrada.delete("1.0", "end")

    def enviar_consulta(self):
        pregunta = self.texto_entrada.get("1.0", "end").strip()
        if not pregunta:
            messagebox.showwarning("Atencion", "Escribi una consulta antes de enviar.")
            return

        if self.asistente is None:
            messagebox.showwarning("Atencion", "El asistente todavia se esta inicializando.")
            return

        self._append_salida(f"\n{'='*50}\n[Tu]\n{pregunta}\n\n")
        self._set_botones_habilitados(False)
        self._cambiar_estado("Analizando consulta...")

        def tarea():
            try:
                respuesta = self.asistente.preguntar(pregunta)
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
        self._cambiar_estado("Ejecutando evaluacion...")
        self._append_salida(f"\n{'='*50}\n[Modo Evaluar] Iniciando prueba conceptual...\n")

        def tarea():
            try:
                pregunta, respuesta = self.asistente.evaluar()
                self._last_response = respuesta
                self._append_salida(f"\n[Pregunta de evaluacion]\n{pregunta}\n\n")
                self._append_salida("[Agente Fisica] Evaluacion completada.\n")
                self._append_salida("La respuesta completa se abrio en el navegador.\n")

                # Abrir HTML automaticamente
                abrir_en_navegador(respuesta)

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
        """Extrae TODOS los bloques ```python ... ``` de la respuesta (case-insensitive)."""
        bloques = []
        # Buscar con regex para capturar variantes: ```python, ```Python, ``` python, etc.
        patron = re.compile(r'```[Pp]ython\s*\n(.*?)```', re.DOTALL)
        for match in patron.finditer(texto):
            codigo = match.group(1).strip()
            if codigo:
                bloques.append(codigo)
        # Debug: imprimir cuantos bloques se detectaron
        if bloques:
            print(f"[DEBUG] Se detectaron {len(bloques)} bloque(s) de codigo Python en la respuesta.")
            for i, b in enumerate(bloques):
                primera_linea = b.split('\n')[0][:80]
                print(f"  Bloque {i+1}: {primera_linea}")
        else:
            print("[DEBUG] NO se detectaron bloques ```python en la respuesta.")
            # Mostrar si hay algun indicio de codigo
            if "import matplotlib" in texto or "plt." in texto:
                print("[DEBUG] PERO se encontro 'matplotlib' o 'plt.' en el texto sin bloque de codigo.")
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
                proc.wait()
                if proc.returncode != 0:
                    error_msg = (proc.stderr.read() if proc.stderr else "").strip()
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
            self._ejecutar_bloques(bloques, "grafico(s) de tiempo/DCL")

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
            self._ejecutar_bloques(bloques, "diagrama(s) de vectores")

    def _abrir_en_navegador(self):
        if not self._last_response:
            messagebox.showinfo("Sin respuesta", "No hay respuesta para mostrar en el navegador.")
            return
        abrir_en_navegador(self._last_response)


if __name__ == "__main__":
    app = AppFisica()
    app.mainloop()
