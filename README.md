# Simulador de impacto por cierre de oficinas — MVP 1.0

Aplicación local en Python + Streamlit que simula el reparto de tickets de una oficina cerrada a oficinas cercanas, y su efecto en horas útiles diarias y estructura necesaria.

## Instalación en Windows

1. Instala Python 3.10 o posterior (marca **Add python.exe to PATH** al instalarlo).
2. Descomprime el ZIP en una carpeta de tu equipo, manteniendo los archivos juntos.
3. Abre PowerShell o CMD dentro de esa carpeta.
4. Instala dependencias una sola vez:

   ```bash
   py -m pip install -r requirements.txt
   ```

5. Ejecuta la aplicación:

   ```bash
   py -m streamlit run app.py
   ```

   También puedes hacer doble clic en `iniciar_windows.bat` después de instalar las dependencias.

6. Abre `http://localhost:8501` si el navegador no se abre automáticamente.
7. Detén el servidor local con `Ctrl+C` en la terminal.

En macOS / Linux: utiliza `python3 -m pip install -r requirements.txt` y `python3 -m streamlit run app.py`.

## Archivos

- `app.py`: interfaz web interactiva.
- `motor.py`: lectura y limpieza de CSV; distancias, distribución, cargas y dictámenes; Excel.
- `BASE CIERRE OFICINAS - BASE TOTAL_v2.csv`: base proporcionada, incluida para ejecutar sin carga manual.
- `requirements.txt`: librerías necesarias.
- `iniciar_windows.bat`: inicio rápido Windows.
- `tests/test_motor.py`: pruebas reproducibles de negocio.

## Decisiones implementadas

- Radio inicial **3 km** y máximo **5 receptoras**, ambos editables.
- Selección de receptoras por distancia de **Haversine** en línea recta; es posible desmarcarlas manualmente.
- Redistribución de tickets por peso `1/distancia`, por separado para **ventanilla** y **plataforma**. Si dos receptoras están exactamente en la ubicación de la oficina cerrada, solo esas participan en el reparto y reciben proporciones iguales.
- **100%** de tickets inicialmente redistribuidos por canal; ambos porcentajes editables (0–100%). El volumen a redistribuir se redondea al entero más cercano (mitad hacia arriba).
- Los tickets se reparten en enteros por **mayores restos**, conservando la cantidad total, con desempate por menor distancia y código.
- Ventanilla: `TKT VNT`, `EST AU`, `TIEMPO ATENCION VNT`.
- Plataforma: `TKT PLT`, `EST EBP`, `TIEMPO ATENCION PLT`.
- Cálculo: `horas útiles = tickets mensuales * tiempo promedio (min) / (60 * 24 * estructura)`.
- Umbral inicial **7 horas útiles diarias por persona**, editable.
- Estructura requerida por canal = techo(`tickets * tiempo / (60 * 24 * umbral)`).
- Déficit previo = `max(0, estructura requerida actual - estructura actual)`.
- Déficit total = `max(0, estructura requerida proyectada - estructura actual)`.
- Adicional por cierre = `déficit total - déficit previo`.
- No se redistribuye personal de la oficina cerrada.
- Si algún dato esencial es insuficiente (`#N/A`, vacío, estructura cero), **no se emite dictamen de ese canal** y se muestra alerta; el resultado global se indica como parcial/no concluyente.
- El mapa de coordenadas funciona **sin Internet**. El usuario puede activar el mapa de calles opcional (descarga teselas públicas de OpenStreetMap).
- Exportación a Excel con siete hojas, incluidas alertas, criterios y parámetros.

## Notas importantes

- Los tickets de los canales son **tickets de atención**, no la suma de operaciones monetarias y no monetarias; no se suman para evitar duplicación.
- Si la oficina cerrada carece de tickets de un canal, ese canal no se redistribuye. El resultado puede ser parcial.
- Si no hay oficinas dentro del radio, se indica expresamente; no se inventan receptoras.
- No se modelan tiempos de desplazamiento reales, preferencias de clientes, disponibilidad física, cambios de TMA tras el cierre ni migración digital distinta de los porcentajes configurados.
- Se recomienda mantener los datos en un equipo o infraestructura autorizada, sin publicar el dashboard y su CSV en Internet.

## Pruebas

Desde la carpeta del proyecto, si tienes `pytest` instalado:

```bash
py -m pip install pytest
py -m pytest -q
```

## Cambiar o actualizar la base

Desde la barra lateral, selecciona **Reemplazar CSV**. Se exige que los nombres de columnas coincidan con la base v2. El CSV debe estar codificado como UTF-8, separado por comas, con decimales que pueden estar expresados con coma entre comillas.
