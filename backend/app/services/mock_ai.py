import asyncio
import json
import os
import re
import subprocess
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

ASSETS_DIR = Path("renders/assets")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
RENDERS_DIR = Path("renders")
RENDERS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_VOICES = [
    {"provider": "azure", "provider_voice_id": "es-ES-AlvaroNeural", "name": "Álvaro", "gender": "male", "language": "es", "tone": "formal"},
    {"provider": "azure", "provider_voice_id": "es-ES-ElviraNeural", "name": "Elvira", "gender": "female", "language": "es", "tone": "formal"},
    {"provider": "elevenlabs", "provider_voice_id": "es-antonio", "name": "Antonio", "gender": "male", "language": "es", "tone": "narrativo"},
    {"provider": "elevenlabs", "provider_voice_id": "es-lucia", "name": "Lucía", "gender": "female", "language": "es", "tone": "coloquial"},
]


def _call_gemini_with_retry(prompt: str, max_retries: int = 4):
    if not gemini_client:
        raise ValueError("GEMINI_API_KEY no está configurada en el archivo .env")

    delay = 2.0
    for attempt in range(max_retries):
        try:
            return gemini_client.models.generate_content(
                model=VISION_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
        except Exception as e:
            err_msg = str(e)
            is_transient = any(code in err_msg for code in ["503", "429", "UNAVAILABLE", "high demand", "ResourceExhausted"])
            if is_transient and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
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
    """
    Define el "Concepto Visual" del proyecto UNA sola vez: el estilo artístico
    general y una guía de entidades (personajes/lugares/conceptos clave) con su
    descripción física en inglés. Esto es lo que faltaba antes: sin esto, cada
    plano se generaba con un estilo fijo ("anime aesthetic") sin relación con
    el tema real del vídeo, y los personajes cambiaban de aspecto entre planos.
    """
    fallback = {
        "visual_style": "Cinematic documentary style, realistic lighting, high detail, 35mm photograph",
        "entities": {},
    }
    if not gemini_client:
        return fallback

    prompt = f"""
    Analiza este proyecto de vídeo y devuelve un JSON con:
    1. "visual_style": Descripción en inglés del estilo artístico general que mejor
       encaja con el tema (ej: "2D anime screencap, MAPPA style", "Cinematic historical
       documentary, 35mm film photograph", "Hyperrealistic cyberpunk 3D render",
       "Found-footage horror, grainy VHS aesthetic", etc.). Debe encajar con el tema
       real del vídeo, nunca un estilo genérico por defecto.
    2. "entities": Diccionario de personajes, conceptos o lugares clave que se repiten
       en el vídeo, con su descripción física visual detallada en inglés, para que un
       generador de imágenes los dibuje siempre igual. Si no hay personajes recurrentes
       claros, devuelve un diccionario vacío.

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


def construir_prompt_imagen(descripcion_plano: str, estilo_global: str, entidades: dict) -> str:
    """
    Traduce y expande la descripción (en español, a nivel de guion) de un plano
    concreto a un prompt visual en inglés, aplicando el estilo global del proyecto
    y sustituyendo cualquier mención de una entidad por su descripción física fija,
    para mantener la coherencia de personajes entre planos.
    """
    if not gemini_client:
        # Sin Gemini disponible: al menos usamos el estilo del proyecto en vez de uno
        # fijo, y aplicamos la guía de entidades con un reemplazo de texto simple.
        texto = descripcion_plano
        for nombre, rasgos in entidades.items():
            texto = re.sub(re.escape(nombre), rasgos, texto, flags=re.IGNORECASE)
        return f"{texto}, {estilo_global}, high detail, coherent composition"

    prompt_sistema = f"""
    Eres un director de arte visual para modelos de generación de imágenes (SDXL, Flux, Midjourney).
    Convierte la descripción de este plano en un prompt visual en inglés.

    Estilo artístico obligatorio: {estilo_global}
    Guía de entidades y personajes: {json.dumps(entidades, ensure_ascii=False)}

    Instrucciones:
    - Traduce y expande la escena a una descripción puramente visual en inglés.
    - Si se menciona una entidad/personaje, reemplaza su nombre por sus rasgos físicos
      visuales definidos en la guía, para que se vea igual en todos los planos.
    - Especifica tipo de plano, composición, iluminación y paleta de color coherente
      con el estilo artístico indicado arriba (NUNCA cambies el estilo por otro).
    - Devuelve ÚNICAMENTE el prompt final en inglés, sin introducciones ni comillas.
    """
    try:
        response = gemini_client.models.generate_content(
            model=VISION_MODEL,
            contents=f"{prompt_sistema}\n\nPlano a convertir: {descripcion_plano}",
        )
        texto = response.text.strip().strip('"')
        return texto if texto else f"{descripcion_plano}, {estilo_global}"
    except Exception:
        return f"{descripcion_plano}, {estilo_global}, high detail, coherent composition"


def generate_storyboard(script: list[dict]) -> list[dict]:
    prompt = f"""
    A partir de este guion de locución, genera el desglose de planos (storyboard visual):
    {json.dumps(script, ensure_ascii=False)}

    Para cada plano indica:
    - id: identificador secuencial (s1, s2, ...)
    - order: número correlativo (1, 2, ...)
    - description: qué se debe ver visualmente en pantalla
    - shot_type: 'graphic', 'stock' o 'ai_generated'
    - source: 'library' si es graphic, 'stock' si es stock, 'ai_generated' si es ai_generated
    - duration_seconds: duración del plano

    Responde ÚNICAMENTE con un JSON válido:
    {{
      "storyboard": [
        {{
          "id": "s1",
          "order": 1,
          "description": "Plano de...",
          "shot_type": "graphic",
          "source": "library",
          "duration_seconds": 4.5
        }}
      ]
    }}
    """

    response = _call_gemini_with_retry(prompt)
    data = _parse_json_safely(response.text)
    return data.get("storyboard", [])


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
    """
    Genera la imagen con IA gestionando reintentos ante errores 429 y tiempos de espera.

    `prompt_text` debe llegar YA construido (en inglés, con el estilo del proyecto y
    las entidades aplicadas) por `construir_prompt_imagen`. Antes esta función forzaba
    "anime aesthetic" en TODAS las imágenes sin importar el tema del vídeo — eso es lo
    que causaba resultados sin sentido con la temática; ahora se respeta el prompt tal
    cual se le pasa, solo se sanea para que sea válido como URL.
    """
    # Limpiamos solo caracteres problemáticos para una URL, conservando comas/puntos
    # (un prompt de dirección de arte los necesita para separar cláusulas visuales).
    clean_prompt = re.sub(r"[^\w\s,.\-]", "", prompt_text).strip()[:600]
    encoded_prompt = urllib.parse.quote(clean_prompt)
    
    # Añadimos un seed aleatorio para evitar colisiones de caché en el generador
    seed = random.randint(1000, 999999)
    # 1024x576 (16:9) se procesa en ~4-6 segundos y FFmpeg lo escala limpiamente a 1080p
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=576&model=flux&nologo=true&seed={seed}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )

    backoff = 4.0
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                with open(output_path, "wb") as f:
                    f.write(response.read())
            return
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < max_retries - 1:
                print(f"      [429 Límite alcanzado] Esperando {backoff}s antes de reintentar...")
                time.sleep(backoff)
                backoff *= 2
                continue
            raise err
        except Exception as exc:
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            raise exc


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
    estilo_global = estilo_global or "Cinematic, realistic lighting, high detail"
    entidades = entidades or {}

    prompt_final = construir_prompt_imagen(desc, estilo_global, entidades)

    print(f"-> Generando imagen real para plano {item_id}: {prompt_final[:80]}...")
    try:
        _generate_ai_image(prompt_final, filepath)
        print(f"   [OK] Imagen guardada en {filename}")
        # Pausa de cortesía para no saturar el servidor y prevenir nuevos 429
        time.sleep(2)
    except Exception as err:
        print(f"   [Fallo definitivo] {item_id} ({err}). Usando tarjeta gráfica.")
        _create_slide_image(desc, shot_type, filepath)

    return {
        "status": "ready",
        "type": "image",
        "url": f"http://localhost:8000/renders/assets/{filename}",
        "provider": "ai-generator",
        "duration_seconds": storyboard_item.get("duration_seconds", 4.0),
        "error_message": None,
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
            "-t", str(duration), "-pix_fmt", "yuv420p",
            "-vf", "scale=1920:1080", str(seg_path)
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