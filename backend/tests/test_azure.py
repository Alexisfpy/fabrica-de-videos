import os
from pathlib import Path
from dotenv import find_dotenv, load_dotenv
import azure.cognitiveservices.speech as speechsdk

load_dotenv(find_dotenv())

key = os.getenv("AZURE_SPEECH_KEY")
region = os.getenv("AZURE_SPEECH_REGION")

speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
speech_config.speech_synthesis_voice_name = "es-ES-AlvaroNeural"
speech_config.set_speech_synthesis_output_format(
    speechsdk.SpeechSynthesisOutputFormat.Audio16Khz128KBitRateMonoMp3
)

output_file = Path("test_azure.mp3")
audio_config = speechsdk.audio.AudioOutputConfig(filename=str(output_file))
synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

print("Sintetizando con Azure Speech...")
result = synthesizer.speak_text_async("Prueba de audio oficial desde Azure North Europe").get()

if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
    print(f"✅ ¡Éxito! Archivo generado: {output_file.resolve()} ({output_file.stat().st_size} bytes)")
else:
    cancellation = result.cancellation_details
    print(f"❌ Error: {cancellation.reason} - {cancellation.error_details}")