import os
import time
import yaml
import logging
from typing import Dict, Any
from copy import deepcopy
from core.utils.util import get_project_dir
from core.utils import asr, vad, llm, tts

class PrivateConfig:
    def __init__(self, device_id: str, default_config: Dict[str, Any]):
        self.device_id = device_id
        self.default_config = default_config
        self.config_path = get_project_dir() + 'data/.private_config.yaml'
        self.logger = logging.getLogger(__name__)
        self.private_config = {}

    async def load_or_create(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    all_configs = yaml.safe_load(f) or {}
            else:
                all_configs = {}

            if self.device_id not in all_configs:
                # Get selected module names
                selected_modules = self.default_config['selected_module']
                selected_tts = selected_modules['TTS']
                selected_llm = selected_modules['LLM']
                selected_asr = selected_modules['ASR']
                selected_vad = selected_modules['VAD']

                # Initialize device config with only necessary configurations
                device_config = {
                    'selected_module': deepcopy(selected_modules),
                    'prompt': self.default_config['prompt'],
                    'LLM': {
                        selected_llm: deepcopy(self.default_config['LLM'][selected_llm])
                    },
                    'TTS': {
                        selected_tts: deepcopy(self.default_config['TTS'][selected_tts])
                    },
                    'ASR': {
                        selected_asr: deepcopy(self.default_config['ASR'][selected_asr])
                    },
                    'VAD': {
                        selected_vad: deepcopy(self.default_config['VAD'][selected_vad])
                    }
                }
                
                all_configs[self.device_id] = device_config
                
                # Save updated configs
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(all_configs, f, allow_unicode=True)

            self.private_config = all_configs[self.device_id]

        except Exception as e:
            self.logger.error(f"Error handling private config: {e}")
            self.private_config = {}

    async def update_config(self, selected_modules: Dict[str, str], prompt: str, nickname: str) -> bool:
        """更新设备配置
        Args:
            selected_modules: 选择的模块配置，格式如 {'LLM': 'AliLLM', 'TTS': 'EdgeTTS',...}
            prompt: 提示词配置
        Returns:
            bool: 更新是否成功
        """
        try:
            # Read main config to get full module configurations
            main_config = self.default_config

            # Create new device config
            device_config = {
                'selected_module': selected_modules,
                'prompt': prompt,
                'nickname': nickname,
            }
            if self.private_config.get('last_chat_time'):
                device_config['last_chat_time'] = self.private_config['last_chat_time']

            # Copy full module configurations from main config
            for module_type, selected_name in selected_modules.items():
                if selected_name and selected_name in main_config.get(module_type, {}):
                    device_config[module_type] = {
                        selected_name: main_config[module_type][selected_name]
                    }

            # Read all configs
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    all_configs = yaml.safe_load(f) or {}
            else:
                all_configs = {}

            # Update device config
            all_configs[self.device_id] = device_config
            self.private_config = device_config

            # Save back to file
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(all_configs, f, allow_unicode=True)

            return True

        except Exception as e:
            self.logger.error(f"Error updating config: {e}")
            return False

    async def delete_config(self) -> bool:
        """删除设备配置
        Returns:
            bool: 删除是否成功
        """
        try:
            # 读取所有配置
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    all_configs = yaml.safe_load(f) or {}
            else:
                return False

            # 删除设备配置
            if self.device_id in all_configs:
                del all_configs[self.device_id]
                
                # 保存更新后的配置
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(all_configs, f, allow_unicode=True)
                
                self.private_config = {}
                return True
            
            return False

        except Exception as e:
            self.logger.error(f"Error deleting config: {e}")
            return False

    def create_private_instances(self):
        #  判断存在私有配置，并且self.device_id在私有配置中
        if not self.private_config:
            logging.error("Private config not found for device_id: %s", self.device_id)
            return None, None, None, None
        
        """创建私有处理模块实例"""
        config = self.private_config
        selected_modules = config['selected_module']
        return (
            vad.create_instance(
                selected_modules["VAD"],
                config["VAD"][selected_modules["VAD"]]
            ),
            asr.create_instance(
                selected_modules["ASR"],
                config["ASR"][selected_modules["ASR"]],
                self.default_config.get("delete_audio", True)  # Using default_config for global settings
            ),
            llm.create_instance(
                selected_modules["LLM"]
                if not 'type' in config["LLM"][selected_modules["LLM"]]
                else
                config["LLM"][selected_modules["LLM"]]['type'],
                config["LLM"][selected_modules["LLM"]],
            ),
            tts.create_instance(
                selected_modules["TTS"]
                if not 'type' in config["TTS"][selected_modules["TTS"]]
                else
                config["TTS"][selected_modules["TTS"]]["type"],
                config["TTS"][selected_modules["TTS"]],
                self.default_config.get("delete_audio", True)  # Using default_config for global settings
            )
        )

    def update_last_chat_time(self, timestamp=None):
        """更新设备最近一次的聊天时间
        Args:
            timestamp: 指定的时间戳,不传则使用当前时间
        """
        if not self.private_config:
            self.logger.error("Private config not found")
            return False
            
        try:
            if timestamp is None:
                timestamp = int(time.time())
                
            self.private_config['last_chat_time'] = timestamp
            
            # 读取所有配置
            with open(self.config_path, 'r', encoding='utf-8') as f:
                all_configs = yaml.safe_load(f) or {}
                
            # 更新当前设备配置    
            all_configs[self.device_id] = self.private_config
            
            # 保存回文件
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(all_configs, f, allow_unicode=True)
                
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating last chat time: {e}")
            return False