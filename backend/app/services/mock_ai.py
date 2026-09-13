import asyncio
import io
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from dotenv import find_dotenv, load_dotenv
import edge_tts
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont
import random

load_dotenv(find_dotenv())

VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3.6-flash")
gemini_api_key = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None

POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY")

if not POLLINATIONS_API_KEY:
    # IMPORTANTE: según la documentación oficial de Pollinations, "nologo" solo
    # funciona con una cuenta registrada (token generado en https://auth.pollinations.ai).
    # Sin token, nologo=true no tiene ningún efecto y el logo seguirá apareciendo
    # SIEMPRE, por diseño de su API — no es un bug de este código.
    print(
        "[AVISO] POLLINATIONS_API_KEY no está configurada: el logo de Pollinations "
        "aparecerá en todas las imágenes (nologo=true requiere una cuenta registrada "
        "en https://auth.pollinations.ai). Además, sin cuenta, el límite de peticiones "
        "es de 1 cada 15 segundos, lo que puede causar fallos en storyboards largos."
    )

ASSETS_DIR = Path("renders/assets")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
RENDERS_DIR = Path("renders")
RENDERS_DIR.mkdir(parents=True, exist_ok=True)

# --- Limitador de ritmo para Pollinations ---
# Anónimo: 1 petición / 15s. Con cuenta registrada (nivel "Seed" o superior): 1 / 5s.
# Antes el código solo esperaba 2s fijos entre imágenes, muy por debajo del límite
# anónimo real, lo que provocaba 429 en cadena en storyboards largos (y de ahí las
# tarjetas azules de repuesto "por causa desconocida").
_POLLINATIONS_MIN_INTERVAL = 6.0 if POLLINATIONS_API_KEY else 16.0
_pollinations_lock = threading.Lock()
_pollinations_last_call = 0.0


def _esperar_turno_pollinations():
    """Bloquea lo necesario para no superar el límite de peticiones de Pollinations."""
    global _pollinations_last_call
    with _pollinations_lock:
        ahora = time.monotonic()
        espera = _POLLINATIONS_MIN_INTERVAL - (ahora - _pollinations_last_call)
        if espera > 0:
            time.sleep(espera)
        _pollinations_last_call = time.monotonic()


DEFAULT_VOICES = [
    {"provider": "azure", "provider_voice_id": "es-ES-AlvaroNeural", "name": "Álvaro", "gender": "male", "language": "es", "tone": "formal"},
    {"provider": "azure", "provider_voice_id": "es-ES-ElviraNeural", "name": "Elvira", "gender": "female", "language": "es", "tone": "formal"},
    {"provider": "elevenlabs", "provider_voice_id": "es-antonio", "name": "Antonio", "gender": "male", "language": "es", "tone": "narrativo"},
    {"provider": "elevenlabs", "provider_voice_id": "es-lucia", "name": "Lucía", "gender": "female", "language": "es", "tone": "coloquial"},
]


def _call_gemini_with_retry(prompt: str, max_retries: int = 5, json_mode: bool = True):
    if not gemini_client:
        raise ValueError("GEMINI_API_KEY no está configurada en el archivo .env")

    delay = 3.0
    config = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None

    for attempt in range(max_retries):
        try:
            return gemini_client.models.generate_content(
                model=VISION_MODEL,
                contents=prompt,
                config=config,
            )
        except Exception as e:
            err_msg = str(e)
            is_transient = any(code in err_msg for code in ["503", "429", "UNAVAILABLE", "high demand", "ResourceExhausted"])
            if is_transient and attempt < max_retries - 1:
                match = re.search(r"retry in ([\d\.]+)s", err_msg, re.IGNORECASE)
                if match:
                    wait_time = float(match.group(1)) + 2.0
                    print(f"[Gemini 429] Cuota alcanzada. Esperando {wait_time:.1f}s según la indicación de la API...")
                else:
                    wait_time = delay
                    delay *= 2
                    print(f"[Gemini Espera] Reintento {attempt + 1}/{max_retries}. Esperando {wait_time}s...")
                time.sleep(wait_time)
                continue
            raise e


def _parse_json_safely(text: str) -> dict:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean)
    return json.loads(clean.strip())


def analyze_reference(reference_url: str) -> dict:
    if not gemini_client:
        return {
            "summary": "Referencia procesada con valores por defecto.",
            "avg_shot_seconds": 4.0,
            "total_shots": 40,
            "structure": [{"block": "desarrollo", "seconds": 60}],
            "source_url": reference_url,
        }

    prompt = f"""
    Analiza el estilo y ritmo de vídeo para una referencia en: {reference_url}
    Extrae la estructura típica de edición, el ritmo en segundos por plano y un resumen del estilo.
    Devuelve un JSON con este esquema exacto:
    {{
      "summary": "Breve resumen del ritmo y montaje...",
      "avg_shot_seconds": 4.5,
      "total_shots": 60,
      "structure": [
        {{"block": "gancho", "seconds": 8}},
        {{"block": "desarrollo", "seconds": 120}},
        {{"block": "cierre", "seconds": 15}}
      ],
      "source_url": "{reference_url}"
    }}
    """
    response = _call_gemini_with_retry(prompt)
    return _parse_json_safely(response.text)


def generate_script(title: str, description: str, target_duration_seconds: int, reference_analysis: dict | None) -> list[dict]:
    style_guide = ""
    if reference_analysis:
        style_guide = f"Toma como referencia un ritmo de corte promedio de {reference_analysis.get('avg_shot_seconds', 4)}s."

    prompt = f"""
    Eres un guionista profesional para vídeos dinámicos.
    Tema: {title}
    Detalles: {description}
    Duración objetivo total: {target_duration_seconds} segundos.
    {style_guide}

    Divide el guion en bloques cronológicos para la voz en off.
    Responde ÚNICAMENTE con un JSON válido con este formato:
    {{
      "blocks": [
        {{"id": "b1", "order": 1, "text": "Locución del bloque...", "estimated_seconds": 15.0}}
      ]
    }}
    Asegúrate de que la suma de 'estimated_seconds' se aproxime a {target_duration_seconds} segundos.
    """

    response = _call_gemini_with_retry(prompt)
    data = _parse_json_safely(response.text)
    return data.get("blocks", [])


def analizar_estilo_proyecto(tema: str, sinopsis: str) -> dict:
    fallback = {
        "visual_style": "Cinematic documentary style, realistic lighting, high detail, 35mm photograph",
        "entities": {},
    }
    if not gemini_client:
        return fallback

    prompt = f"""
    Analiza este proyecto de vídeo y devuelve un JSON con:
    1. "visual_style": Descripción en inglés del estilo artístico general que mejor encaja con el tema (ej: "2D anime screencap, MAPPA style", "Cinematic historical documentary, 35mm film photograph", "Hyperrealistic cyberpunk 3D render", "Found-footage horror, grainy VHS aesthetic", etc.). Debe encajar con el tema real del vídeo, nunca un estilo genérico por defecto.
    2. "entities": Diccionario de personajes, conceptos o lugares clave que se repiten en el vídeo, con su descripción física visual detallada en inglés, para que un generador de imágenes los dibuje siempre igual. Si no hay personajes recurrentes claros, devuelve un diccionario vacío.

    Tema: {tema}
    Sinopsis: {sinopsis}

    Responde ÚNICAMENTE con el JSON, sin explicaciones ni Markdown.
    """
    try:
        response = _call_gemini_with_retry(prompt)
        data = _parse_json_safely(response.text)
        if not data.get("visual_style"):
            return fallback
        data.setdefault("entities", {})
        return data
    except Exception:
        return fallback


def generate_storyboard(
    script: list[dict],
    estilo_global: str = "Cinematic documentary style, realistic lighting, high detail, 35mm photograph",
    entidades: dict | None = None,
) -> list[dict]:
    entidades = entidades or {}
    prompt = f"""
    A partir de este guion de locución, genera el desglose de planos (storyboard visual) y optimiza el prompt de imagen ('prompt_en') para cada plano.

    Estilo visual del proyecto: {estilo_global}
    Guía de entidades y personajes: {json.dumps(entidades, ensure_ascii=False)}

    Guion:
    {json.dumps(script, ensure_ascii=False)}

    REGLAS ESTRICTAS DE COMPOSICIÓN Y ENCUADRE PARA 'prompt_en':
    1. Rostros y personas (Evitar caras derretidas):
       - Queda PROHIBIDO generar planos generales lejanos con multitudes, grupos grandes o mesas largas donde los rostros de las personas ocupen pocos píxeles.
       - En cualquier escena donde aparezcan personas, PRIORIZA SIEMPRE planos medios ('medium shot'), primeros planos ('close-up') o retratos cinematográficos ('cinematic portrait').
       - Si la escena requiere varias personas, enfoca nítidamente a UN solo personaje principal en primer término y desenfoca al resto con profundidad de campo reducida ('shallow depth of field, sharp subject focus, soft background bokeh').
    2. Tokens obligatorios de nitidez y textura:
       - Todos y cada uno de los 'prompt_en' DEBEN terminar exactamente con la siguiente coletilla de calidad:
         ", 8k resolution, crisp details, sharp focus, high texture detail, cinematic photography, 35mm film aesthetics"
    3. Sustitución de entidades:
       - Si se menciona un personaje o lugar de la guía de entidades, usa sus rasgos físicos en inglés en lugar del nombre propio.

    Para cada plano indica:
    - id: identificador secuencial (s1, s2, ...)
    - order: número correlativo (1, 2, ...)
    - description: qué se debe ver visualmente en pantalla (en español)
    - prompt_en: prompt visual completo en INGLÉS cumpliendo con las 3 reglas anteriores
    - shot_type: 'graphic', 'stock' o 'ai_generated'
    - source: 'library' si es graphic, 'stock' si es stock, 'ai_generated' si es ai_generated
    - duration_seconds: duración del plano

    Responde ÚNICAMENTE con un JSON válido con este formato:
    {{
      "storyboard": [
        {{
          "id": "s1",
          "order": 1,
          "description": "Plano de...",
          "prompt_en": "Medium shot of a diplomat examining blueprints, shallow depth of field, 8k resolution, crisp details, sharp focus, high texture detail, cinematic photography, 35mm film aesthetics",
          "shot_type": "ai_generated",
          "source": "ai_generated",
          "duration_seconds": 4.5
        }}
      ]
    }}
    """

    response = _call_gemini_with_retry(prompt)
    data = _parse_json_safely(response.text)
    return data.get("storyboard", [])


def construir_prompt_imagen(descripcion_plano: str, estilo_global: str, entidades: dict) -> str:
    texto = descripcion_plano
    for nombre, rasgos in entidades.items():
        texto = re.sub(re.escape(nombre), rasgos, texto, flags=re.IGNORECASE)
    return f"{texto}, {estilo_global}, medium shot, shallow depth of field, 8k resolution, crisp details, sharp focus, high texture detail, cinematic photography, 35mm film aesthetics"


def _create_slide_image(text: str, shot_type: str, output_path: Path):
    width, height = 1920, 1080
    img = Image.new("RGB", (width, height), color=(15, 23, 42))
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("arial.ttf", 46)
        font_badge = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font_title = ImageFont.load_default()
        font_badge = ImageFont.load_default()

    draw.rectangle([40, 40, width - 40, height - 40], outline=(40, 50, 70), width=3)
    draw.text((100, 100), f"[{shot_type.upper()}]", fill=(56, 189, 248), font=font_badge)

    words = text.split()
    lines, current_line = [], []
    for word in words:
        current_line.append(word)
        if len(" ".join(current_line)) > 50:
            lines.append(" ".join(current_line))
            current_line = []
    if current_line:
        lines.append(" ".join(current_line))

    y_start = height // 2 - (len(lines) * 35)
    for i, line in enumerate(lines):
        draw.text((100, y_start + (i * 70)), line, fill=(241, 245, 249), font=font_title)

    img.save(output_path)


def _generate_ai_image(prompt_text: str, output_path: Path, max_retries: int = 3):
    """Genera la imagen con IA de forma rápida y estable."""
    # Limpiamos y acotamos el prompt a una longitud segura para la URL
    clean_prompt = re.sub(r"[^\w\s,.\-]", "", prompt_text).strip()[:240]
    encoded_prompt = urllib.parse.quote(clean_prompt)
    seed = random.randint(1000, 999999)

    GEN_WIDTH, GEN_HEIGHT = 1280, 720
    FINAL_WIDTH, FINAL_HEIGHT = 1920, 1080

    # Usamos model=turbo (o sin parámetro de modelo) y quitamos enhance=true
    url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width={GEN_WIDTH}&height={GEN_HEIGHT}&model=turbo"
        f"&nologo=true&seed={seed}"
    )

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if POLLINATIONS_API_KEY:
        headers["Authorization"] = f"Bearer {POLLINATIONS_API_KEY.strip()}"

    backoff = 6.0
    last_error: Exception | None = None

    for attempt in range(max_retries):
        _esperar_turno_pollinations()
        try:
            req = urllib.request.Request(url, headers=headers)
            # Con el modelo turbo, 25 segundos son suficientes para completar la descarga
            with urllib.request.urlopen(req, timeout=25) as response:
                raw_bytes = response.read()

            try:
                with Image.open(io.BytesIO(raw_bytes)) as img:
                    img.verify()
                with Image.open(io.BytesIO(raw_bytes)) as img:
                    ancho, alto = img.size
                    if ancho < 256 or alto < 256:
                        raise ValueError(f"Dimensiones insuficientes ({ancho}x{alto})")
                    imagen_final = img.convert("RGB").resize(
                        (FINAL_WIDTH, FINAL_HEIGHT), Image.LANCZOS
                    )
                    imagen_final.save(output_path, "PNG")
            except Exception as err_validacion:
                raise ValueError(f"Respuesta corrupta o no es imagen: {err_validacion}")

            return  # Descarga exitosa

        except urllib.error.HTTPError as err:
            last_error = err
            if err.code == 429 and attempt < max_retries - 1:
                print(f"      [429 Límite alcanzado] Esperando {backoff}s antes de reintentar...")
                time.sleep(backoff)
                backoff *= 2
                continue
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            raise
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            raise

    if last_error:
        raise last_error

def provision_asset(
    storyboard_item: dict,
    project_id: str = "default",
    estilo_global: str | None = None,
    entidades: dict | None = None,
) -> dict:
    item_id = storyboard_item["id"]
    filename = f"{project_id}_{item_id}.png"
    filepath = ASSETS_DIR / filename

    desc = storyboard_item.get("description", "Cinematic scene")
    shot_type = storyboard_item.get("shot_type", "ai_generated")
    estilo_global = estilo_global or "Cinematic documentary style, realistic lighting, high detail"
    entidades = entidades or {}

    prompt_final = storyboard_item.get("prompt_en")
    if not prompt_final:
        prompt_final = construir_prompt_imagen(desc, estilo_global, entidades)

    print(f"-> Generando imagen para plano {item_id}: {prompt_final[:80]}...")
    try:
        _generate_ai_image(prompt_final, filepath)
        print(f"   [OK] Imagen guardada en {filename}")
        return {
            "status": "ready",
            "type": "image",
            "url": f"http://localhost:8000/renders/assets/{filename}",
            "provider": "pollinations-flux",
            "duration_seconds": storyboard_item.get("duration_seconds", 4.0),
            "error_message": None,
        }
    except Exception as err:
        # Antes esto devolvía "status": "ready" y "error_message": None SIEMPRE,
        # aunque la generación con IA hubiera fallado del todo — así era imposible
        # saber qué planos habían fallado de verdad ni por qué. Ahora se marca como
        # "error" (la tarjeta de repuesto ya existe en disco y no bloquea el montaje,
        # pero queda visible en el panel de abastecimiento con el motivo real y se
        # puede reintentar).
        motivo = str(err)[:300]
        print(f"   [Fallo definitivo] {item_id} ({motivo}). Usando tarjeta gráfica.")
        _create_slide_image(desc, shot_type, filepath)
        return {
            "status": "error",
            "type": "image",
            "url": f"http://localhost:8000/renders/assets/{filename}",
            "provider": "fallback-slide",
            "duration_seconds": storyboard_item.get("duration_seconds", 4.0),
            "error_message": f"No se pudo generar la imagen con IA: {motivo}",
        }


def render_video(project) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "-", project.title.lower()).strip("-")[:35] or "video"
    output_filename = f"{slug}-{uuid.uuid4().hex[:6]}.mp4"
    output_path = RENDERS_DIR / output_filename

    script_blocks = project.script or []
    full_voice_text = " ".join([b.get("text", "") for b in script_blocks])
    if not full_voice_text.strip():
        full_voice_text = project.description or project.title

    audio_path = RENDERS_DIR / f"{project.id}_voice.mp3"
    voice_name = "es-ES-AlvaroNeural"
    if project.voice_selection and isinstance(project.voice_selection, dict):
        voice_name = project.voice_selection.get("provider_voice_id", voice_name)

    async def _make_tts():
        communicate = edge_tts.Communicate(full_voice_text, voice_name)
        await communicate.save(str(audio_path))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_make_tts())
    finally:
        loop.close()

    storyboard = project.storyboard or []
    segment_files = []

    for i, shot in enumerate(storyboard):
        duration = float(shot.get("duration_seconds", 3.0))
        img_path = ASSETS_DIR / f"{project.id}_{shot['id']}.png"
        if not img_path.exists():
            _create_slide_image(shot.get("description", "Escena"), "graphic", img_path)

        seg_path = RENDERS_DIR / f"{project.id}_seg_{i}.mp4"
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(img_path),
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-r", "30", "-t", str(duration), "-pix_fmt", "yuv420p",
            "-vf", "scale=1920:1080:flags=lanczos", str(seg_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Fallo de FFmpeg en el plano {shot['id']}: {res.stderr}")
        segment_files.append(seg_path)

    concat_list_path = RENDERS_DIR / f"{project.id}_concat.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for seg in segment_files:
            f.write(f"file '{seg.resolve().as_posix()}'\n")

    full_video_temp = RENDERS_DIR / f"{project.id}_temp_video.mp4"
    cmd_concat = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list_path), "-c", "copy", str(full_video_temp)
    ]
    res_concat = subprocess.run(cmd_concat, capture_output=True, text=True)
    if res_concat.returncode != 0:
        raise RuntimeError(f"Fallo al concatenar vídeos: {res_concat.stderr}")

    cmd_final = [
        "ffmpeg", "-y", "-i", str(full_video_temp), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-shortest", str(output_path)
    ]
    res_final = subprocess.run(cmd_final, capture_output=True, text=True)
    if res_final.returncode != 0:
        raise RuntimeError(f"Fallo al mezclar audio y vídeo: {res_final.stderr}")

    for seg in segment_files:
        seg.unlink(missing_ok=True)
    concat_list_path.unlink(missing_ok=True)
    full_video_temp.unlink(missing_ok=True)

    return {
        "final_video_url": f"http://localhost:8000/renders/{output_filename}"
    }