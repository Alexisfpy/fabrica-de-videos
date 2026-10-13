import asyncio
import base64
import concurrent.futures
import gc
import io
import json
import os
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from dotenv import find_dotenv, load_dotenv
import edge_tts
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

import azure.cognitiveservices.speech as speechsdk

load_dotenv(find_dotenv())

VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3.8-flash")
gemini_api_key = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None

# --- Configuración Cloudflare Workers AI ---
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CF_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
CF_MODEL = os.getenv("CLOUDFLARE_AI_MODEL", "@cf/black-forest-labs/flux-1-schnell")

_cf_last_call = 0.0
_CF_MIN_INTERVAL = 1.0

# Margen musical al final para pantallas finales / tarjetas interactivas de YouTube
OUTRO_BUFFER_SECONDS = 7.0

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

# Cascada de modelos disponibles en tu cuenta para evitar errores 503 / 429
FALLBACK_MODELS = [
    os.getenv("VISION_MODEL", "gemini-3.8-flash"),
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-pro",
]


def _call_gemini_with_retry(prompt: str, max_retries: int = 1, json_mode: bool = True):
    if not gemini_client:
        raise ValueError("GEMINI_API_KEY no está configurada en el archivo .env")

    config = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
    
    modelos_a_probar = []
    for m in FALLBACK_MODELS:
        m_clean = m.replace("models/", "").strip()
        if m_clean and m_clean not in modelos_a_probar:
            modelos_a_probar.append(m_clean)

    ultimo_error = None

    for model_name in modelos_a_probar:
        try:
            print(f"[Gemini] Solicitando con {model_name}...", flush=True)
            return gemini_client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )
        except Exception as e:
            err_msg = str(e)
            ultimo_error = e
            
            es_fallback = any(k in err_msg for k in [
                "503", "429", "404", "UNAVAILABLE", "high demand", 
                "ResourceExhausted", "not found", "no longer available"
            ])
            
            if es_fallback:
                print(f"[Gemini Fallback] {model_name} no disponible ({err_msg[:75]}...). Pasando al siguiente modelo...", flush=True)
                continue
            raise e

    raise RuntimeError(f"Todos los modelos de la lista de respaldo fallaron. Último error: {ultimo_error}")


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


# ============================================================================
# 1. SISTEMA DINÁMICO DE FONÉTICA (BASE + DETECCIÓN GEMINI)
# ============================================================================

DICCIONARIO_FONETICA_BASE = {
    r"\bGPU\b": "G-P-U",
    r"\bGPUs\b": "G-P-Us",
    r"\bCPU\b": "C-P-U",
    r"\bCPUs\b": "C-P-Us",
    r"\bTPU\b": "T-P-U",
    r"\bTPUs\b": "T-P-Us",
    r"\bSMR\b": "S-M-R",
    r"\bSMRs\b": "S-M-Rs",
    r"\bLLM\b": "L-L-M",
    r"\bLLMs\b": "L-L-Ms",
    r"\bAPI\b": "A-P-I",
    r"\bAPIs\b": "A-P-Is",
    r"\bkW\b": "kilovatios",
    r"\bMW\b": "megavatios",
    r"\bGW\b": "gigavatios",
}

REVERSION_SUBTITULOS_BASE = {
    "G-P-U": "GPU",
    "G-P-Us": "GPUs",
    "C-P-U": "CPU",
    "C-P-Us": "CPUs",
    "T-P-U": "TPU",
    "T-P-Us": "TPUs",
    "S-M-R": "SMR",
    "S-M-Rs": "SMRs",
    "L-L-M": "LLM",
    "L-L-Ms": "LLMs",
    "A-P-I": "API",
    "A-P-Is": "APIs",
}


def detectar_fonetica_con_ia(texto: str) -> list[dict]:
    prompt = f"""
    Eres un director de doblaje y lingüista experto en síntesis de voz (Text-to-Speech) en español de España.
    Analiza este texto narrativo y detecta:
    1. Siglas y acrónimos técnicos (ej: TSMC, CUDA, ASIC, HBM3e, PCIe, FP8, MoE, DRAM).
    2. Nombres propios de personas, empresas, lugares y modelos en inglés o extranjeros (ej: Ilya Sutskever -> "Ilia Súts-ke-ver", Mark Zuckerberg -> "Mark Zúker-berg", Jensen Huang -> "Yensen Juang", Sam Altman -> "Sam Óltman", Microsoft -> "Máicrosoft", Three Mile Island -> "Tri-Mail-Áiland", Susquehanna -> "Sas-que-jana", Blackwell -> "Blak-üel", DeepSeek -> "Dip-Sik").
    3. Anglicismos técnicos donde una adaptación fonética en español mejore la dicción natural del sintetizador (ej: hardware -> "járd-üer", software -> "sóft-üer", baseload -> "beis-lód", racks -> "raks", direct-to-chip -> "direct-tu-chip", datacenter -> "data-sénter").

    REGLAS:
    - NO incluyas palabras normales en español.
    - NO incluyas siglas ya estándar si se leen de forma obvia en español.
    - Responde ÚNICAMENTE con un JSON válido:
    {{"sustituciones": [
        {{"original": "TSMC", "tts": "T-S-M-C"}},
        {{"original": "Zuckerberg", "tts": "Zúker-berg"}}
    ]}}

    Texto a analizar:
    {texto[:5000]}
    """
    try:
        response = _call_gemini_with_retry(prompt)
        data = _parse_json_safely(response.text)
        return data.get("sustituciones", [])
    except Exception as e:
        print(f"[Fonética IA] Aviso: No se pudo generar fonética dinámica ({e}). Se usará solo la base.", flush=True)
        return []


def generar_diccionarios_foneticos(texto: str, project_id: str = "default") -> tuple[dict, dict]:
    dicc_fonetica = dict(DICCIONARIO_FONETICA_BASE)
    dicc_reversion = dict(REVERSION_SUBTITULOS_BASE)

    cache_file = RENDERS_DIR / f"{project_id}_phonetics.json"
    sustituciones_ia = []

    if cache_file.exists():
        try:
            sustituciones_ia = json.loads(cache_file.read_text(encoding="utf-8"))
            print(f"[Fonética IA] Cargadas {len(sustituciones_ia)} reglas desde caché ({cache_file.name}).", flush=True)
        except Exception:
            sustituciones_ia = []

    if not sustituciones_ia and gemini_client:
        print("[Fonética IA] Analizando guion con Gemini para detectar siglas y nombres extranjeros...", flush=True)
        sustituciones_ia = detectar_fonetica_con_ia(texto)
        if sustituciones_ia:
            try:
                cache_file.write_text(json.dumps(sustituciones_ia, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[Fonética IA] {len(sustituciones_ia)} términos detectados y guardados en caché.", flush=True)
            except Exception:
                pass

    for item in sustituciones_ia:
        orig = item.get("original", "").strip()
        tts = item.get("tts", "").strip()
        if orig and tts and orig.lower() != tts.lower():
            patron = rf"\b{re.escape(orig)}\b"
            dicc_fonetica[patron] = tts
            dicc_reversion[tts] = orig

    return dicc_fonetica, dicc_reversion


def aplicar_sustitucion_fonetica(texto: str, dicc_fonetica: dict) -> str:
    if not texto:
        return ""
    t = texto
    for patron, reemplazo in dicc_fonetica.items():
        t = re.sub(patron, reemplazo, t, flags=re.IGNORECASE)
    return t


def restaurar_ortografia_subtitulos(texto: str, dicc_reversion: dict) -> str:
    if not texto:
        return ""
    t = texto
    for fon, orig in dicc_reversion.items():
        t = re.sub(re.escape(fon), orig, t, flags=re.IGNORECASE)
    return t


def limpiar_texto_locucion(texto: str) -> str:
    if not texto:
        return ""

    t = re.sub(r"\[.*?\]", " ", texto)
    t = re.sub(r"\(.*?\)", " ", t)

    patrones_tecnicos = [
        r"(?i)\b(b-roll|b_roll|broll)\b\s*:?",
        r"(?i)\b(primerísimo\s+primer\s+plano|primer\s+plano|plano\s+[a-zá-ú]+|toma\s+[a-zá-ú]+|cámara\s+[a-zá-ú]+)\s*:?",
        r"(?i)\b(corte\s+a|transición|zoom\s+in|zoom\s+out|paneo)\b\s*:?",
        r"(?i)\b(pantalla\s+dividida|infografía|gráfico\s+en\s+pantalla)\b\s*:?",
        r"(?i)\b(un\s+especialista\s+analiza.*?cámara|el\s+macro\s+revela.*?|observa\s+la\s+contracción.*?)\b\s*:?",
    ]
    for patron in patrones_tecnicos:
        t = re.sub(patron, " ", t)

    t = re.sub(r"\s+", " ", t).strip()
    return t


def generate_script(title: str, description: str, target_duration_seconds: int, reference_analysis: dict | None) -> list[dict]:
    style_guide = ""
    if reference_analysis:
        style_guide = f"Toma como referencia un ritmo de corte promedio de {reference_analysis.get('avg_shot_seconds', 3.5)}s."

    min_palabras = int((target_duration_seconds / 60) * 145)

    prompt = f"""
    Eres el guionista principal de un canal de divulgación y ensayos visuales de alto rigor técnico.
    Tema: {title}
    Detalles clave: {description}
    Duración objetivo total: {target_duration_seconds} segundos.
    {style_guide}

    FILOSOFÍA Y FIRMA DEL CANAL ("CUELLO DE BOTELLA"):
    1. Debes articular las explicaciones utilizando el concepto de 'cuello de botella' como marco analítico recurrente. Sin embargo, limita estrictamente el uso de la frase exacta "cuello de botella" a un MÁXIMO de 2 veces en todo el guion. Para el resto de ocasiones, usa sinónimos como "limitación crítica", "obstáculo estructural" o "punto de estrangulamiento"
    2. Identifica siempre el factor limitante de cada sistema y cómo se resuelve o colapsa.

    REGLAS EDITORIALES Y DE LOCUCIÓN ESTRICTAS:
    1. EL CAMPO "text" ES EXCLUSIVAMENTE LO QUE EL LOCUTOR DICE PALABRA POR PALABRA.
    2. TOTALMENTE PROHIBIDO incluir acotaciones visuales, B-roll, direcciones de cámara o notas entre paréntesis.
    3. EXTENSIÓN OBLIGATORIA: El guion COMPLETO debe tener como MÍNIMO {min_palabras} palabras reales narradas en total.
    4. HOOK INICIAL (0-5s): Prohibido abrir con saludos o introducciones vacías. Empieza en mitad del clímax con una cifra perturbadora.
    5. LONGITUD POR BLOQUE: Cada bloque debe tener entre 25 y 40 palabras conversacionales continuas.
    6. TONO: Ritmo ágil, periodismo de investigación, verbos directos en presente activo.
    7. ESTRUCTURA: Conflicto inicial -> Revelación de datos poco conocidos -> Consecuencias reales -> Conclusión contundente.

    Divide el guion en bloques cronológicos para la voz en off.
    Responde ÚNICAMENTE con un JSON válido:
    {{"blocks": [{{"id": "b1", "order": 1, "text": "...", "estimated_seconds": 13.0}}]}}
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


PROMPT_NEGATIVO_FLUX = (
    "clean industrial scene, strictly no text, no letters, no words, no fonts, "
    "no typography, no watermark, no labels, no signs, no subtitles, no billboards, "
    "no readable logos, blank dark screens, clean display panels without letters"
)


def generate_storyboard(script: list[dict],
                        estilo_global: str = "Cinematic documentary style, realistic lighting, high detail, 35mm photograph",
                        entidades: dict | None = None) -> list[dict]:
    entidades = entidades or {}
    prompt = f"""
    A partir de este guion de locución, genera el desglose de planos (storyboard visual).
    Estilo visual del proyecto: {estilo_global}
    Guía de entidades: {json.dumps(entidades, ensure_ascii=False)}
    Guion: {json.dumps(script, ensure_ascii=False)}

    REGLAS EDITORIALES DE EDICIÓN:
    1. DURACIÓN: Cada plano DEBE durar entre 2.0 y 3.5 segundos máximo.
    2. VARIEDAD DE ENCUADRE: Alterna constantemente entre 'extreme close-up', 'medium shot', 'low angle dramatic shot' y 'wide panoramic shot'.
    3. REGLA ESTRICTA ANTI-TEXTO: TOTALMENTE PROHIBIDO describir texto legible, carteles, titulares o rótulos en monitores.
    4. PROMPT EN: Termina siempre con: ", {PROMPT_NEGATIVO_FLUX}, 8k resolution, crisp details, sharp focus, high texture detail, cinematic photography, 35mm film aesthetics".

    Devuelve para cada plano: id, order, description, prompt_en, shot_type, source, duration_seconds.
    Responde ÚNICAMENTE con: {{"storyboard": [...]}}
    """
    response = _call_gemini_with_retry(prompt)
    data = _parse_json_safely(response.text)
    return data.get("storyboard", [])


def construir_prompt_imagen(descripcion_plano: str, estilo_global: str, entidades: dict) -> str:
    texto = descripcion_plano
    for nombre, rasgos in entidades.items():
        texto = re.sub(re.escape(nombre), rasgos, texto, flags=re.IGNORECASE)
    return (
        f"{texto}, {estilo_global}, {PROMPT_NEGATIVO_FLUX}, medium shot, "
        f"shallow depth of field, 8k resolution, crisp details, sharp focus, "
        f"high texture detail, cinematic photography, 35mm film aesthetics"
    )


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
    endpoint = f"[https://api.cloudflare.com/client/v4/accounts/](https://api.cloudflare.com/client/v4/accounts/){CF_ACCOUNT_ID.strip()}/ai/run/{CF_MODEL}"
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
            print(f"      [Cloudflare HTTP {err.code}] {err_body[:200]}", flush=True)
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


def sanitizar_prompt_anti_texto(prompt_en: str) -> str:
    t = prompt_en
    frases_negativas = [
        r",\s*strictly no text.*",
        r",\s*no text.*",
        r",\s*no letters.*",
        r",\s*without letters.*",
    ]
    for fn in frases_negativas:
        t = re.sub(fn, "", t, flags=re.IGNORECASE)

    t = re.sub(r"(?i)\b(cockpit )?weight[- ]and[- ]balance screen\b", "cockpit control yoke, throttle quadrant and glowing illuminated instruments", t)
    t = re.sub(r"(?i)\bcockpit screen\b", "cockpit instrument panel with glowing analog dials and switches", t)
    t = re.sub(r"(?i)\b(computer|terminal|data|server) screen\b", "server rack LED telemetry status lights", t)
    t = re.sub(r"(?i)\b(touchscreen|monitor|display screen)\b", "dark instrument panel with abstract glow", t)
    t = re.sub(r"(?i)\b(showing|displaying) (data|text|code|metrics|values)\b", "showing abstract geometric thermal telemetry", t)
    return f"{t.strip()}, minimal industrial aesthetic, clean surface, cinematic shallow depth of field"


def provision_asset(storyboard_item: dict, project_id: str = "default",
                    estilo_global: str | None = None, entidades: dict | None = None) -> dict:
    item_id = storyboard_item["id"]
    filename = f"{project_id}_{item_id}.png"
    filepath = ASSETS_DIR / filename
    desc = storyboard_item.get("description", "Cinematic scene")
    shot_type = storyboard_item.get("shot_type", "ai_generated")
    estilo_global = estilo_global or "Cinematic documentary style, realistic lighting, high detail"
    entidades = entidades or {}

    prompt_base = storyboard_item.get("prompt_en") or construir_prompt_imagen(desc, estilo_global, entidades)
    prompt_final = sanitizar_prompt_anti_texto(prompt_base)

    print(f"-> Generando imagen para plano {item_id}: {prompt_final[:80]}...", flush=True)
    try:
        _generate_ai_image(prompt_final, filepath)
        print(f"   [OK] Imagen guardada en {filename}", flush=True)
        return {"status": "ready", "type": "image", "url": f"http://localhost:8000/renders/assets/{filename}",
                "provider": "cloudflare-workers-ai", "duration_seconds": storyboard_item.get("duration_seconds", 4.0),
                "error_message": None}
    except Exception as err:
        motivo = str(err)[:300]
        print(f"   [Fallo definitivo] {item_id} ({motivo}). Usando tarjeta gráfica.", flush=True)
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
# KEN BURNS (zoom suave) — lienzo 6000px contra jitter
# ============================================================================

KENBURNS_FPS = 30
KENBURNS_ZOOM_START = 1.0
KENBURNS_ZOOM_END = 1.08
KENBURNS_UPSCALE_LONG_EDGE = 6000


def _build_kenburns_segment(img_path: Path, seg_path: Path, duration: float,
                            target_w: int, target_h: int, is_vertical: bool,
                            shot_index: int, focus_x: int = 50, focus_y: int = 50) -> list[str]:
    total_frames = max(int(round(duration * KENBURNS_FPS)), 1)
    
    fx = max(0.0, min(1.0, focus_x / 100.0))
    fy = max(0.0, min(1.0, focus_y / 100.0))

    zoom_step = 0.08 / max(total_frames, 1)
    movimiento = shot_index % 3

    if movimiento == 0:
        z_expr = f"min(zoom+{zoom_step:.8f}, 1.08)"
        x_expr = f"(iw-iw/zoom)*{fx:.4f}"
        y_expr = f"(ih-ih/zoom)*{fy:.4f}"
    elif movimiento == 1:
        z_expr = f"max(1.08-{zoom_step:.8f}*on, 1.0)"
        x_expr = f"(iw-iw/zoom)*{fx:.4f}"
        y_expr = f"(ih-ih/zoom)*{fy:.4f}"
    else:
        z_expr = "1.08"
        pan_rango = 0.12
        pan_inicio = max(0.0, min(1.0 - pan_rango, fx - (pan_rango / 2.0)))
        x_expr = f"(iw-iw/zoom)*({pan_inicio:.4f} + (on/{total_frames})*{pan_rango:.4f})"
        y_expr = f"(ih-ih/zoom)*{fy:.4f}"

    if is_vertical:
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
# TTS: MOTOR HÍBRIDO (AZURE OFICIAL COMO PRINCIPAL + EDGE-TTS COMO RESPALDO)
# ============================================================================

def _synthesize_azure_tts(text: str, voice: str, output_path: Path) -> list[dict]:
    """Síntesis oficial autenticada con Azure Speech SDK (sin errores 403 y con limpieza tolerante a Windows)."""
    speech_key = os.getenv("AZURE_SPEECH_KEY")
    speech_region = os.getenv("AZURE_SPEECH_REGION", "northeurope")

    if not voice or "Neural" not in voice:
        voice = "es-ES-AlvaroNeural"

    def _split_text(s: str, max_len: int = 600) -> list[str]:
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
                actual = f
        if actual:
            chunks.append(actual)
        return [c for c in chunks if c.strip()]

    chunks = _split_text(text, max_len=600)
    print(f"\n[Azure Speech] Sintetizando {len(chunks)} bloques con '{voice}'...", flush=True)

    tmp_dir = output_path.parent / f"_tts_tmp_{uuid.uuid4().hex[:6]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    chunk_files: list[Path] = []
    chunk_boundaries: list[list[dict]] = []
    chunk_durations: list[float] = []

    for idx, chunk in enumerate(chunks):
        chunk_path = tmp_dir / f"chunk_{idx:03d}.mp3"
        chunk_files.append(chunk_path)
        chunk_limpio = chunk.replace("...", " — ").replace(" - ", ", ")

        speech_config = speechsdk.SpeechConfig(subscription=speech_key.strip(), region=speech_region.strip())
        speech_config.speech_synthesis_voice_name = voice
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio16Khz128KBitRateMonoMp3
        )
        audio_config = speechsdk.audio.AudioOutputConfig(filename=str(chunk_path.resolve()))
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

        boundaries: list[dict] = []

        def word_cb(evt):
            is_word = getattr(evt, "boundary_type", None) == speechsdk.SpeechSynthesisBoundaryType.Word
            if is_word and evt.text:
                dur_sec = evt.duration.total_seconds() if hasattr(evt.duration, "total_seconds") else (float(evt.duration) / 10_000_000.0)
                start_sec = float(evt.audio_offset) / 10_000_000.0
                boundaries.append({"text": evt.text, "start": start_sec, "end": start_sec + dur_sec})

        synthesizer.synthesis_word_boundary.connect(word_cb)

        escaped = (
            chunk_limpio.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
        )
        ssml = (
            f'<speak version="1.0" xmlns="[http://www.w3.org/2001/10/synthesis](http://www.w3.org/2001/10/synthesis)" xml:lang="es-ES">'
            f'<voice name="{voice}"><prosody rate="+6%">{escaped}</prosody></voice></speak>'
        )

        res = synthesizer.speak_ssml_async(ssml).get()
        if res.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            boundaries.clear()
            res = synthesizer.speak_text_async(chunk_limpio).get()

        if res.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = res.cancellation_details
            raise RuntimeError(f"Error en Azure: {details.reason} - {details.error_details}")

        palabras_orig = [w for w in chunk_limpio.split() if any(c.isalnum() for c in w)]
        if len(palabras_orig) == len(boundaries):
            for b, orig_w in zip(boundaries, palabras_orig):
                b["text"] = orig_w

        dur = _get_audio_duration(chunk_path)
        chunk_boundaries.append(boundaries)
        chunk_durations.append(dur)
        print(f"[Azure Speech] Bloque {idx+1}/{len(chunks)}: {len(boundaries)} palabras, {dur:.2f}s.", flush=True)

        # Liberar descriptores de archivos de C++ en Windows inmediatamente
        del synthesizer
        del audio_config
        time.sleep(0.05)

    # Forzar recolección de basura para cerrar cualquier handle nativo abierto
    gc.collect()

    concat_txt = None
    if len(chunk_files) == 1:
        shutil.copyfile(chunk_files[0], output_path)
    else:
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

    palabras: list[dict] = []
    offset = 0.0
    for boundaries, dur in zip(chunk_boundaries, chunk_durations):
        for b in boundaries:
            palabras.append({"text": b["text"], "start": offset + b["start"], "end": offset + b["end"]})
        offset += dur

    dur_final = _get_audio_duration(output_path)
    if palabras and dur_final > 0:
        fin_est = palabras[-1]["end"]
        if fin_est > 0 and abs(dur_final - fin_est) > 0.5:
            factor = dur_final / fin_est
            for p in palabras:
                p["start"] *= factor
                p["end"] *= factor

    # Limpieza defensiva en Windows: protegida contra WinError 32
    for cf in chunk_files:
        try:
            cf.unlink(missing_ok=True)
        except OSError:
            pass

    if concat_txt and concat_txt.exists():
        try:
            concat_txt.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except OSError:
        pass

    return palabras


async def _synthesize_edge_tts(text: str, voice: str, output_path: Path) -> list[dict]:
    """Función original de Edge-TTS guardada para respaldo futuro."""
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
    print(f"[Edge-TTS] Texto troceado en {len(chunks)} bloques.", flush=True)

    tmp_dir = output_path.parent / f"_tts_tmp_{uuid.uuid4().hex[:6]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    chunk_files: list[Path] = []
    chunk_boundaries: list[list[dict]] = []
    chunk_durations: list[float] = []

    for idx, chunk in enumerate(chunks):
        chunk_path = tmp_dir / f"chunk_{idx:03d}.mp3"
        chunk_files.append(chunk_path)
        boundaries: list[dict] = []
        chunk_limpio = chunk.replace("...", " — ").replace(" - ", ", ")
        exito = False
        ultimo_error_tts = None

        for intento in range(3):
            boundaries.clear()
            try:
                communicate = edge_tts.Communicate(chunk_limpio, voice, rate="+6%", pitch="+0Hz", boundary="WordBoundary")
                with open(chunk_path, "wb") as f:
                    async for piece in communicate.stream():
                        ptype = piece.get("type") if isinstance(piece, dict) else getattr(piece, "type", None)
                        data = piece.get("data") if isinstance(piece, dict) else getattr(piece, "data", None)
                        offset = piece.get("offset") if isinstance(piece, dict) else getattr(piece, "offset", None)
                        duration = piece.get("duration") if isinstance(piece, dict) else getattr(piece, "duration", None)
                        text_w = piece.get("text") if isinstance(piece, dict) else getattr(piece, "text", None)

                        if ptype == "audio" and data:
                            f.write(data)
                        elif ptype == "WordBoundary" and offset is not None and duration is not None:
                            boundaries.append({
                                "text": text_w or "",
                                "start": offset / 10_000_000,
                                "end": (offset + duration) / 10_000_000,
                            })

                if chunk_path.exists() and chunk_path.stat().st_size > 1000:
                    exito = True
                    break
                else:
                    raise IOError(f"Audio vacío ({chunk_path.stat().st_size} bytes)")
            except Exception as e:
                ultimo_error_tts = e
                print(f"[Edge-TTS] Reintento {intento + 1}/3 en bloque {idx + 1}: {e}", flush=True)
                await asyncio.sleep(1.5)

        if not exito:
            raise RuntimeError(f"Fallo definitivo en Edge-TTS: {ultimo_error_tts}")

        palabras_orig = [w for w in chunk_limpio.split() if any(c.isalnum() for c in w)]
        if len(palabras_orig) == len(boundaries):
            for b, orig_w in zip(boundaries, palabras_orig):
                b["text"] = orig_w

        dur = _get_audio_duration(chunk_path)
        chunk_boundaries.append(boundaries)
        chunk_durations.append(dur)
        await asyncio.sleep(0.3)

    concat_txt = tmp_dir / "concat.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for cf in chunk_files:
            f.write(f"file '{cf.resolve().as_posix()}'\n")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_txt), "-c", "copy", str(output_path)
    ], check=True, capture_output=True)

    palabras: list[dict] = []
    offset = 0.0
    for boundaries, dur in zip(chunk_boundaries, chunk_durations):
        for b in boundaries:
            palabras.append({"text": b["text"], "start": offset + b["start"], "end": offset + b["end"]})
        offset += dur

    dur_final = _get_audio_duration(output_path)
    if palabras and dur_final > 0:
        fin_est = palabras[-1]["end"]
        if fin_est > 0 and abs(dur_final - fin_est) > 0.5:
            factor = dur_final / fin_est
            for p in palabras:
                p["start"] *= factor
                p["end"] *= factor

    for cf in chunk_files:
        try:
            cf.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        concat_txt.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except OSError:
        pass

    return palabras


async def _synthesize_tts_con_timestamps(text: str, voice: str, output_path: Path) -> list[dict]:
    """Punto de entrada principal llamado por render_video."""
    tts_provider = os.getenv("TTS_PROVIDER", "azure").lower()

    if tts_provider == "azure" and os.getenv("AZURE_SPEECH_KEY"):
        return _synthesize_azure_tts(text, voice, output_path)

    try:
        return await _synthesize_edge_tts(text, voice, output_path)
    except Exception as e:
        if os.getenv("AZURE_SPEECH_KEY"):
            print(f"[TTS Fallback] Edge-TTS falló ({e}). Conmutando automáticamente a Azure Speech...", flush=True)
            return _synthesize_azure_tts(text, voice, output_path)
        raise e


def _formatear_tiempo_ass(segundos: float) -> str:
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    segs = segundos % 60
    return f"{horas}:{minutos:02d}:{segs:05.2f}"


def _generar_subtitulos_ass(palabras: list[dict], output_path: Path,
                            diccionario_reversion: dict | None = None,
                            max_palabras_por_cue: int = 4, max_segundos_por_cue: float = 2.2,
                            resolution: tuple[int, int] = (1920, 1080), is_vertical: bool = False) -> bool:
    if not palabras:
        return False
        
    res_w, res_h = resolution
    font_size = 64 if is_vertical else 52
    margen_v = int(res_h * 0.18) if is_vertical else int(res_h * 0.11)

    cues: list[dict] = []
    actual: list[dict] = []

    def _cerrar_cue():
        if actual:
            raw_text = " ".join(p["text"] for p in actual).strip()
            clean_text = restaurar_ortografia_subtitulos(raw_text, diccionario_reversion or {})
            cues.append({
                "start": actual[0]["start"],
                "end": actual[-1]["end"],
                "text": clean_text
            })

    PUNTUACION_TERMINAL = {".", "!", "?", ":", ";", "—"}

    for palabra in palabras:
        texto = palabra.get("text", "").strip()
        if not texto:
            continue

        if actual:
            prev_text = actual[-1].get("text", "").strip()
            prev_end = actual[-1].get("end", 0.0)
            curr_start = palabra.get("start", 0.0)
            curr_end = palabra.get("end", curr_start)
            gap = curr_start - prev_end

            termina_oracion = any(prev_text.endswith(p) for p in PUNTUACION_TERMINAL)
            pausa_notable = gap > 0.25
            es_mayuscula = (texto[0].isupper() and len(actual) >= 2 and not prev_text.endswith(","))
            limite_palabras = len(actual) >= max_palabras_por_cue
            limite_tiempo = (curr_end - actual[0]["start"]) > max_segundos_por_cue

            if termina_oracion or pausa_notable or es_mayuscula or limite_palabras or limite_tiempo:
                _cerrar_cue()
                actual = []

        actual.append(palabra)

    _cerrar_cue()

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_w}
PlayResY: {res_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3.8,1.5,2,30,30,{margen_v},1

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
    print(f"[Subtítulos] Archivo .ass generado: {output_path.name}", flush=True)
    return True


def _escapar_ruta_ffmpeg_filtro(path: Path) -> str:
    return path.resolve().as_posix().replace(":", "\\:")


def _elegir_pista_bgm(categoria: str | None = None) -> Path | None:
    if categoria and categoria.lower() != "none":
        subcarpeta = BGM_LIBRARY_DIR / categoria.lower()
        if subcarpeta.exists():
            candidatas = list(subcarpeta.glob("*.mp3")) + list(subcarpeta.glob("*.wav"))
            if candidatas:
                return random.choice(candidatas)

    todas = list(BGM_LIBRARY_DIR.rglob("*.mp3")) + list(BGM_LIBRARY_DIR.rglob("*.wav"))
    return random.choice(todas) if todas else None


def _get_audio_duration(audio_path: Path) -> float:
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
# 3. RENDER PRINCIPAL (CON FONÉTICA DINÁMICA, OUTRO Y FEEDBACK EN VIVO)
# ============================================================================

def render_video(project) -> dict:
    ratio = _detect_aspect_ratio(project)
    is_vertical = (ratio == "9:16")
    target_w, target_h, _, _ = _get_project_dimensions(project)

    slug = re.sub(r"[^a-z0-9]+", "-", project.title.lower()).strip("-")[:35] or "video"
    output_filename = f"{slug}-{uuid.uuid4().hex[:6]}.mp4"
    output_path = RENDERS_DIR / output_filename

    script_blocks = project.script or []
    raw_text = " ".join([b.get("text", "") for b in script_blocks]) or project.description or project.title

    texto_locucion_limpio = limpiar_texto_locucion(raw_text)
    dicc_fonetica, dicc_reversion = generar_diccionarios_foneticos(texto_locucion_limpio, project_id=project.id)
    full_voice_text = aplicar_sustitucion_fonetica(texto_locucion_limpio, dicc_fonetica)

    audio_path = RENDERS_DIR / f"{project.id}_voice.mp3"
    voice_name = "es-ES-AlvaroNeural"
    if project.voice_selection and isinstance(project.voice_selection, dict):
        voice_name = project.voice_selection.get("provider_voice_id", voice_name)

    # Ejecución asíncrona robusta tolerante a hilos de FastAPI y CLI
    try:
        active_loop = asyncio.get_running_loop()
    except RuntimeError:
        active_loop = None

    if active_loop and active_loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as pool:
            palabras = pool.submit(
                lambda: asyncio.run(_synthesize_tts_con_timestamps(full_voice_text, voice_name, audio_path))
            ).result()
    else:
        palabras = asyncio.run(_synthesize_tts_con_timestamps(full_voice_text, voice_name, audio_path))

    audio_file_duration = _get_audio_duration(audio_path)
    last_word_end = palabras[-1]["end"] if palabras else audio_file_duration
    print(f"\n[Sync] Duración MP3 (ffprobe): {audio_file_duration:.2f}s | Fin locución: {last_word_end:.2f}s", flush=True)

    storyboard = list(project.storyboard or [])
    total_shot_duration = sum(float(s.get("duration_seconds", 3.0)) for s in storyboard)

    if total_shot_duration > 0 and last_word_end > 0.1:
        factor = last_word_end / total_shot_duration
        if abs(factor - 1.0) > 0.005:
            print(f"[Sync] Ajustando {len(storyboard)} planos: factor={factor:.4f} ({total_shot_duration:.2f}s → {last_word_end:.2f}s)", flush=True)
            for s in storyboard:
                s["duration_seconds"] = round(float(s.get("duration_seconds", 3.0)) * factor, 4)
        else:
            print(f"[Sync] Duraciones coinciden (factor={factor:.4f}).", flush=True)

    if storyboard:
        duracion_original_ultimo = float(storyboard[-1].get("duration_seconds", 3.0))
        storyboard[-1]["duration_seconds"] = round(duracion_original_ultimo + OUTRO_BUFFER_SECONDS, 4)
        print(f"[Outro] Plano final extendido +{OUTRO_BUFFER_SECONDS}s para tarjetas de YouTube.", flush=True)

    total_video_duration = last_word_end + OUTRO_BUFFER_SECONDS

    subs_path = RENDERS_DIR / f"{project.id}_subs.ass"
    tiene_subtitulos = _generar_subtitulos_ass(
        palabras, subs_path,
        diccionario_reversion=dicc_reversion,
        resolution=(target_w, target_h), is_vertical=is_vertical
    )

    # 5. Segmentos con Ken Burns con progreso visible en tiempo real
    segment_files = []
    total_shots = len(storyboard)
    print(f"\n[FFmpeg] Iniciando montaje de {total_shots} planos de vídeo...", flush=True)

    for i, shot in enumerate(storyboard):
        duration = max(0.5, float(shot.get("duration_seconds", 3.0)))
        img_path = ASSETS_DIR / f"{project.id}_{shot['id']}.png"
        if not img_path.exists():
            _create_slide_image(shot.get("description", "Escena"), "graphic", img_path)
            
        focus_x = int(shot.get("focus_x", 50))
        focus_y = int(shot.get("focus_y", 50))
        
        # Feedback continuo en terminal para evitar sensación de congelamiento
        print(f"[FFmpeg] Procesando plano {i+1}/{total_shots} ({duration:.1f}s)...", flush=True)
        
        seg_path = RENDERS_DIR / f"{project.id}_seg_{i}.mp4"
        cmd = _build_kenburns_segment(img_path, seg_path, duration, target_w, target_h, is_vertical, i, focus_x, focus_y)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Fallo de FFmpeg en el plano {shot['id']}: {res.stderr}")
        segment_files.append(seg_path)

    # 6. Concatenar
    print(f"\n[FFmpeg] Uniendo los {len(segment_files)} clips concatenados...", flush=True)
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

    # 7. Mix final con ducking y subtítulos
    print("[FFmpeg] Realizando mezcla final de audio, música y subtítulos...", flush=True)
    bgm_category = getattr(project, "bgm_category", None) or "documental"
    bgm_path = _elegir_pista_bgm(bgm_category)
    inputs = ["-i", str(full_video_temp), "-i", str(audio_path)]
    filter_parts = []
    if tiene_subtitulos and subs_path.exists():
        filter_parts.append(f"[0:v]ass='{subs_path.as_posix()}'[vout]")
        video_map = "[vout]"
    else:
        video_map = "0:v"

    if bgm_path:
        inputs += ["-stream_loop", "-1", "-i", str(bgm_path)]
        fade_start = max(0.0, total_video_duration - 2.0)
        filter_parts.append(
            f"[1:a]apad=pad_dur={OUTRO_BUFFER_SECONDS},asplit=2[voice_duck][voice_mix];"
            f"[2:a]volume=0.28[bgm_vol];"
            f"[bgm_vol][voice_duck]sidechaincompress=threshold=0.05:ratio=8:attack=5:release=600[bgm_duck];"
            f"[voice_mix][bgm_duck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,afade=t=out:st={fade_start:.2f}:d=2.0[aout]"
        )
        audio_map = "[aout]"
    else:
        filter_parts.append(f"[1:a]apad=pad_dur={OUTRO_BUFFER_SECONDS}[aout]")
        audio_map = "[aout]"

    cmd_final = ["ffmpeg", "-y", *inputs]
    if filter_parts:
        cmd_final += ["-filter_complex", ";".join(filter_parts)]
    cmd_final += ["-map", video_map, "-map", audio_map,
                  "-c:v", "libx264", "-preset", "veryfast",
                  "-c:a", "aac", "-shortest", str(output_path)]
    res_final = subprocess.run(cmd_final, capture_output=True, text=True)
    if res_final.returncode != 0:
        raise RuntimeError(f"Fallo al mezclar audio y subtítulos: {res_final.stderr}")

    # Limpieza final de temporales a prueba de bloqueos de Windows
    for seg in segment_files:
        try:
            seg.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        concat_list_path.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        full_video_temp.unlink(missing_ok=True)
    except OSError:
        pass

    print(f"\n🎉 ¡Vídeo final generado con éxito!: {output_path.resolve()}", flush=True)
    return {"final_video_url": f"http://localhost:8000/renders/{output_filename}"}


# ============================================================================
# DEMO EJECUTABLE
# ============================================================================

if __name__ == "__main__":
    import types as _types
    DEMO_ID = "demo"
    print("\n=== DEMO FÁBRICA DE VÍDEOS ===\n", flush=True)

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

    print("Generando assets de prueba...", flush=True)
    for shot in demo_storyboard:
        img_path = ASSETS_DIR / f"{DEMO_ID}_{shot['id']}.png"
        if not img_path.exists():
            try:
                provision_asset(shot, project_id=DEMO_ID)
            except Exception as e:
                print(f"  Fallback gráfico para {shot['id']}: {e}", flush=True)
                _create_slide_image(shot["description"], shot["shot_type"], img_path)

    print("\nRenderizando vídeo...\n", flush=True)
    result = render_video(demo_project)
    print(f"\n✅ Vídeo generado: {result['final_video_url']}", flush=True)
    print(f"   Archivo local: {RENDERS_DIR}/", flush=True)