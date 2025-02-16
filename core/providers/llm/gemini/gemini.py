import logging
import google.generativeai as genai
from core.providers.llm.base import LLMProviderBase

logger = logging.getLogger(__name__)

class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        """初始化Gemini LLM Provider"""
        self.model_name = config.get("model_name", "gemini-1.5-pro") 
        self.api_key = config.get("api_key")
        
        if not self.api_key or "你" in self.api_key:
            logger.error("你还没配置Gemini LLM的密钥，请在配置文件中配置密钥，否则无法正常工作")
            return
            
        try:
            # 初始化Gemini客户端
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(self.model_name)
            
            # 设置生成参数
            self.generation_config = {
                "temperature": 0.7,
                "top_p": 0.9,
                "top_k": 40,
                "max_output_tokens": 2048,
            }
            self.chat = None
        except Exception as e:
            logger.error(f"Gemini初始化失败: {e}")
            self.model = None

    def response(self, session_id, dialogue):
        """生成Gemini对话响应"""
        if not self.model:
            yield "【Gemini服务未正确初始化】"
            return

        try:
            # 处理对话历史
            chat_history = []
            for msg in dialogue[:-1]:  # 历史对话
                role = "model" if msg["role"] == "assistant" else "user"
                content = msg["content"].strip()
                if content:
                    chat_history.append({
                        "role": role,
                        "parts": [content]
                    })

            # 获取当前消息
            current_msg = dialogue[-1]["content"]

            # 创建新的聊天会话
            chat = self.model.start_chat(history=chat_history)
            
            # 发送消息并获取流式响应
            response = chat.send_message(
                current_msg,
                stream=True,
                generation_config=self.generation_config
            )

            # 处理流式响应
            for chunk in response:
                if hasattr(chunk, 'text') and chunk.text:
                    yield chunk.text

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Gemini响应生成错误: {error_msg}")
            
            # 针对不同错误返回友好提示
            if "Rate limit" in error_msg:
                yield "【Gemini服务请求太频繁,请稍后再试】"
            elif "Invalid API key" in error_msg:
                yield "【Gemini API key无效】"
            else:
                yield f"【Gemini服务响应异常: {error_msg}】"
