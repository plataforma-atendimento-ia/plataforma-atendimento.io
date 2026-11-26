"""Arquivos das conversas (imagem, vídeo, áudio, documento): validação, armazenamento e conversão de áudio.

Limites e formatos seguem o que o WhatsApp aceita. Áudio gravado no navegador (webm) é convertido para
OGG/Opus com o FFmpeg, que é o formato da mensagem de voz do WhatsApp."""
import asyncio
import glob
import os
import shutil
import subprocess
import uuid

from .config import settings

MB = 1024 * 1024
LIMITS = {"image": 5 * MB, "video": 16 * MB, "audio": 16 * MB, "document": 100 * MB}
MIMES = {
    "image": {"image/jpeg", "image/png", "image/webp"},
    "video": {"video/mp4", "video/3gpp"},  # formatos que o WhatsApp aceita
    "audio": {"audio/ogg", "audio/mpeg", "audio/mp4", "audio/aac", "audio/amr", "audio/webm", "audio/x-m4a", "audio/wav"},
    "document": {
        "application/pdf", "text/plain", "application/msword", "application/zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    },
}
EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "video/mp4": ".mp4", "video/3gpp": ".3gp",
    "video/quicktime": ".mov", "video/webm": ".webm", "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
    "audio/aac": ".aac", "audio/amr": ".amr", "audio/webm": ".webm", "audio/x-m4a": ".m4a", "audio/wav": ".wav",
    "application/pdf": ".pdf", "text/plain": ".txt",
}


class MediaError(Exception):
    pass


def base_mime(mime: str) -> str:
    return (mime or "").split(";")[0].strip().lower()


def kind_for(mime: str) -> str:
    m = base_mime(mime)
    for kind, allowed in MIMES.items():
        if m in allowed:
            return kind
    raise MediaError("tipo de arquivo não aceito pelo WhatsApp")


def check_size(kind: str, size: int) -> None:
    if size <= 0:
        raise MediaError("arquivo vazio")
    if size > LIMITS[kind]:
        raise MediaError(f"arquivo grande demais: o WhatsApp aceita até {LIMITS[kind] // MB} MB para {kind_label(kind)}")


def kind_label(kind: str) -> str:
    return {"image": "imagem", "video": "vídeo", "audio": "áudio", "document": "documento"}[kind]


def root() -> str:
    return os.path.abspath(settings.media_dir)


def save(tenant_id: str, data: bytes, mime: str, name: str | None = None) -> str:
    """Grava o arquivo e devolve o caminho relativo à pasta de mídia."""
    ext = EXT.get(base_mime(mime)) or os.path.splitext(name or "")[1][:8] or ".bin"
    rel = os.path.join(tenant_id, uuid.uuid4().hex + ext)
    full = os.path.join(root(), rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)
    return rel


def abs_path(rel: str) -> str:
    full = os.path.abspath(os.path.join(root(), rel))
    if not full.startswith(root() + os.sep):  # nunca sair da pasta de mídia
        raise MediaError("caminho inválido")
    return full


def ffmpeg_path() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    pattern = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin", "ffmpeg.exe")
    hits = glob.glob(pattern)
    return hits[0] if hits else None


def _convert(src: str, dst: str) -> None:
    exe = ffmpeg_path()
    if not exe:
        raise MediaError("FFmpeg não instalado: o áudio não pôde ser convertido para mensagem de voz")
    r = subprocess.run(
        [exe, "-y", "-loglevel", "error", "-i", src, "-vn", "-c:a", "libopus", "-b:a", "32k", "-ac", "1", "-ar", "48000", dst],
        capture_output=True, timeout=120,
    )
    if r.returncode != 0 or not os.path.exists(dst):
        raise MediaError("falha ao converter o áudio: " + r.stderr.decode(errors="ignore")[-200:])


async def to_voice(tenant_id: str, rel: str) -> tuple[str, int]:
    """Converte para OGG/Opus (mensagem de voz). Devolve (novo caminho relativo, tamanho)."""
    src = abs_path(rel)
    new_rel = os.path.join(tenant_id, uuid.uuid4().hex + ".ogg")
    dst = os.path.join(root(), new_rel)
    await asyncio.to_thread(_convert, src, dst)
    try:
        os.remove(src)
    except OSError:
        pass
    return new_rel, os.path.getsize(dst)


def store_incoming(tenant_id: str, data: bytes, mime: str, name: str | None = None, voice: bool = False) -> dict:
    """Guarda um arquivo que o CLIENTE mandou. Tipos fora da lista viram documento."""
    mime = base_mime(mime) or "application/octet-stream"
    try:
        kind = kind_for(mime)
    except MediaError:
        kind = "document"
    if len(data) > LIMITS["document"]:
        raise MediaError("arquivo recebido grande demais")
    rel = save(tenant_id, data, mime, name)
    return {"path": rel, "mime": mime, "name": (name or "")[:255] or None, "size": len(data), "kind": kind,
            "voice": bool(voice and kind == "audio")}
