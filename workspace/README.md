# workspace/

手稿数据目录，**不提交到 Git**。

- 每个页面 = 一个 ALTO `.xml` / `.alto` + 一张图片（同名，或在 `sourceImageInformation/fileName` 里指定）。
- 也可以从网页左上角 `＋` 上传，会保存到 `workspace/uploads/`。
- 识别结果在 `workspace/.results/`，缩略图 / 裁剪缓存在 `workspace/.cache/`。
- 想清空结果：删除 `.results/` 即可。
- 想生成一套示例页：在仓库根目录执行

  ```bash
  python tools/make_sample.py
  ```

  （需要 Pillow；在 Windows 或 WSL 里都可以。）
