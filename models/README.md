# models/

模型权重**不提交到 Git**。这个目录只保留说明文件。

- 默认模型：`models/Medusa0.2Line-4B/`（可以在网页里点 **模型** 下载，或手动放入）
- 可选模型：`models/Medusa0.2Line-9B/`（网页下载，约 18.8 GB）
- `models/.active` 记录当前使用的模型，同样被忽略。

下载源默认 `https://hf-mirror.com`，可通过 `MEDUSA_HF_ENDPOINT` 切换。

一个可用的模型目录至少需要：

```
config.json
model.safetensors        # 或分片 + safetensors.index.json
tokenizer.json
tokenizer_config.json
processor_config.json
chat_template.jinja
```
