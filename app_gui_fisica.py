import os
import glob
import subprocess
import tempfile
import threading
import sys

import customtkinter as ctk
import google.generativeai as genai
from dotenv import load_dotenv
from tkinter import filedialog, messagebox


# ==========================================================
# CONFIGURACIÓN VISUAL
# ==========================================================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ==========================================================
# LÓGICA DEL ASISTENTE DE FÍSICA
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
                "No se encontró la API Key. Crea un archivo .env con GEMINI_API_KEY=tu_clave"
            )

        genai.configure(api_key=api_key)

        system_instruction = """
Eres un Asistente y Tutor Avanzado de Física I a nivel universitario.
Tu objetivo es resolver, explicar y analizar problemas de física enfocados en las siguientes áreas EXCLUSIVAMENTE:
- Cinemática
- Dinámica
- Trabajo y Energía
- Cantidad de movimiento o momento lineal
- Momento Angular

REGLAS ESTRICTAS DE COMPORTAMIENTO:
1. USO DE RECURSOS: Inicialmente, tu conocimiento DEBE limitarse a los documentos, libros, apuntes y ejercicios resueltos proporcionados por el usuario. No debes inventar datos ni usar búsquedas web externas por ahora. Aplica el conocimiento de esos textos para resolver las dudas.
2. ESTRUCTURA DE RESPUESTA PARA EJERCICIOS NUMÉRICOS:
   - Paso 1: Análisis Teórico y Detección de Datos. Explica claramente la situación física y los datos disponibles.
   - Paso 2: Leyes y Fórmulas. Menciona (si aplica) las fuerzas actuantes y las leyes o teoremas de conservación (energía, momento lineal/angular) que aplican al caso.
   - Paso 3: Resolución paso a paso explicando el por qué de cada operación matemática.
   - Paso 4: Justificación del resultado y detección de posibles errores conceptuales o trampas comunes de los estudiantes en la formulación de este tipo de ejercicios.
3. ADAPTACIÓN: Debes adaptar el nivel de tu explicación según el nivel del usuario si este te lo pide. Explica conceptos teóricos con tono científico pero accesible e intuitivo.
4. GRÁFICOS (Solo en Cinemática): Si el usuario te pide explícitamente grafica o generarme un gráfico de posición, velocidad o aceleración vs tiempo, DEBES devolver un bloque de código Python ejecutable usando la librería matplotlib.pyplot.
   - El código debe estar rodeado de ```python y ```.
   - DEBE finalizar con plt.show() para lanzarse en la PC del estudiante.
   - NO mandes dibujos ASCII, solo el código python directo.

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
            return False, f"No se encontró la carpeta '{pdf_directory}'."

        pdf_files = glob.glob(os.path.join(pdf_directory, "*.pdf"))
        if not pdf_files:
            return False, f"La carpeta '{pdf_directory}' está vacía."

        errores = []

        for pdf_path in pdf_files:
            try:
                archivo = genai.upload_file(path=pdf_path)
                self.uploaded_files.append(archivo)
            except Exception as e:
                errores.append(f"{os.path.basename(pdf_path)}: {e}")

        if not self.uploaded_files:
            return False, "No se pudo subir ningún PDF.\n" + "\n".join(errores)

        try:
            prompt_inicial = (
                "A continuación, tienes los recursos base. "
                "A partir de ahora, extrae tu sabiduría EXCLUSIVAMENTE de ahí "
                "para ayudar a resolver, explicar y analizar problemas de física."
            )
            self.chat.send_message(self.uploaded_files + [prompt_inicial])
        except Exception as e:
            return False, f"Se subieron archivos, pero falló el análisis inicial: {e}"

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
            "si me piden la energía en el punto más alto de vuelo... "
            "¿No sería todo cero porque la velocidad arriba de todo es de 0 m/s y entonces no hace Trabajo?"
        )
        respuesta = self.chat.send_message(pregunta_trampa)
        return pregunta_trampa, respuesta.text


# ==========================================================
# INTERFAZ GRÁFICA
# ==========================================================
class AppFisica(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Asistente de Física I")
        self.geometry("1200x760")
        self.minsize(1000, 650)

        self.asistente = None
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
            text="Asistente de Física I",
            font=ctk.CTkFont(size=28, weight="bold"),
        )
        titulo.grid(row=0, column=0, padx=20, pady=(16, 4), sticky="w")

        subtitulo = ctk.CTkLabel(
            header,
            text="Consultas, evaluación y carga de apuntes PDF",
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
            text="Escribí tu consulta",
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

        self.boton_grafico = ctk.CTkButton(left, text="Ejecutar gráfico detectado", command=self.ejecutar_grafico_detectado)
        self.boton_grafico.grid(row=5, column=0, padx=16, pady=(0, 16), sticky="ew")

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

        # Enter para enviar desde el cuadro de texto
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

    def _cambiar_estado(self, texto):
        self.estado.configure(text=f"Estado: {texto}")

    def _set_botones_habilitados(self, habilitados):
        estado = "normal" if habilitados else "disabled"
        self.boton_enviar.configure(state=estado)
        self.boton_evaluar.configure(state=estado)
        self.boton_cargar.configure(state=estado)
        self.boton_grafico.configure(state=estado)
        self.boton_limpiar.configure(state=estado)

    def limpiar_entrada(self):
        self.texto_entrada.delete("1.0", "end")

    def enviar_consulta(self):
        pregunta = self.texto_entrada.get("1.0", "end").strip()
        if not pregunta:
            messagebox.showwarning("Atención", "Escribí una consulta antes de enviar.")
            return

        if self.asistente is None:
            messagebox.showwarning("Atención", "El asistente todavía se está inicializando.")
            return

        self._append_salida(f"\n[Tú]\n{pregunta}\n\n")
        self._set_botones_habilitados(False)
        self._cambiar_estado("Analizando consulta...")

        def tarea():
            try:
                respuesta = self.asistente.preguntar(pregunta)
                self._append_salida(f"[Agente Física]\n{respuesta}\n")
                self._cambiar_estado("Respuesta lista.")
            except Exception as e:
                self._append_salida(f"[Error] {e}\n")
                self._cambiar_estado("Error al consultar.")
            finally:
                self._set_botones_habilitados(True)

        threading.Thread(target=tarea, daemon=True).start()

    def modo_evaluar(self):
        if self.asistente is None:
            messagebox.showwarning("Atención", "El asistente todavía se está inicializando.")
            return

        self._set_botones_habilitados(False)
        self._cambiar_estado("Ejecutando evaluación...")
        self._append_salida("\n[Modo Evaluar] Iniciando prueba conceptual...\n")

        def tarea():
            try:
                pregunta, respuesta = self.asistente.evaluar()
                self._append_salida(f"\n[Pregunta de evaluación]\n{pregunta}\n\n")
                self._append_salida(f"[Respuesta del agente]\n{respuesta}\n")
                self._cambiar_estado("Evaluación completada.")
            except Exception as e:
                self._append_salida(f"[Error en evaluación] {e}\n")
                self._cambiar_estado("Error en evaluación.")
            finally:
                self._set_botones_habilitados(True)

        threading.Thread(target=tarea, daemon=True).start()

    def cargar_pdfs(self):
        if self.asistente is None:
            messagebox.showwarning("Atención", "El asistente todavía se está inicializando.")
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
            messagebox.showinfo("Sin gráfico", "No se detectó un bloque de código Python en la respuesta.")
            return

        if "matplotlib" not in codigo_python.lower():
            messagebox.showwarning(
                "Atención",
                "Se encontró código Python, pero no parece ser un gráfico con matplotlib.",
            )
            return

        confirmar = messagebox.askyesno(
            "Confirmación",
            "Se detectó código Python con matplotlib. ¿Deseas ejecutarlo ahora?",
        )
        if not confirmar:
            return

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".py", delete=False, mode="w", encoding="utf-8"
            ) as archivo_temp:
                archivo_temp.write(codigo_python)
                ruta_temporal = archivo_temp.name

            self._append_salida("\n[Sistema] Ejecutando gráfico detectado...\n")
            self._cambiar_estado("Ejecutando gráfico...")
            subprocess.run([sys.executable, ruta_temporal], check=False)
            self._cambiar_estado("Gráfico ejecutado.")
        except Exception as e:
            self._append_salida(f"[Error al ejecutar gráfico] {e}\n")
            self._cambiar_estado("Error al ejecutar gráfico.")
        finally:
            try:
                if 'ruta_temporal' in locals() and os.path.exists(ruta_temporal):
                    os.unlink(ruta_temporal)
            except Exception:
                pass


if __name__ == "__main__":
    app = AppFisica()
    app.mainloop()