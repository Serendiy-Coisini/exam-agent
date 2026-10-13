# 工作台模型设置

打开工作台侧边栏的 **模型设置**，填写以下三项：

| 设置 | 填写内容 |
| --- | --- |
| 接口基础地址（Base URL） | 服务商提供的 OpenAI 兼容基础地址，例如 `https://api.example.com/v1`；不要追加 `/chat/completions` |
| 模型名称 | 服务商提供的准确模型 ID |
| API Key | 对应服务商的密钥 |

点击 **测试连接**，工作台向当前填写的接口发送一句简短问候，验证模型能否返回文字。测试不会保存设置，也不会发送岗位数据或画像。连接测试关闭自动重试，网络操作超时最多设置为 10 秒；认证失败、额度不足、模型不存在和网络异常会显示相应提示。

点击 **保存设置** 后，后续分析立即使用新配置，重启后仍会保留。保存表示配置已写入本机；是否能连接模型需要通过连接测试确认。已开始的单次模型调用使用其启动时的配置。

已保存的密钥不会返回浏览器。密钥框留空时保留现有密钥，填写新值则替换；更换接口地址需要重新填写密钥。选择 **清除已保存的密钥** 并保存后，分析师恢复规则分析。

配置保存在服务端项目目录的 `data/model_settings.json`，属于本机私有文件，由 Git 忽略；其中密钥以明文保存，备份时按密钥文件保管。页面不将密钥写入浏览器本地存储。该文件优先于 `.env` 中的模型配置；如果未通过界面保存过，继续使用 `.env`。需要恢复使用环境变量时，可停止服务，将该配置文件移出 `data` 目录，再重启。

当前设置适用于整个工作台。设置入口仅允许通过运行工作台的本机地址访问；开启 `AUTH_ENABLED=true` 时，还必须使用数据库中 `role=admin` 的管理员账号，普通注册账号不能修改配置。当前项目没有管理员角色分配界面，正式部署时应由维护者配置管理员。

正式咨询时，工作台会将相关问题、对话上下文及查询结果发送到配置的模型服务，用于理解问题与生成解释。岗位、分数和招录人数仍由数据库查询，模型输出应结合原始公告核验。

自动测试使用临时配置、虚构密钥和本机模拟服务，不调用真实收费模型。


## 初始配置与快捷填入

首次安装默认预填 DeepSeek：`https://api.deepseek.com`、`deepseek-flash`，密钥留空，因此填写密钥并保存前仍使用规则分析。旧版尚未配置密钥和模型的空白 OpenAI 默认表单也会显示 DeepSeek；已有保存配置或自定义配置继续保留。

页面提供 DeepSeek、通义千问（北京）、智谱 GLM、Kimi（国内）和硅基流动快捷按钮。只修改表单草稿，不自动保存或发送请求；地址和模型仍可手动修改。切换地址清空输入框中的草稿密钥，并提示填写对应服务商密钥；服务器继续阻止旧密钥用于新地址。

| 服务 | 基础地址 | 预填模型 |
| --- | --- | --- |
| DeepSeek | https://api.deepseek.com | deepseek-flash |
| 通义千问 | https://dashscope.aliyuncs.com/compatible-mode/v1 | qwen-plus |
| 智谱 GLM | https://open.bigmodel.cn/api/paas/v4 | glm-4.7 |
| Kimi | https://api.moonshot.cn/v1 | kimi-k2.6 |
| 硅基流动 | https://api.siliconflow.cn/v1 | Qwen/Qwen3-32B |

这些快捷项采用已核对的官方接口示例，供快速开始使用，不代表自动检测模型权限或保证账户余额。可按服务商后台可用模型修改名称。连接测试对上述预填模型关闭思考，只验证简短文字响应，正式分析保留服务商默认思考行为；Kimi K2.6 请求不发送不支持的 temperature 参数。自定义地址不会套用官方地址的专用参数。

官方参考：[DeepSeek](https://api-docs.deepseek.com/)、[通义千问](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions)、[智谱 GLM](https://docs.bigmodel.cn/cn/guide/models/text/glm-4.7)、[Kimi](https://platform.kimi.com/docs/guide/use-thinking-models)、[硅基流动](https://docs.siliconflow.cn/docs/usercases/use-siliconcloud-in-translate-js)。
