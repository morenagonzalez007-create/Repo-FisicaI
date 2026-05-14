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
    # \[...\] con backslash -> $$...$$
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


def abrir_en_navegador(response_text):
    # Convertir \[...\] a $$...$$ para que MathJax los detecte
    safe = re.sub(r'\\\[(.+?)\\\]', r'$$\1$$', response_text, flags=re.DOTALL)
    safe = safe.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    safe = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', safe)
    safe = re.sub(r'^#### (.+)$', r'<h4>\1</h4>', safe, flags=re.MULTILINE)
    safe = re.sub(r'^### (.+)$', r'<h3>\1</h3>', safe, flags=re.MULTILINE)
    safe = re.sub(r'^## (.+)$', r'<h2>\1</h2>', safe, flags=re.MULTILINE)
    safe = re.sub(r'^# (.+)$', r'<h1>\1</h1>', safe, flags=re.MULTILINE)
    safe = re.sub(r'```python\n(.*?)```', r'<pre><code>\1</code></pre>', safe, flags=re.DOTALL)
    safe = re.sub(r'`([^`]+)`', r'<code>\1</code>', safe)
    safe = safe.replace('\n', '<br>\n')

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Respuesta - Asistente Fisica I</title>
<script>
MathJax = {{
  tex: {{ inlineMath: [['$', '$']], displayMath: [['$$', '$$']] }}
}};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<style>
body {{ font-family: 'Segoe UI', Tahoma, sans-serif; max-width: 850px; margin: 40px auto;
       padding: 24px; background: #1a1a2e; color: #e0e0e0; line-height: 1.8; font-size: 16px; }}
h1,h2,h3,h4 {{ color: #4ea8de; margin-top: 1.2em; }}
strong {{ color: #7ec8e3; }}
code {{ background: #16213e; padding: 2px 8px; border-radius: 4px; font-size: 0.95em; }}
pre {{ background: #16213e; padding: 16px; border-radius: 8px; overflow-x: auto; }}
pre br {{ display: none; }}
.MathJax {{ font-size: 115% !important; }}
</style>
</head><body>
{safe}
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
     $$\\vec{F}_{neta} = m \\cdot \\vec{a}$$
     $$E_c = \\frac{1}{2} m v^2$$
     $$\\vec{v}_{F1/D} = v_r \\, \\hat{u}_r + v_\\theta \\, \\hat{u}_\\theta$$
     $$a_r = \\dot{v}_r - R \\, \\omega^2$$
     $$a_\\theta = 2 \\, v_r \\, \\omega + R \\, \\alpha$$
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

        self.boton_grafico = ctk.CTkButton(left, text="Ejecutar grafico detectado", command=self.ejecutar_grafico_detectado)
        self.boton_grafico.grid(row=5, column=0, padx=16, pady=(0, 10), sticky="ew")

        self.boton_navegador = ctk.CTkButton(
            left, text="Ver formulas en navegador",
            command=self._abrir_en_navegador,
            fg_color="#2d6a4f", hover_color="#40916c",
        )
        self.boton_navegador.grid(row=6, column=0, padx=16, pady=(0, 16), sticky="ew")

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
        # Primero normalizar: convertir [latex] y \[latex\] a $$latex$$
        texto = normalizar_formulas(texto)

        # Obtener el widget Text interno de CTkTextbox para image_create
        text_widget = self.texto_salida._textbox

        last_end = 0
        for match in FORMULA_BLOCK.finditer(texto):
            # Insertar texto antes de la formula
            text_before = texto[last_end:match.start()]
            if text_before:
                self.texto_salida.insert("end", text_before)

            # group(1) = display $$...$$, group(2) = inline $...$
            is_display = match.group(1) is not None
            latex = (match.group(1) or match.group(2) or "").strip()

            if latex:
                try:
                    fontsize = 16 if is_display else 12
                    photo = self.formula_renderer.render(latex, fontsize=fontsize)
                    if is_display:
                        self.texto_salida.insert("end", "\n")
                    text_widget.image_create("end", image=photo)
                    if is_display:
                        self.texto_salida.insert("end", "\n")
                except Exception:
                    self.texto_salida.insert("end", f" {latex} ")

            last_end = match.end()

        # Insertar texto restante despues de la ultima formula
        remaining = texto[last_end:]
        if remaining:
            self.texto_salida.insert("end", remaining)
        self.texto_salida.see("end")

    def _cambiar_estado(self, texto):
        self.estado.configure(text=f"Estado: {texto}")

    def _set_botones_habilitados(self, habilitados):
        estado = "normal" if habilitados else "disabled"
        self.boton_enviar.configure(state=estado)
        self.boton_evaluar.configure(state=estado)
        self.boton_cargar.configure(state=estado)
        self.boton_grafico.configure(state=estado)
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

        self._append_salida(f"\n[Tu]\n{pregunta}\n\n")
        self._set_botones_habilitados(False)
        self._cambiar_estado("Analizando consulta...")

        def tarea():
            try:
                respuesta = self.asistente.preguntar(pregunta)
                self._last_response = respuesta
                self._append_salida("[Agente Fisica]\n")
                self._render_respuesta(respuesta)
                self._append_salida("\n")
                self._cambiar_estado("Respuesta lista.")
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
        self._append_salida("\n[Modo Evaluar] Iniciando prueba conceptual...\n")

        def tarea():
            try:
                pregunta, respuesta = self.asistente.evaluar()
                self._last_response = respuesta
                self._append_salida(f"\n[Pregunta de evaluacion]\n{pregunta}\n\n")
                self._append_salida("[Respuesta del agente]\n")
                self._render_respuesta(respuesta)
                self._append_salida("\n")
                self._cambiar_estado("Evaluacion completada.")
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

    def _extraer_codigo_python(self, texto):
        if "```python" not in texto:
            return None

        try:
            partes = texto.split("```python")
            codigo_python = partes[1].split("```")[0].strip()
            return codigo_python
        except Exception:
            return None

    def ejecutar_grafico_detectado(self):
        texto = self.texto_salida.get("1.0", "end")
        codigo_python = self._extraer_codigo_python(texto)

        if not codigo_python:
            messagebox.showinfo("Sin grafico", "No se detecto un bloque de codigo Python en la respuesta.")
            return

        if "matplotlib" not in codigo_python.lower():
            messagebox.showwarning(
                "Atencion",
                "Se encontro codigo Python, pero no parece ser un grafico con matplotlib.",
            )
            return

        confirmar = messagebox.askyesno(
            "Confirmacion",
            "Se detecto codigo Python con matplotlib. Deseas ejecutarlo ahora?",
        )
        if not confirmar:
            return

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".py", delete=False, mode="w", encoding="utf-8"
            ) as archivo_temp:
                archivo_temp.write(codigo_python)
                ruta_temporal = archivo_temp.name

            self._append_salida("\n[Sistema] Ejecutando grafico detectado...\n")
            self._cambiar_estado("Ejecutando grafico...")
            subprocess.run([sys.executable, ruta_temporal], check=False)
            self._cambiar_estado("Grafico ejecutado.")
        except Exception as e:
            self._append_salida(f"[Error al ejecutar grafico] {e}\n")
            self._cambiar_estado("Error al ejecutar grafico.")
        finally:
            try:
                if 'ruta_temporal' in locals() and os.path.exists(ruta_temporal):
                    os.unlink(ruta_temporal)
            except Exception:
                pass

    def _abrir_en_navegador(self):
        if not self._last_response:
            messagebox.showinfo("Sin respuesta", "No hay respuesta para mostrar en el navegador.")
            return
        abrir_en_navegador(self._last_response)


if __name__ == "__main__":
    app = AppFisica()
    app.mainloop()
