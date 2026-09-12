# Fábrica de Vídeos

Implementación funcional (backend + frontend) del diseño técnico: una app
para generar vídeos automatizados con IA a partir de un tema, siguiendo el
pipeline Referencia → Guion → Voz y tiempos → Plan visual → Abastecimiento
→ Montaje.

## Qué hay aquí

- **`backend/`** — API en FastAPI + SQLAlchemy (SQLite por defecto). Implementa
  todos los endpoints del diseño técnico. Las llamadas a proveedores de IA
  reales (LLM, Gemini Vision, Stable Diffusion/Kling, ElevenLabs, FFmpeg)
  están **mockeadas** en `app/services/mock_ai.py` para que puedas ejecutar
  y probar el pipeline completo sin credenciales de ninguna API — la forma
  de los datos que devuelven es la misma que tendría la integración real,
  así que sustituir cada función por la llamada de verdad no debería
  requerir tocar el resto de la app.
- **`frontend/`** — SPA en React + TypeScript + Tailwind v4 (Vite) con el
  editor de proyecto (timeline de 6 pasos, editor de guion, storyboard con
  filtros y reintento de planos individuales) y el listado "Tus vídeos".

## Cómo ejecutarlo

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

La API queda en `http://localhost:8000`. Documentación interactiva
(Swagger) en `http://localhost:8000/docs`. La base de datos SQLite se crea
sola en `backend/fabrica_de_videos.db` al arrancar.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Abre `http://localhost:5173`. El `vite.config.ts` ya tiene un proxy de
`/api` hacia `http://127.0.0.1:8000`, así que no hace falta configurar CORS
ni variables de entorno para desarrollo local.

## Qué es real y qué es una simplificación deliberada

Para poder entregarte algo que se ejecuta de punta a punta sin
infraestructura externa, se simplificó respecto al diseño técnico original
en tres puntos, documentados también como comentarios en el propio código:

1. **Sin Celery/Redis** — los "workers" son funciones de Python que corren
   como `BackgroundTasks` de FastAPI en el mismo proceso
   (`app/services/pipeline.py`). Migrar a Celery más adelante es sobre todo
   decidir quién llama a esas funciones, no reescribirlas.
2. **Sin autenticación real** — hay un único usuario de demo
   (`app/deps.py`) con saldo de créditos fijo, en vez del flujo JWT +
   OAuth2 del diseño técnico.
3. **Progreso por polling, no WebSocket** — el frontend consulta
   `GET /projects/{id}` cada ~1.2s mientras el proyecto está en un estado
   "ocupado", en vez de suscribirse a un canal de eventos.

 Practicamente todo lo demás — modelo de datos, endpoints, contrato de la
API, comportamiento del abastecimiento plano a plano con reintentos
independientes — sigue el diseño técnico tal cual.

## Siguiente paso natural

Sustituir `app/services/mock_ai.py` función por función por llamadas reales
(Claude/Gemini para guion, Gemini Vision + OpenCV para el análisis de
referencia, un proveedor de imagen/vídeo, ElevenLabs/Azure para TTS y
FFmpeg para el montaje final) es el camino más directo hacia producción,
sin tener que tocar routers, modelos ni frontend.
