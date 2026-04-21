import os
import glob
import subprocess
import tempfile
import google.generativeai as genai
from dotenv import load_dotenv

# ==========================================
# 1. CARGA DE CONFIGURACIÓN
# ==========================================
load_dotenv(override=True)
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("No se encontró la API Key. Crea un archivo .env con GEMINI_API_KEY=tu_clave")

genai.configure(api_key=api_key)

# ==========================================
# 2. INGENIERÍA DE PROMPTS (SYSTEM INSTRUCTION)
# ==========================================
# Aquí está el corazón de los requerimientos de tu proyecto
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
   - Paso 3: Resolución paso a paso explicando el *por qué* de cada operación matemática.
   - Paso 4: Justificación del resultado y detección de posibles errores conceptuales o "trampas" comunes de los estudiantes en la formulación de este tipo de ejercicios.
3. ADAPTACIÓN: Debes adaptar el nivel de tu explicación según el nivel del usuario si este te lo pide. Explica conceptos teóricos sin usar analogías ridículas, mantén un tono científico pero accesible e intuitivo. 
4. GRÁFICOS (Solo en Cinemática): Si el usuario te pide explícitamente "grafica" o "generarme un gráfico de" posición, velocidad o aceleración vs tiempo, DEBES devolver un bloque de código Python ejecutable usando la librería `matplotlib.pyplot`. 
   - El código debe estar rodeado de ```python y ```.
   - DEBE finalizar con `plt.show()` para lanzarse en la PC del estudiante. 
   - NO mandes dibujos ASCII, solo el código python directo.

RESPONDE DE FORMA CLARA Y NO TE SALGAS DE TU ROL.
"""

model = genai.GenerativeModel(
    "gemini-2.5-flash",
    system_instruction=system_instruction
)

print("="*60)
print("     Asistente de Física I - Proyecto Universitario")
print("="*60)

# ==========================================
# 3. CARGA DE BASE DE CONOCIMIENTOS (PDFs)
# ==========================================
pdf_directory = "apuntes_catedra"
uploaded_files = []

if os.path.exists(pdf_directory):
    # Buscamos todos los PDFs dentro de la carpeta
    pdf_files = glob.glob(os.path.join(pdf_directory, "*.pdf"))
    if pdf_files:
        print(f"\nDetectados {len(pdf_files)} documento(s) en la carpeta '{pdf_directory}'...")
        print("Subiendo material a Gemini para usarlo como Entrenamiento Exclusivo:")
        for pdf_path in pdf_files:
            try:
                print(f" -> Procesando {os.path.basename(pdf_path)}...")
                f = genai.upload_file(path=pdf_path)
                uploaded_files.append(f)
            except Exception as e:
                print(f"Error subiendo {pdf_path}: {e}")
        print("¡Base de conocimientos cargada con éxito!")
    else:
        print(f"\nLa carpeta '{pdf_directory}' está vacía. Por favor, pega tus PDFs ahí cuando quieras entrenarlo.")
else:
    print(f"\nNo se encontró la carpeta '{pdf_directory}'. Por favor, créala de forma manual y mete PDFs dentro.")


chat = model.start_chat(history=[])

if uploaded_files:
    # Mensaje inicial cargando todo el historial para inyectar contexto
    prompt_inicial = "A continuación, tienes los recursos base. A partir de ahora, extrae tu sabiduría EXCLUSIVAMENTE de ahí para ayudar a resolver, explicar y analizar problemas de física."
    print("\nAnalizando el material por dentro...")
    respuesta_inicial = chat.send_message(uploaded_files + [prompt_inicial])
    print(f"\n[Agente Física]: Material asimilado. Estoy listo para ayudarte basándome en los archivos provistos.")
else:
    print("\n[Agente Física]: Hola, estoy activo pero sin base de conocimientos estricta. Puedes agregar PDFs a la carpeta más adelante.")

print("\n(Escribe 'salir' para terminar o 'evaluar' para medir el desempeño)")

# ==========================================
# 4. BUCLE PRINCIPAL Y EJECUCIÓN GRÁFICA
# ==========================================
while True:
    pregunta = input("\nTu consulta de Física: ")
    
    if pregunta.lower() in ['salir', 'exit', 'quit', 'terminar']:
        print("¡Éxitos con el proyecto y el estudio!")
        break
    
    # Módulo de Evaluación
    if pregunta.lower() == 'evaluar':
        print("\n--- MODO DE EVALUACIÓN DE DESEMPEÑO ACTIVADO ---")
        pregunta_trampa = "Imagina que lanzo un bloque hacia arriba y cae. Ignorando el roce del aire... si me piden la energía en el punto más alto de vuelo... ¿No sería todo cero porque la velocidad arriba de todo es de 0 m/s y entonces no hace Trabajo?"
        print(f"Enviando consulta trampa para probar al bot: \n'{pregunta_trampa}'")
        print("\nEsperando explicación que detecte el error conceptual sobre Energía Potencial...")
        try:
            respuesta = chat.send_message(pregunta_trampa)
            print(f"\n[Evaluación completada - Respuesta del Agente]:\n{respuesta.text}")
        except Exception as e:
            print("Error en evaluación:", e)
        continue
        
    if pregunta.strip():
        try:
            print("\n[Agente Física]: Analizando tus apuntes y pensando la respuesta... (esto puede tardar unos segundos)")
            respuesta = chat.send_message(pregunta)
            texto_respuesta = respuesta.text
            
            print(f"\n[Agente Física]:\n{texto_respuesta}")
            
            # ===============================================
            # 5. PARSEADOR: EJECUCIÓN GRÁFICA EN VIVO LOCAL
            # ===============================================
            if "```python" in texto_respuesta and "matplotlib" in texto_respuesta.lower():
                print("\n[!] El agente avanzado ha generado el código para mostrar un gráfico de Cinemática.")
                confirmacion = input("¿Deseas dibujar el gráfico ahora mismo abriendo la ventana? (s/n): ")
                
                if confirmacion.lower() == 's':
                    partes = texto_respuesta.split("```python")
                    codigo_python = partes[1].split("```")[0].strip()
                    
                    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8") as f:
                        f.write(codigo_python)
                        ruta_archivo_temporal = f.name
                    
                    try:
                        print("Calculando imagen matemática...")
                        import sys
                        subprocess.run([sys.executable, ruta_archivo_temporal])
                    except Exception as e:
                        print(f"Error al intentar dibujar el cuadro: {e}")
                    finally:
                        # Limpiamos el archivo temporal para no ensuciar la PC
                        os.unlink(ruta_archivo_temporal)
                        
            print("-" * 75)
        except Exception as e:
            print(f"\n[Error de Comunicación con el Agente]: {e}")
