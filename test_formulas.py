"""Test completo: normalizacion + deteccion + renderizado."""
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO
from PIL import Image

def normalizar_formulas(texto):
    texto = re.sub(r'\\\[(.+?)\\\]', r'$$\1$$', texto, flags=re.DOTALL)
    texto = re.sub(
        r'^\s*\[([^\[\]]*\\[a-zA-Z][^\[\]]*)\]\s*$',
        r'$$\1$$',
        texto,
        flags=re.MULTILINE,
    )
    return texto

FORMULA_BLOCK = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)

# Respuesta REAL de Gemini
test = r"""La formula general para el vector de velocidad es:

   [\vec{v} = v_R \, \hat{u}_R + v_\theta \, \hat{u}_\theta]
Donde:

   [v_R = \frac{dR}{dt} = \dot{R}]

   [v_\theta = R \, \frac{d\theta}{dt} = R \, \dot{\theta}]

   [a_R = \frac{d^2R}{dt^2} - R \, \left(\frac{d\theta}{dt}\right)^2 = \ddot{R} - R \, \dot{\theta}^2]

   [a_\theta = 2 \, \frac{dR}{dt} \, \frac{d\theta}{dt} + R \, \frac{d^2\theta}{dt^2} = 2 \, \dot{R} \, \dot{\theta} + R \, \ddot{\theta}]
"""

print("=== 1. Normalizacion ===")
normalizado = normalizar_formulas(test)
# Mostrar que los [...] se convirtieron a $$...$$
count_brackets = test.count(r'\vec')
count_dollars = normalizado.count('$$')
print(f"  Formulas [...] originales que contienen \\vec u otros comandos: {count_brackets}")
print(f"  Delimitadores $$ despues de normalizar: {count_dollars}")

print("\n=== 2. Deteccion ===")
formulas = list(FORMULA_BLOCK.finditer(normalizado))
print(f"  Formulas detectadas: {len(formulas)}")
for i, m in enumerate(formulas):
    print(f"  {i+1}. {m.group(1).strip()[:60]}...")

print("\n=== 3. Renderizado ===")
errores = 0
for i, m in enumerate(formulas):
    latex = m.group(1).strip()
    try:
        fig, ax = plt.subplots(figsize=(0.01, 0.01), dpi=110)
        ax.set_axis_off()
        fig.patch.set_facecolor('#2b2b2b')
        t = ax.text(0.5, 0.5, f'${latex}$', fontsize=16, color='#DCE4EE',
                    ha='center', va='center', transform=ax.transAxes)
        fig.canvas.draw()
        bbox = t.get_window_extent(fig.canvas.get_renderer())
        fig.set_size_inches((bbox.width + 20) / 110, (bbox.height + 10) / 110)
        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=110, facecolor='#2b2b2b',
                    bbox_inches='tight', pad_inches=0.08)
        plt.close(fig)
        buf.seek(0)
        img = Image.open(buf)
        print(f"  {i+1}. OK ({img.size[0]}x{img.size[1]}px)")
    except Exception as e:
        print(f"  {i+1}. ERROR: {e}")
        errores += 1

if errores == 0:
    print("\n=== TODO FUNCIONA! Ejecuta la app. ===")
else:
    print(f"\n=== {errores} formulas fallaron ===")
