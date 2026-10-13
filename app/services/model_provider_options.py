"""Small compatibility adjustments for the built-in model presets."""
from urllib.parse import urlsplit


def model_request_options(config, *, temperature=None, connection_test=False) -> dict:
    host = (urlsplit(config.base_url).hostname or '').lower()
    model = config.model_name.lower()
    kimi = host in {'api.moonshot.cn', 'api.moonshot.ai'} and model == 'kimi-k2.6'
    options = {} if temperature is None or kimi else {'temperature': temperature}
    if not connection_test:
        return options
    # Short probes need final text within their small output budget.
    if ((host == 'api.deepseek.com' and model in {'deepseek-flash', 'deepseek-v4-pro'})
            or kimi or (host == 'open.bigmodel.cn' and model == 'glm-4.7')):
        options['extra_body'] = {'thinking': {'type': 'disabled'}}
    elif ((host == 'dashscope.aliyuncs.com' and model == 'qwen-plus')
          or (host == 'api.siliconflow.cn' and model == 'qwen/qwen3-32b')):
        options['extra_body'] = {'enable_thinking': False}
    return options
