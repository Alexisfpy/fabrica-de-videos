import asyncio
import base64
import subprocess
import io
import json
import os
import random
import re
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path
from dotenv import find_dotenv, load_dotenv
import edge_tts
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

load_dotenv(find_dotenv())

VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3.6-flash")
gemini_api_key = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None

# --- Configuración Cloudflare Workers AI ---
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CF_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
CF_MODEL = os.getenv("CLOUDFLARE_AI_MODEL", "@cf/black-forest-labs/flux-1-schnell")

_cf_last_call = 0.0
_CF_MIN_INTERVAL = 1.0

ASSETS_DIR = Path("renders/assets")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
RENDERS_DIR = Path("renders")
RENDERS_DIR.mkdir(parents=True, exist_ok=True)

BGM_LIBRARY_DIR = Path("assets/bgm")
BGM_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)


def _esperar_turno_cloudflare():
    global _cf_last_call
    ahora = time.monotonic()
    espera = _CF_MIN_INTERVAL - (ahora - _cf_last_call)
    if espera > 0:
        time.sleep(espera)
    _cf_last_call = time.monotonic()


def _crop_to_16_9(img: Image.Image) -> Image.Image:
    w, h = img.size
    target_aspect = 16 / 9
    current_aspect = w / h
    if abs(current_aspect - target_aspect) < 0.01:
        return img
    if current_aspect < target_aspect:
        new_h = int(w / target_aspect)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))
    else:
        new_w = int(h * target_aspect)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))


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
            return gemini_client.models.generate_content(model=VISION_MODEL, contents=prompt, config=config)
        except Exception as e:
            err_msg = str(e)
            is_transient = any(code in err_msg for code in ["503", "429", "UNAVAILABLE", "high demand", "ResourceExhausted"])
            if is_transient and attempt < max_retries - 1:
                match = re.search(r"retry in ([\d\.]+)s", err_msg, re.IGNORECASE)
                if match:
                    wait_time = float(match.group(1)) + 2.0
                    print(f"[Gemini 429] Cuota alcanzada. Esperando {wait_time:.1f}s...")
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
        return {"summary": "Referencia procesada con valores por defecto.", "avg_shot_seconds": 4.0, "total_shots": 40,
                "structure": [{"block": "desarrollo", "seconds": 60}], "source_url": reference_url}
    prompt = f"""
    Analiza el estilo y ritmo de vídeo para una referencia en: {reference_url}
    Extrae la estructura típica de edición, el ritmo en segundos por plano y un resumen del estilo.
    Devuelve un JSON con este esquema exacto:
    {{"summary": "...", "avg_shot_seconds": 4.5, "total_shots": 60,
      "structure": [{{"block": "gancho", "seconds": 8}}], "source_url": "{reference_url}"}}
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
    Responde ÚNICAMENTE con un JSON válido:
    {{"blocks": [{{"id": "b1", "order": 1, "text": "...", "estimated_seconds": 15.0}}]}}
    """
    response = _call_gemini_with_retry(prompt)
    data = _parse_json_safely(response.text)
    return data.get("blocks", [])


def analizar_estilo_proyecto(tema: str, sinopsis: str) -> dict:
    fallback = {"visual_style": "Cinematic documentary style, realistic lighting, high detail, 35mm photograph", "entities": {}}
    if not gemini_client:
        return fallback
    prompt = f"""
    Analiza este proyecto de vídeo y devuelve un JSON con:
    1. "visual_style": Descripción en inglés del estilo artístico.
    2. "entities": Diccionario de personajes/lugares recurrentes con descripción física visual detallada en inglés.
    Tema: {tema}
    Sinopsis: {sinopsis}
    Responde ÚNICAMENTE con el JSON.
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


def generate_storyboard(script: list[dict],
                        estilo_global: str = "Cinematic documentary style, realistic lighting, high detail, 35mm photograph",
                        entidades: dict | None = None) -> list[dict]:
    entidades = entidades or {}
    prompt = f"""
    A partir de este guion de locución, genera el desglose de planos (storyboard visual).
    Estilo visual del proyecto: {estilo_global}
    Guía de entidades: {json.dumps(entidades, ensure_ascii=False)}
    Guion: {json.dumps(script, ensure_ascii=False)}

    REGLAS ESTRICTAS DE 'prompt_en':
    1. Rostros: PRIORIZA planos medios o primeros planos. Profundidad de campo reducida.
    2. Cada 'prompt_en' DEBE terminar con: ", 8k resolution, crisp details, sharp focus, high texture detail, cinematic photography, 35mm film aesthetics"
    3. Sustituye entidades por sus rasgos físicos.

    Para cada plano: id, order, description, prompt_en, shot_type, source, duration_seconds.
    Responde ÚNICAMENTE con: {{"storyboard": [...]}}
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
            lines.append(" ".join(current_line)); current_line = []
    if current_line:
        lines.append(" ".join(current_line))
    y_start = height // 2 - (len(lines) * 35)
    for i, line in enumerate(lines):
        draw.text((100, y_start + (i * 70)), line, fill=(241, 245, 249), font=font_title)
    img.save(output_path)


def _generate_ai_image(prompt_text: str, output_path: Path, max_retries: int = 3):
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        raise ValueError("Faltan CLOUDFLARE_ACCOUNT_ID o CLOUDFLARE_API_TOKEN en el .env")
    clean_prompt = re.sub(r"[^\w\s,.\-]", "", prompt_text).strip()[:800]
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID.strip()}/ai/run/{CF_MODEL}"
    body_data = json.dumps({"prompt": clean_prompt, "steps": 4}).encode("utf-8")
    headers = {"Authorization": f"Bearer {CF_API_TOKEN.strip()}", "Content-Type": "application/json", "User-Agent": "FabricaVideos/1.0"}
    FINAL_WIDTH, FINAL_HEIGHT = 1920, 1080
    last_error: Exception | None = None
    for attempt in range(max_retries):
        _esperar_turno_cloudflare()
        try:
            req = urllib.request.Request(endpoint, data=body_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=35) as response:
                raw_bytes = response.read()
            image_bytes = raw_bytes
            try:
                data = json.loads(raw_bytes.decode("utf-8"))
                if isinstance(data, dict):
                    if not data.get("success", True):
                        raise ValueError(f"Error en Cloudflare: {data.get('errors', [])}")
                    if "result" in data and "image" in data["result"]:
                        image_bytes = base64.b64decode(data["result"]["image"])
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            with Image.open(io.BytesIO(image_bytes)) as img:
                img.verify()
            with Image.open(io.BytesIO(image_bytes)) as img:
                img_16_9 = _crop_to_16_9(img.convert("RGB"))
                imagen_final = img_16_9.resize((FINAL_WIDTH, FINAL_HEIGHT), Image.LANCZOS)
                imagen_final.save(output_path, "PNG")
            return
        except urllib.error.HTTPError as err:
            last_error = err
            err_body = err.read().decode("utf-8", errors="ignore")
            print(f"      [Cloudflare HTTP {err.code}] {err_body[:200]}")
            if attempt < max_retries - 1:
                time.sleep(4 if err.code == 429 else 2)
                continue
            raise
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2); continue
            raise
    if last_error:
        raise last_error


def provision_asset(storyboard_item: dict, project_id: str = "default",
                    estilo_global: str | None = None, entidades: dict | None = None) -> dict:
    item_id = storyboard_item["id"]
    filename = f"{project_id}_{item_id}.png"
    filepath = ASSETS_DIR / filename
    desc = storyboard_item.get("description", "Cinematic scene")
    shot_type = storyboard_item.get("shot_type", "ai_generated")
    estilo_global = estilo_global or "Cinematic documentary style, realistic lighting, high detail"
    entidades = entidades or {}
    prompt_final = storyboard_item.get("prompt_en") or construir_prompt_imagen(desc, estilo_global, entidades)
    print(f"-> Generando imagen para plano {item_id}: {prompt_final[:80]}...")
    try:
        _generate_ai_image(prompt_final, filepath)
        print(f"   [OK] Imagen guardada en {filename}")
        return {"status": "ready", "type": "image", "url": f"http://localhost:8000/renders/assets/{filename}",
                "provider": "cloudflare-workers-ai", "duration_seconds": storyboard_item.get("duration_seconds", 4.0),
                "error_message": None}
    except Exception as err:
        motivo = str(err)[:300]
        print(f"   [Fallo definitivo] {item_id} ({motivo}). Usando tarjeta gráfica.")
        _create_slide_image(desc, shot_type, filepath)
        return {"status": "error", "type": "image", "url": f"http://localhost:8000/renders/assets/{filename}",
                "provider": "fallback-slide", "duration_seconds": storyboard_item.get("duration_seconds", 4.0),
                "error_message": f"No se pudo generar la imagen con IA: {motivo}"}


def _get_project_dimensions(project) -> tuple[int, int, int, int]:
    formato = str(getattr(project, "aspect_ratio", "") or getattr(project, "format", "")
                  or getattr(project, "video_format", "") or "16:9").lower()
    if any(k in formato for k in ["9:16", "short", "vertical", "reel", "tiktok"]):
        return 1080, 1920, 1440, 2560
    elif any(k in formato for k in ["1:1", "square", "cuadrado"]):
        return 1080, 1080, 1440, 1440
    else:
        return 1920, 1080, 2560, 1440


def _detect_aspect_ratio(project) -> str:
    formato = str(getattr(project, "aspect_ratio", "") or getattr(project, "format", "")
                  or getattr(project, "video_format", "") or "16:9").lower()
    if any(k in formato for k in ["9:16", "short", "vertical", "reel", "tiktok"]):
        return "9:16"
    return "16:9"


# ============================================================================
# KEN BURNS (zoom suave) — versión corregida contra el jitter
# ============================================================================
#
# CAMBIOS clave respecto a la versión original que te hacía micro-saltos:
#
#  1. Canvas de trabajo MUCHO más grande. Antes: 2560px (para salida 1920px).
#     Con solo 1.33x de margen, el crop de zoompan avanza ~0.5px/frame, se
#     cuantiza y provoca el "tirón". Ahora: 6000px → ~1.5px/frame → fluido.
#
#  2. Se ELIMINA la alternancia zoom-in/zoom-out. Alternar dirección hacía que
#     el ojo percibiera un "rebote" en cada corte. Ahora siempre zoom-in suave.
#
#  3. Acumulador `zoom+delta` en vez de `1+delta*on`. El segundo redondea mal
#     en frames intermedios y produce saltos discretos.

KENBURNS_FPS = 30
KENBURNS_ZOOM_START = 1.0
KENBURNS_ZOOM_END = 1.08
KENBURNS_UPSCALE_LONG_EDGE = 6000   # antes 2560 → ESTA es la clave del arreglo


def _build_kenburns_segment(img_path: Path, seg_path: Path, duration: float,
                            target_w: int, target_h: int, is_vertical: bool,
                            shot_index: int) -> list[str]:
    total_frames = max(int(round(duration * KENBURNS_FPS)), 1)
    zoom_delta = (KENBURNS_ZOOM_END - KENBURNS_ZOOM_START) / max(total_frames, 1)

    # Acumulador: arranca en 1.0 (valor por defecto de zoompan) y sube lineal.
    z_expr = f"min(zoom+{zoom_delta:.8f},{KENBURNS_ZOOM_END})"
    x_expr = "iw/2-(iw/zoom/2)"
    y_expr = "ih/2-(ih/zoom/2)"

    if is_vertical:
        # 9:16 → fondo desenfocado (zoom suave) + plano 16:9 nítido centrado.
        fg_h = int(round(target_w * 9 / 16))
        kb_bg = (f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
                 f"d={total_frames}:s={target_w}x{target_h}:fps={KENBURNS_FPS}")
        kb_fg = (f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
                 f"d={total_frames}:s={target_w}x{fg_h}:fps={KENBURNS_FPS}")
        filter_str = (
            f"[0:v]split=2[raw_bg][raw_fg];"
            f"[raw_bg]scale=3240:5760:force_original_aspect_ratio=increase,"
            f"crop=3240:5760,{kb_bg},boxblur=25:5[bg];"
            f"[raw_fg]scale=3240:-1:force_original_aspect_ratio=increase,{kb_fg}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p"
        )
        filter_flag = "-filter_complex"
    else:
        canvas_w = KENBURNS_UPSCALE_LONG_EDGE
        canvas_h = int(round(canvas_w * 9 / 16))
        kb = (f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
              f"d={total_frames}:s={target_w}x{target_h}:fps={KENBURNS_FPS}")
        filter_str = (
            f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
            f"crop={canvas_w}:{canvas_h},{kb},format=yuv420p"
        )
        filter_flag = "-vf"

    return ["ffmpeg", "-y", "-loop", "1", "-framerate", str(KENBURNS_FPS),
            "-i", str(img_path), filter_flag, filter_str,
            "-c:v", "libx264", "-preset", "veryfast",
            "-t", str(duration), "-r", str(KENBURNS_FPS), str(seg_path)]


# ============================================================================
# TTS + SUBTÍTULOS
# ============================================================================

async def _synthesize_tts_con_timestamps(text: str, voice: str, output_path: Path) -> list[dict]:
    """
    Sintetiza con Edge-TTS troceando el texto en bloques de ~350 caracteres.

    Si Edge-TTS NO emite WordBoundary (ocurre en algunas versiones/entornos),
    se genera un fallback proporcional DENTRO DE CADA BLOQUE: sabemos la
    duración exacta de cada trozo (ffprobe) y repartimos las palabras según
    su longitud. Esto es MUCHO más preciso que repartir uniformemente todo
    el audio, porque respeta los límites reales de cada bloque.
    """
    def _split_text(s: str, max_len: int = 350) -> list[str]:
        frases = re.split(r'(?<=[\.\!\?\:;])\s+', s.strip())
        chunks, actual = [], ""
        for f in frases:
            if not f:
                continue
            if len(actual) + len(f) + 1 <= max_len:
                actual = (actual + " " + f).strip()
            else:
                if actual:
                    chunks.append(actual)
                while len(f) > max_len:
                    corte = f.rfind(",", 0, max_len)
                    if corte < max_len // 2:
                        corte = f.rfind(" ", 0, max_len)
                    if corte < max_len // 2:
                        corte = max_len
                    chunks.append(f[:corte].strip())
                    f = f[corte:].lstrip(" ,")
                actual = f
        if actual:
            chunks.append(actual)
        return [c for c in chunks if c.strip()]

    chunks = _split_text(text, max_len=350)
    print(f"[TTS] Texto troceado en {len(chunks)} bloques.")

    tmp_dir = output_path.parent / f"_tts_tmp_{uuid.uuid4().hex[:6]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    chunk_files: list[Path] = []
    chunk_boundaries: list[list[dict]] = []
    chunk_durations: list[float] = []

    for idx, chunk in enumerate(chunks):
        chunk_path = tmp_dir / f"chunk_{idx:03d}.mp3"
        chunk_files.append(chunk_path)
        boundaries: list[dict] = []

        try:
            communicate = edge_tts.Communicate(chunk, voice, boundary="WordBoundary")
            with open(chunk_path, "wb") as f:
                async for piece in communicate.stream():
                    # Acceso robusto: funciona con dicts y con objetos en distintas
                    # versiones de edge-tts.
                    if isinstance(piece, dict):
                        ptype = piece.get("type")
                        data = piece.get("data")
                        offset = piece.get("offset")
                        duration = piece.get("duration")
                        text_w = piece.get("text")
                    else:
                        ptype = getattr(piece, "type", None)
                        data = getattr(piece, "data", None)
                        offset = getattr(piece, "offset", None)
                        duration = getattr(piece, "duration", None)
                        text_w = getattr(piece, "text", None)

                    if ptype == "audio" and data:
                        f.write(data)
                    elif ptype == "WordBoundary" and offset is not None and duration is not None:
                        boundaries.append({
                            "text": text_w or "",
                            "start": offset / 10_000_000,
                            "end": (offset + duration) / 10_000_000,
                        })
        except Exception as e:
            print(f"[TTS] Error en bloque {idx+1}: {e}")
            # Asegura que al menos hay un MP3 (aunque vacío) para no romper el concat.
            chunk_path.touch(exist_ok=True)

        dur = _get_audio_duration(chunk_path)
        chunk_boundaries.append(boundaries)
        chunk_durations.append(dur)
        print(f"[TTS] Bloque {idx+1}/{len(chunks)}: {len(boundaries)} WordBoundary, {dur:.2f}s de audio.")

    # Concatenar los MP3 de cada bloque en un único archivo
    concat_txt = tmp_dir / "concat.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for cf in chunk_files:
            f.write(f"file '{cf.resolve().as_posix()}'\n")

    res_join = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_txt), "-c", "copy", str(output_path)
    ], capture_output=True, text=True)
    if res_join.returncode != 0:
        raise RuntimeError(f"Fallo al unir chunks TTS: {res_join.stderr}")

    # Construir la lista de palabras
    total_boundaries = sum(len(b) for b in chunk_boundaries)
    palabras: list[dict] = []
    offset = 0.0

    if total_boundaries > 0:
        # Caso ideal: usar los WordBoundary reales
        for boundaries, dur in zip(chunk_boundaries, chunk_durations):
            for b in boundaries:
                palabras.append({
                    "text": b["text"],
                    "start": offset + b["start"],
                    "end": offset + b["end"],
                })
            offset += dur
    else:
        # Fallback proporcional: dentro de cada bloque repartimos por longitud
        # de palabra. Mucho mejor que el fallback anterior (uniforme global).
        print("[TTS] Sin WordBoundary: usando fallback proporcional por bloque.")
        for chunk, dur in zip(chunks, chunk_durations):
            ws = chunk.split()
            if not ws or dur <= 0:
                offset += dur
                continue
            pesos = [max(len(w), 3) for w in ws]
            total_peso = sum(pesos)
            sub = 0.0
            for w, p in zip(ws, pesos):
                d = dur * (p / total_peso)
                palabras.append({
                    "text": w,
                    "start": offset + sub,
                    "end": offset + sub + d,
                })
                sub += d
            offset += dur

    # Ajuste fino: comparar el fin estimado con la duración real del MP3 unido.
    # La concatenación de MP3 con `-c copy` a veces añade unos ms de padding;
    # si la diferencia es apreciable, reescalamos linealmente.
    dur_final = _get_audio_duration(output_path)
    if palabras and dur_final > 0:
        ultimo_fin = palabras[-1]["end"]
        if ultimo_fin > 0 and abs(dur_final - ultimo_fin) > 0.5:
            factor = dur_final / ultimo_fin
            print(f"[TTS] Reajuste fino: x{factor:.4f} "
                  f"(fin estimado {ultimo_fin:.2f}s → audio real {dur_final:.2f}s)")
            for p in palabras:
                p["start"] *= factor
                p["end"] *= factor

    # Limpieza
    for cf in chunk_files:
        cf.unlink(missing_ok=True)
    concat_txt.unlink(missing_ok=True)
    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    print(f"[Subtítulos] Total de palabras sincronizadas: {len(palabras)}")
    return palabras


def _formatear_tiempo_ass(segundos: float) -> str:
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    segs = segundos % 60
    return f"{horas}:{minutos:02d}:{segs:05.2f}"


def _generar_subtitulos_ass(palabras: list[dict], output_path: Path,
                            max_palabras_por_cue: int = 5, max_segundos_por_cue: float = 2.5,
                            resolution: tuple[int, int] = (1920, 1080), is_vertical: bool = False) -> bool:
    if not palabras:
        return False
    res_w, res_h = resolution
    font_size = 54 if is_vertical else 42
    margen_v = int(res_h * 0.18) if is_vertical else int(res_h * 0.10)

    cues: list[dict] = []
    actual: list[dict] = []

    def _cerrar_cue():
        if actual:
            cues.append({"start": actual[0]["start"], "end": actual[-1]["end"],
                         "text": " ".join(p["text"] for p in actual)})

    for palabra in palabras:
        if actual and (len(actual) >= max_palabras_por_cue
                       or (palabra["end"] - actual[0]["start"]) > max_segundos_por_cue):
            _cerrar_cue(); actual = []
        actual.append(palabra)
    _cerrar_cue()

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_w}
PlayResY: {res_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3.5,1.5,2,30,30,{margen_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lineas = []
    for cue in cues:
        texto = cue["text"].replace("\n", " ").strip()
        if not texto:
            continue
        lineas.append(f"Dialogue: 0,{_formatear_tiempo_ass(cue['start'])},{_formatear_tiempo_ass(cue['end'])},Default,,0,0,0,,{texto}")
    output_path.write_text(header + "\n".join(lineas) + "\n", encoding="utf-8")
    print(f"[Subtítulos] Archivo .ass generado: {output_path.name}")
    return True


def _escapar_ruta_ffmpeg_filtro(path: Path) -> str:
    return path.resolve().as_posix().replace(":", "\\:")


def _elegir_pista_bgm() -> Path | None:
    candidatas = list(BGM_LIBRARY_DIR.glob("*.mp3")) + list(BGM_LIBRARY_DIR.glob("*.wav"))
    return random.choice(candidatas) if candidatas else None


def _get_audio_duration(audio_path: Path) -> float:
    """Duración del archivo de audio según ffprobe (INCLUYE silencio de cola)."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


# ============================================================================
# RENDER PRINCIPAL (con el arreglo real de sincronización)
# ============================================================================

def render_video(project) -> dict:
    ratio = _detect_aspect_ratio(project)
    is_vertical = (ratio == "9:16")
    target_w, target_h, _, _ = _get_project_dimensions(project)

    slug = re.sub(r"[^a-z0-9]+", "-", project.title.lower()).strip("-")[:35] or "video"
    output_filename = f"{slug}-{uuid.uuid4().hex[:6]}.mp4"
    output_path = RENDERS_DIR / output_filename

    script_blocks = project.script or []
    full_voice_text = " ".join([b.get("text", "") for b in script_blocks]) or project.description or project.title

    audio_path = RENDERS_DIR / f"{project.id}_voice.mp3"
    voice_name = "es-ES-AlvaroNeural"
    if project.voice_selection and isinstance(project.voice_selection, dict):
        voice_name = project.voice_selection.get("provider_voice_id", voice_name)

    # 1) TTS con timestamps reales por palabra
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        palabras = loop.run_until_complete(_synthesize_tts_con_timestamps(full_voice_text, voice_name, audio_path))
    finally:
        loop.close()

    audio_file_duration = _get_audio_duration(audio_path)
    # *** ESTE es el arreglo: usamos el FIN REAL de la locución, no la duración del MP3
    # (Edge-TTS añade silencio al final del MP3 y eso desincronizaba los subtítulos).
    last_word_end = palabras[-1]["end"] if palabras else audio_file_duration
    print(f"[Sync] Duración MP3 (ffprobe): {audio_file_duration:.2f}s | "
          f"Fin real locución: {last_word_end:.2f}s | "
          f"Silencio de cola: {audio_file_duration - last_word_end:.2f}s")

    # 2) Ajustar duraciones de planos para que su suma == fin real de la locución
    storyboard = list(project.storyboard or [])
    total_shot_duration = sum(float(s.get("duration_seconds", 3.0)) for s in storyboard)

    if total_shot_duration > 0 and last_word_end > 0.1:
        factor = last_word_end / total_shot_duration
        if abs(factor - 1.0) > 0.005:
            print(f"[Sync] Ajustando {len(storyboard)} planos: factor={factor:.4f} "
                  f"({total_shot_duration:.2f}s → {last_word_end:.2f}s)")
            for s in storyboard:
                s["duration_seconds"] = round(float(s.get("duration_seconds", 3.0)) * factor, 4)
        else:
            print(f"[Sync] Duraciones ya coinciden (factor={factor:.4f}). Sin ajuste.")
    else:
        print("[Sync] Aviso: duración total de planos o locución no válida.")

    # 3) Subtítulos .ass (con los timestamps REALES del audio, sin escalar)
    subs_path = RENDERS_DIR / f"{project.id}_subs.ass"
    tiene_subtitulos = _generar_subtitulos_ass(palabras, subs_path,
                                                resolution=(target_w, target_h), is_vertical=is_vertical)

    # 4) Segmentos con Ken Burns (zoom suave, canvas grande)
    segment_files = []
    for i, shot in enumerate(storyboard):
        duration = float(shot.get("duration_seconds", 3.0))
        img_path = ASSETS_DIR / f"{project.id}_{shot['id']}.png"
        if not img_path.exists():
            _create_slide_image(shot.get("description", "Escena"), "graphic", img_path)
        seg_path = RENDERS_DIR / f"{project.id}_seg_{i}.mp4"
        cmd = _build_kenburns_segment(img_path, seg_path, duration, target_w, target_h, is_vertical, i)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Fallo de FFmpeg en el plano {shot['id']}: {res.stderr}")
        segment_files.append(seg_path)

    # 5) Concatenar. -avoid_negative_ts + -fflags +genpts fuerzan PTS desde 0,
    #    imprescindible para que los subtítulos caigan sobre el frame correcto.
    concat_list_path = RENDERS_DIR / f"{project.id}_concat.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for seg in segment_files:
            f.write(f"file '{seg.resolve().as_posix()}'\n")

    full_video_temp = RENDERS_DIR / f"{project.id}_temp_video.mp4"
    res_concat = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list_path),
        "-c", "copy",
        "-fflags", "+genpts",
        "-avoid_negative_ts", "make_zero",
        "-fps_mode", "cfr",
        str(full_video_temp)
    ], capture_output=True, text=True)
    if res_concat.returncode != 0:
        raise RuntimeError(f"Fallo al concatenar: {res_concat.stderr}")

    # 6) Mix final
    bgm_path = _elegir_pista_bgm()
    inputs = ["-i", str(full_video_temp), "-i", str(audio_path)]
    filter_parts = []
    if tiene_subtitulos and subs_path.exists():
        filter_parts.append(f"[0:v]ass='{subs_path.as_posix()}'[vout]")
        video_map = "[vout]"
    else:
        video_map = "0:v"

    if bgm_path:
        inputs += ["-stream_loop", "-1", "-i", str(bgm_path)]
        filter_parts.append(
            "[2:a]volume=0.28[bgm_vol];"
            "[bgm_vol][1:a]sidechaincompress=threshold=0.05:ratio=8:attack=5:release=400[bgm_duck];"
            "[1:a][bgm_duck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = "1:a"

    cmd_final = ["ffmpeg", "-y", *inputs]
    if filter_parts:
        cmd_final += ["-filter_complex", ";".join(filter_parts)]
    cmd_final += ["-map", video_map, "-map", audio_map,
                  "-c:v", "libx264", "-preset", "veryfast",
                  "-c:a", "aac", "-shortest", str(output_path)]
    res_final = subprocess.run(cmd_final, capture_output=True, text=True)
    if res_final.returncode != 0:
        raise RuntimeError(f"Fallo al mezclar audio y subtítulos: {res_final.stderr}")

    for seg in segment_files:
        seg.unlink(missing_ok=True)
    concat_list_path.unlink(missing_ok=True)
    full_video_temp.unlink(missing_ok=True)

    return {"final_video_url": f"http://localhost:8000/renders/{output_filename}"}


# ============================================================================
# DEMO EJECUTABLE
# ============================================================================

if __name__ == "__main__":
    import types as _types
    DEMO_ID = "demo"
    print("\n=== DEMO FÁBRICA DE VÍDEOS ===\n")

    demo_storyboard = [
        {"id": "s1", "order": 1, "description": "Primer plano del Nintendo Entertainment System.",
         "prompt_en": "Close-up of the original Nintendo Entertainment System on a wooden table, 8k resolution, crisp details, sharp focus, high texture detail, cinematic photography, 35mm film aesthetics",
         "shot_type": "ai_generated", "source": "ai_generated", "duration_seconds": 4.0},
        {"id": "s2", "order": 2, "description": "Game Boy original en manos de un niño.",
         "prompt_en": "Medium shot of a child holding a classic Game Boy, shallow depth of field, 8k resolution, crisp details, sharp focus, high texture detail, cinematic photography, 35mm film aesthetics",
         "shot_type": "ai_generated", "source": "ai_generated", "duration_seconds": 3.5},
        {"id": "s3", "order": 3, "description": "Super Nintendo con cartucho insertado.",
         "prompt_en": "Super Nintendo console with cartridge inserted, cinematic lighting, 8k resolution, crisp details, sharp focus, high texture detail, cinematic photography, 35mm film aesthetics",
         "shot_type": "ai_generated", "source": "ai_generated", "duration_seconds": 4.5},
        {"id": "s4", "order": 4, "description": "Nintendo 64 con mando de tres puntas.",
         "prompt_en": "Nintendo 64 with its three-pronged controller, dramatic lighting, 8k resolution, crisp details, sharp focus, high texture detail, cinematic photography, 35mm film aesthetics",
         "shot_type": "ai_generated", "source": "ai_generated", "duration_seconds": 3.0},
        {"id": "s5", "order": 5, "description": "Nintendo Switch 2 en modo portátil.",
         "prompt_en": "Nintendo Switch 2 in handheld mode, modern product shot, 8k resolution, crisp details, sharp focus, high texture detail, cinematic photography, 35mm film aesthetics",
         "shot_type": "ai_generated", "source": "ai_generated", "duration_seconds": 5.0},
    ]

    demo_script = [
        {"id": "b1", "order": 1, "text": "Nintendo empezó en 1977 con la Color TV-Game, una consola que casi nadie recuerda pero que plantó la semilla de todo lo que vino después.", "estimated_seconds": 8.0},
        {"id": "b2", "order": 2, "text": "Con la NES llegó el fenómeno global, y con Game Boy, la revolución portátil que vendió más de cien millones de unidades.", "estimated_seconds": 8.0},
        {"id": "b3", "order": 3, "text": "La Super Nintendo perfeccionó la fórmula con juegos que aún hoy se consideran obras maestras absolutas del medio.", "estimated_seconds": 8.0},
        {"id": "b4", "order": 4, "text": "La Nintendo 64 apostó por el 3D y nos dio el control analógico, cambiando para siempre cómo jugamos.", "estimated_seconds": 8.0},
        {"id": "b5", "order": 5, "text": "Y hoy, con Switch 2, Nintendo sigue demostrando que no necesita competir en potencia para ganar la partida.", "estimated_seconds": 8.0},
    ]

    demo_project = _types.SimpleNamespace(
        id=DEMO_ID,
        title="Todas las consolas de Nintendo explicadas",
        description="Repaso cronológico de las consolas de Nintendo.",
        script=demo_script,
        storyboard=demo_storyboard,
        voice_selection={"provider_voice_id": "es-ES-AlvaroNeural"},
        aspect_ratio="16:9",
    )

    print("Generando assets de prueba...")
    for shot in demo_storyboard:
        img_path = ASSETS_DIR / f"{DEMO_ID}_{shot['id']}.png"
        if not img_path.exists():
            try:
                provision_asset(shot, project_id=DEMO_ID)
            except Exception as e:
                print(f"  Fallback gráfico para {shot['id']}: {e}")
                _create_slide_image(shot["description"], shot["shot_type"], img_path)

    print("\nRenderizando vídeo...\n")
    result = render_video(demo_project)
    print(f"\n✅ Vídeo generado: {result['final_video_url']}")
    print(f"   Archivo local: {RENDERS_DIR}/")