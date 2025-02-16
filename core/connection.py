import os
import json
import uuid
import time
import queue
import asyncio
import logging
import threading
import websockets
from typing import Dict, Any
from collections import deque
from core.utils.util import is_segment
from core.utils.dialogue import Message, Dialogue
from core.handle.textHandle import handleTextMessage
from core.utils.util import get_string_no_punctuation_or_emoji
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from core.handle.audioHandle import handleAudioMessage, sendAudioMessage
from config.private_config import PrivateConfig
from core.auth import AuthMiddleware, AuthenticationError

class ConnectionHandler:
    def __init__(self, config: Dict[str, Any], _vad, _asr, _llm, _tts):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.auth = AuthMiddleware(config)

        self.websocket = None
        self.headers = None
        self.session_id = None
        self.prompt = None
        self.welcome_msg = None

        # 客户端状态相关
        self.client_abort = False
        self.client_listen_mode = "auto"

        # 线程任务相关
        self.loop = asyncio.get_event_loop()
        self.stop_event = threading.Event()
        self.tts_queue = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.scheduled_tasks = deque()

        # 依赖的组件
        self.vad = _vad
        self.asr = _asr
        self.llm = _llm
        self.tts = _tts
        self.dialogue = None

        # vad相关变量
        self.client_audio_buffer = bytes()
        self.client_have_voice = False
        self.client_have_voice_last_time = 0.0
        self.client_voice_stop = False

        # asr相关变量
        self.asr_audio = []
        self.asr_server_receive = True

        # llm相关变量
        self.llm_finish_task = False
        self.dialogue = Dialogue()

        # tts相关变量
        self.tts_first_text = None
        self.tts_last_text = None
        self.tts_start_speak_time = None
        self.tts_duration = 0

        self.cmd_exit = self.config["CMD_exit"]
        self.max_cmd_length = 0
        for cmd in self.cmd_exit:
            if len(cmd) > self.max_cmd_length:
                self.max_cmd_length = len(cmd)
        
        self.private_config = None

    async def handle_connection(self, ws):
        try:
            # 获取并验证headers
            self.headers = dict(ws.request.headers)
            self.logger.info(f"New connection request - Headers: {self.headers}")

            # 进行认证
            await self.auth.authenticate(self.headers)

            device_id = self.headers.get("device-id", None)
            
            # Load private configuration if device_id is provided
            bUsePrivateConfig = self.config.get("use_private_config", False)
            logging.info(f"bUsePrivateConfig: {bUsePrivateConfig}, device_id: {device_id}")
            if bUsePrivateConfig and device_id:
                self.private_config = PrivateConfig(device_id, self.config)
                await self.private_config.load_or_create()
                # Create private instances using private config
                vad, asr, llm, tts = self.private_config.create_private_instances()
                if vad is not None and asr is not None and llm is not None and tts is not None:
                    self.vad = vad
                    self.asr = asr
                    self.llm = llm
                    self.tts = tts

                    self.logger.info(f"Loaded private config and instances for device {device_id}")
                    self.private_config.update_last_chat_time()
                else:
                    self.logger.error(f"Failed to load private config for device {device_id}")
                    self.private_config = None

            # 认证通过,继续处理
            self.websocket = ws
            self.session_id = str(uuid.uuid4())

            self.welcome_msg = self.config["xiaozhi"]
            self.welcome_msg["session_id"] = self.session_id
            await self.websocket.send(json.dumps(self.welcome_msg))

            await self.loop.run_in_executor(None, self._initialize_components)

            tts_priority = threading.Thread(target=self._priority_thread, daemon=True)
            tts_priority.start()

            try:
                async for message in self.websocket:
                    await self._route_message(message)
            except websockets.exceptions.ConnectionClosed:
                self.logger.info("客户端断开连接")
                await self.close()

        except AuthenticationError as e:
            self.logger.error(f"Authentication failed: {str(e)}")
            await ws.close()
            return
        except Exception as e:
            self.logger.error(f"Connection error: {str(e)}")
            await ws.close()
            return

    async def _route_message(self, message):
        """消息路由"""
        if isinstance(message, str):
            await handleTextMessage(self, message)
        elif isinstance(message, bytes):
            await handleAudioMessage(self, message)

    def _initialize_components(self):
        self.prompt = self.config["prompt"]
        if self.private_config:
            self.prompt = self.private_config.private_config.get("prompt", self.prompt)
        # 赋予LLM时间观念
        if "{date_time}" in self.prompt:
            date_time = time.strftime("%Y-%m-%d %H:%M", time.localtime())
            self.prompt = self.prompt.replace("{date_time}", date_time)
        self.dialogue.put(Message(role="system", content=self.prompt))

    def chat(self, query):
        self.dialogue.put(Message(role="user", content=query))
        response_message = []
        start = 0
        # 提交 LLM 任务
        try:
            start_time = time.time()  # 记录开始时间
            llm_responses = self.llm.response(self.session_id, self.dialogue.get_llm_dialogue())
        except Exception as e:
            self.logger.error(f"LLM 处理出错 {query}: {e}")
            return None
        # 提交 TTS 任务到线程池
        self.llm_finish_task = False
        for content in llm_responses:
            response_message.append(content)
            # 如果中途被打断，就停止生成
            if self.client_abort:
                start = len(response_message)
                break

            end_time = time.time()  # 记录结束时间
            self.logger.debug(f"大模型返回时间时间: {end_time - start_time} 秒, 生成token={content}")
            if is_segment(response_message):
                segment_text = "".join(response_message[start:])
                segment_text = get_string_no_punctuation_or_emoji(segment_text)
                if len(segment_text) > 0:
                    self.recode_first_last_text(segment_text)
                    future = self.executor.submit(self.speak_and_play, segment_text)
                    self.tts_queue.put(future)
                    start = len(response_message)

        # 处理剩余的响应
        if start < len(response_message):
            segment_text = "".join(response_message[start:])
            if len(segment_text) > 0:
                self.recode_first_last_text(segment_text)
                future = self.executor.submit(self.speak_and_play, segment_text)
                self.tts_queue.put(future)

        self.llm_finish_task = True
        # 更新对话
        self.dialogue.put(Message(role="assistant", content="".join(response_message)))
        self.logger.debug(json.dumps(self.dialogue.get_llm_dialogue(), indent=4, ensure_ascii=False))
        return True

    def _priority_thread(self):
        while not self.stop_event.is_set():
            text = None
            try:
                future = self.tts_queue.get()
                if future is None:
                    continue
                text = None
                try:
                    self.logger.debug("正在处理TTS任务...")
                    tts_file, text = future.result(timeout=10)
                    if text is None or len(text) <= 0:
                        continue
                    if tts_file is None:
                        self.logger.error(f"TTS文件生成失败: {text}")
                        continue
                    self.logger.debug(f"TTS文件生成完毕，文件路径: {tts_file}")
                    if os.path.exists(tts_file):
                        opus_datas, duration = self.tts.wav_to_opus_data(tts_file)
                    else:
                        self.logger.error(f"TTS文件不存在: {tts_file}")
                        opus_datas = []
                        duration = 0
                except TimeoutError:
                    self.logger.error("TTS 任务超时")
                    continue
                except Exception as e:
                    self.logger.error(f"TTS 任务出错: {e}")
                    continue
                if not self.client_abort:
                    # 如果没有中途打断就发送语音
                    asyncio.run_coroutine_threadsafe(
                        sendAudioMessage(self, opus_datas, duration, text), self.loop
                    )
                if self.tts.delete_audio_file and os.path.exists(tts_file):
                    os.remove(tts_file)
            except Exception as e:
                self.logger.error(f"TTS任务处理错误: {e}")
                self.clearSpeakStatus()
                asyncio.run_coroutine_threadsafe(
                    self.websocket.send(json.dumps({"type": "tts", "state": "stop", "session_id": self.session_id})),
                    self.loop
                )
                self.logger.error(f"tts_priority priority_thread: {text}{e}")

    def speak_and_play(self, text):
        if text is None or len(text) <= 0:
            self.logger.info(f"无需tts转换，query为空，{text}")
            return None, text
        tts_file = self.tts.to_tts(text)
        if tts_file is None:
            self.logger.error(f"tts转换失败，{text}")
            return None, text
        self.logger.debug(f"TTS 文件生成完毕: {tts_file}")
        return tts_file, text

    def clearSpeakStatus(self):
        self.logger.debug(f"清除服务端讲话状态")
        self.asr_server_receive = True
        self.tts_last_text = None
        self.tts_first_text = None
        self.tts_duration = 0
        self.tts_start_speak_time = None

    def recode_first_last_text(self, text):
        if not self.tts_first_text:
            self.logger.info(f"大模型说出第一句话: {text}")
            self.tts_first_text = text
        self.tts_last_text = text

    async def close(self):
        """资源清理方法"""
        self.stop_event.set()
        self.executor.shutdown(wait=False)
        if self.websocket:
            await self.websocket.close()
        self.logger.info("连接资源已释放")

    def reset_vad_states(self):
        self.client_audio_buffer = bytes()
        self.client_have_voice = False
        self.client_have_voice_last_time = 0
        self.client_voice_stop = False
        self.logger.debug("VAD states reset.")

    def stop_all_tasks(self):
        while self.scheduled_tasks:
            task = self.scheduled_tasks.popleft()
            task.cancel()
        self.scheduled_tasks.clear()