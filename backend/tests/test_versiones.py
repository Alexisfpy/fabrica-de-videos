import asyncio
import edge_tts
import edge_tts.constants

VERSIONES_A_PROBAR = [
    "1-131.0.2903.86",
    "1-130.0.2849.68",
    "1-133.0.3065.92",
    "1-135.0.3166.76",
]

async def probar():
    for v in VERSIONES_A_PROBAR:
        print(f"Probando versión: {v}...")
        edge_tts.constants.SEC_MS_GEC_VERSION = v
        try:
            com = edge_tts.Communicate("Probando síntesis", "es-ES-AlvaroNeural")
            await com.save("test_ok.mp3")
            print(f"\n✅ ¡ÉXITO con la versión: {v}!")
            return v
        except Exception as e:
            print(f"   Rechazada ({e})")
    print("\n❌ Ninguna versión por defecto fue aceptada.")
    return None

if __name__ == "__main__":
    asyncio.run(probar())