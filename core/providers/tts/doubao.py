import os
import uuid
import json
import base64
import requests
from datetime import datetime
from core.providers.tts.base import TTSProviderBase


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.appid = config.get("appid")
        self.access_token = config.get("access_token")
        self.cluster = config.get("cluster")
        self.voice = config.get("voice")

        self.host = "openspeech.bytedance.com"
        self.api_url = f"https://{self.host}/api/v1/tts"
        self.header = {"Authorization": f"Bearer;{self.access_token}"}

    def generate_filename(self, extension=".wav"):
        return os.path.join(self.output_file, f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}")

    async def text_to_speak(self, text, output_file):
        request_json = {
            "app": {
                "appid": self.appid,
                "token": "access_token",
                "cluster": self.cluster
            },
            "user": {
                "uid": "1"
            },
            "audio": {
                "voice_type": self.voice,
                "encoding": "wav",
                "speed_ratio": 1.0,
                "volume_ratio": 1.0,
                "pitch_ratio": 1.0,
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": text,
                "text_type": "plain",
                "operation": "query",
                "with_frontend": 1,
                "frontend_type": "unitTson"
            }
        }

        resp = requests.post(self.api_url, json.dumps(request_json), headers=self.header)
        if "data" in resp.json():
            data = resp.json()["data"]
            file_to_save = open(output_file, "wb")
            file_to_save.write(base64.b64decode(data))
