import asyncio
import websockets
import logging
from core.connection import ConnectionHandler
from core.utils.util import get_local_ip
from core.utils import asr, vad, llm, tts


class WebSocketServer:
    def __init__(self, config: dict):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self._vad, self._asr, self._llm, self._tts = self._create_processing_instances()

    def _create_processing_instances(self):
        """创建处理模块实例"""
        return (
            vad.create_instance(
                self.config["selected_module"]["VAD"],
                self.config["VAD"][self.config["selected_module"]["VAD"]]
            ),
            asr.create_instance(
                self.config["selected_module"]["ASR"],
                self.config["ASR"][self.config["selected_module"]["ASR"]],
                self.config["delete_audio"]
            ),
            llm.create_instance(
                self.config["selected_module"]["LLM"]
                if not 'type' in self.config["LLM"][self.config["selected_module"]["LLM"]]
                else
                self.config["LLM"][self.config["selected_module"]["LLM"]]['type'],
                self.config["LLM"][self.config["selected_module"]["LLM"]],
            ),
            tts.create_instance(
                self.config["selected_module"]["TTS"]
                if not 'type' in self.config["TTS"][self.config["selected_module"]["TTS"]]
                else
                self.config["TTS"][self.config["selected_module"]["TTS"]]["type"],
                self.config["TTS"][self.config["selected_module"]["TTS"]],
                self.config["delete_audio"]
            )
        )

    async def start(self):
        server_config = self.config["server"]
        host = server_config["ip"]
        port = server_config["port"]

        self.logger.info("Server is running at ws://%s:%s", get_local_ip(), port)
        async with websockets.serve(
                self._handle_connection,
                host,
                port
        ):
            await asyncio.Future()

    async def _handle_connection(self, websocket):
        """处理新连接，每次创建独立的ConnectionHandler"""
        handler = ConnectionHandler(self.config, self._vad, self._asr, self._llm, self._tts)
        await handler.handle_connection(websocket)
