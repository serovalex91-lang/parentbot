"""Транскрибация голосовых сообщений через Deepgram API."""

import aiohttp
from loguru import logger


async def transcribe_voice(audio_bytes: bytes, api_key: str) -> str | None:
    """Отправляет аудио в Deepgram, возвращает текст или None при ошибке."""
    url = "https://api.deepgram.com/v1/listen"
    params = {
        "model": "nova-2",
        "language": "ru",
        "smart_format": "true",
    }
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/ogg",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, params=params, headers=headers, data=audio_bytes, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("Deepgram HTTP {}: {}", resp.status, body[:200])
                    return None
                data = await resp.json()
                transcript = (
                    data.get("results", {})
                    .get("channels", [{}])[0]
                    .get("alternatives", [{}])[0]
                    .get("transcript", "")
                )
                if not transcript.strip():
                    logger.warning("Deepgram вернул пустую транскрипцию")
                    return None
                return transcript.strip()
    except Exception as e:
        logger.error("Ошибка транскрибации: {}", e)
        return None
