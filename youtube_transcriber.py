"""
youtube_transcriber.py
Transcribe videos de YouTube sin depender de subtítulos.
Usa Whisper 'small' con detección de español.
Requiere Python 3.10+ en Windows.
"""

import sys
import subprocess
import os
import re


# ── Auto-instalación de dependencias ─────────────────────────────────────────
_DEPS = {
    "yt_dlp":        "yt-dlp",
    "whisper":       "openai-whisper",
    "ffmpeg":        "ffmpeg-python",
    "imageio_ffmpeg":"imageio-ffmpeg",
}

def _ensure_deps() -> None:
    missing = [(imp, pip) for imp, pip in _DEPS.items() if not _is_importable(imp)]
    if not missing:
        return
    print("Instalando dependencias faltantes...")
    for imp, pip in missing:
        print(f"  → {pip} ...", end=" ", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pip, "-q"],
            capture_output=True
        )
        if result.returncode != 0:
            print("ERROR")
            print(result.stderr.decode(errors="replace"))
            sys.exit(1)
        print("OK")
    print()

def _is_importable(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False

_ensure_deps()

# ── Imports tras instalación ──────────────────────────────────────────────────
import yt_dlp           # noqa: E402
import whisper          # noqa: E402
import imageio_ffmpeg   # noqa: E402


# ── Utilidades ────────────────────────────────────────────────────────────────
def _get_ffmpeg() -> str:
    """Devuelve la ruta al ejecutable ffmpeg (bundled vía imageio o del sistema)."""
    try:
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.exists(path):
            return path
    except Exception:
        pass
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        return path
    raise RuntimeError(
        "No se encontró ffmpeg.\n"
        "Solución: pip install imageio-ffmpeg   (ya incluido en este script)"
    )


def _sanitize(name: str) -> str:
    """Limpia el nombre para usarlo como nombre de archivo en Windows."""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:150]


# ── Descarga ──────────────────────────────────────────────────────────────────
def download_audio(url: str, ffmpeg_path: str, dest_dir: str) -> tuple[str, str]:
    """
    Descarga solo el audio del video y lo convierte a WAV.
    Devuelve (ruta_del_wav, título_del_video).
    """
    # Obtener título sin descargar
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        title = _sanitize(info.get("title", "video"))

    out_wav = os.path.join(dest_dir, f"{title}.wav")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(dest_dir, f"{title}.%(ext)s"),
        "ffmpeg_location": os.path.dirname(ffmpeg_path),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "0",
        }],
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    return out_wav, title


# ── Transcripción ─────────────────────────────────────────────────────────────
def transcribe(audio_path: str, ffmpeg_path: str) -> str:
    """Transcribe el audio con Whisper modelo 'small' en español."""
    # Añadir ffmpeg al PATH de este proceso para que Whisper lo encuentre
    ffmpeg_dir = os.path.dirname(ffmpeg_path)
    os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

    print("      Cargando modelo Whisper 'small'", end="")
    print(" (primera ejecución: descarga ~460 MB)..." if not _model_cached() else "...")
    model = whisper.load_model("small")

    result = model.transcribe(
        audio_path,
        language="es",
        fp16=False,      # fp16 requiere GPU CUDA; False = compatible con CPU en Windows
        verbose=False,
    )
    return result["text"]


def _model_cached() -> bool:
    """Comprueba si el modelo 'small' ya fue descargado antes."""
    cache = os.path.join(os.path.expanduser("~"), ".cache", "whisper", "small.pt")
    return os.path.exists(cache)


# ── Limpieza de texto ─────────────────────────────────────────────────────────
def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)            # espacios/saltos múltiples → espacio
    text = re.sub(r" ([.,;:!?])", r"\1", text)  # espacio antes de puntuación
    text = re.sub(r"\.{3,}", "…", text)         # puntos suspensivos
    # Insertar salto de párrafo cada ~3 oraciones para legibilidad
    sentences = re.split(r"(?<=[.!?]) +", text)
    paragraphs = [" ".join(sentences[i:i+3]) for i in range(0, len(sentences), 3)]
    return "\n\n".join(p.strip() for p in paragraphs if p.strip())


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    url = input("URL del video de YouTube: ").strip()
    if not url:
        print("URL vacía. Saliendo.")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Resolver ffmpeg
    try:
        ffmpeg_path = _get_ffmpeg()
    except RuntimeError as e:
        print(f"\n❌ {e}")
        sys.exit(1)

    # ── Paso 1: Descarga ──────────────────────────────────────────────────────
    print("\n[1/3] Descargando audio...")
    try:
        audio_path, title = download_audio(url, ffmpeg_path, script_dir)
    except Exception as e:
        print(f"❌ Error al descargar: {e}")
        sys.exit(1)

    if not os.path.exists(audio_path):
        print(f"❌ Archivo WAV no encontrado: {audio_path}")
        sys.exit(1)

    print(f"      Guardado: {os.path.basename(audio_path)}")

    # ── Paso 2: Transcripción ─────────────────────────────────────────────────
    print("\n[2/3] Transcribiendo (puede tardar varios minutos en CPU)...")
    try:
        raw = transcribe(audio_path, ffmpeg_path)
    except Exception as e:
        print(f"❌ Error al transcribir: {e}")
        sys.exit(1)

    # ── Paso 3: Limpieza y guardado ───────────────────────────────────────────
    print("\n[3/3] Limpiando texto y guardando...")
    texto = clean_text(raw)

    output_path = os.path.join(script_dir, f"{title}.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(texto)

    # Borrar el WAV temporal
    try:
        os.unlink(audio_path)
    except Exception:
        pass

    # ── Resultado ─────────────────────────────────────────────────────────────
    palabras = len(texto.split())
    print(f"\n✅ Listo — {palabras} palabras transcritas.")
    print(f"   Archivo: {output_path}\n")
    print("── Primeros 500 caracteres ──────────────────────────────────────")
    print(texto[:500])
    print("─────────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
