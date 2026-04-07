import os
import google.generativeai as genai
from google.genai import types
from dotenv import load_dotenv

# Cargar la API key desde el archivo .env
# 1. Cargar el archivo .env
load_dotenv()

# 2. Obtener la llave
# Buscamos el nombre de la variable "GEMINI_API_KEY" que debe estar en tu .env
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("No se encontró la API Key. Crea un archivo .env con GEMINI_API_KEY=tu_clave")
    # Si ves este error, es porque el archivo .env no tiene la línea GEMINI_API_KEY=tu_llave
    raise ValueError("No se encontró la API Key. Revisa tu archivo .env")

genai.configure(api_key=api_key)
# 3. Configurar e inicializar

# Inicializamos el modelo indicándole que es un tutor de física
model = genai.GenerativeModel(
    "gemini-1.5-flash",
    system_instruction="Eres un profesor y tutor experto en Física I a nivel universitario. Responde de forma clara, con rigor científico y de manera didáctica."
)
print("--- Tutor de Física I Activo (Escribe 'salir' para terminar) ---\n")

print("¡Hola! Soy tu asistente de Física I impulsado por Gemini. (Escribe 'salir' para terminar)\n")

while True:
    pregunta = input("\nPregunta de Física I: ")
    pregunta = input("\nTu duda de Física: ")
    
    if pregunta.lower() in ['salir', 'exit', 'quit']:
        print("¡Éxitos con el estudio!")
        print("¡Suerte con el estudio!")
        break
        
    if pregunta.strip():
        try:
            respuesta = model.generate_content(pregunta)
            print(f"\n[Profesor IA]: {respuesta.text}")
            print("-" * 50)
        except Exception as e:
            print(f"Error al conectar con Gemini: {e}")
#probando push..!!

print("prueba intento subir commit")
