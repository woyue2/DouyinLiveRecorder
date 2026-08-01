# TS+MP3 双格式同时录制方案

## 一、目标

支持同一次直播同时输出 TS(视频+音频)和 MP3(纯音频)两个文件,源流只拉取一次。

## 二、配置约定

- 全局 `config.ini` 的 `视频保存格式` 或 URL 级别第 4 字段,允许用 `|` 分隔多格式
- 仅识别 `{TS, MP3}` 组合为双格式,其他写法按单格式处理(向后兼容)
- 示例:`https://live.douyin.com/696032413311,主播: WS_T3,ts|MP3`

## 三、改动清单(2 个文件)

### 文件 1: `src/recording_config.py`

新增 `parse_multiple` 类方法,解析 `|` 分隔字符串,返回去重后的 `list[RecordFormat]`;空值或全无效时返回单个默认格式。

### 文件 2: `main.py`

1. **`normalize_video_save_type`**: 改为支持 `|` 分隔,返回 `TS|MP3` 形式的字符串;单格式时退化为原行为
2. **`check_subprocess`**: 新增可选参数 `extra_working_paths: list[str] | None = None`
   - [main.py:563](file:///c:/Users/Admin1/Documents/0DouyinLiveRecorder-fix-mp3-recording-bug/main.py#L563) 录制中分段发布:对每个 extra 也调用 `publish_completed_segments`
   - [main.py:609](file:///c:/Users/Admin1/Documents/0DouyinLiveRecorder-fix-mp3-recording-bug/main.py#L609) 录制结束最终发布:对每个 extra 也调用 `publish_output_files` 并 extend 到 `published_files`
3. **`run_recording_subprocess`**: 新增可选参数 `extra_output_paths: list[str] | None = None`
   - 把每个 extra 路径在 `ffmpeg_command` 中替换为 `.part` 版本
   - 把替换后的 working_extra 列表传给 `check_subprocess`
4. **录制分支**: 在 [main.py:1474](file:///c:/Users/Admin1/Documents/0DouyinLiveRecorder-fix-mp3-recording-bug/main.py#L1474) 后做多格式解析,在 [main.py:1496](file:///c:/Users/Admin1/Documents/0DouyinLiveRecorder-fix-mp3-recording-bug/main.py#L1496) `if record_format.audio_only:` 之前插入双输出分支

### 命令构造(双输出)

```
ffmpeg -i <源> \
  -map 0:a -c:a libmp3lame -b:a 96k [-f segment ...]  anchor_xxx.mp3 \
  -map 0   -c:v copy -c:a copy     [-f segment ...]  anchor_xxx.ts
```

- **ts 在命令最后**(作为主输出,日志/路径基线基于它)
- **mp3 作为额外输出**通过 `extra_output_paths` 传入
- 分段时两边各自独立 `%03d` 段模式,各自独立 `-segment_time`/`-segment_format`

## 四、关键点

1. **路径主次关系**:`ts_path` 必须是命令最后一个参数,这样 `ffmpeg_command[-1]` 取到 ts,日志路径、`should_convert_to_mp4` 判断都基于 ts(符合"视频为主"的语义)
2. **`.part` 临时文件双路径替换**:`run_recording_subprocess` 默认只替换 `ffmpeg_command[-1]`,新逻辑需要在 list 中按值查找 extra 路径并替换。**注意路径字符串在命令中必须唯一**(不会和 `-map`、`-c:a` 等参数冲突,但要避免和源 URL 重复)
3. **`save_type="TS|MP3"` 的副作用**:
   - [main.py:549](file:///c:/Users/Admin1/Documents/0DouyinLiveRecorder-fix-mp3-recording-bug/main.py#L549) `save_type in {"TS", "FLV"}` 为 False,不触发转 MP4(符合需求)
   - [main.py:553](file:///c:/Users/Admin1/Documents/0DouyinLiveRecorder-fix-mp3-recording-bug/main.py#L553) `RecordFormat.parse("TS|MP3").audio_only` 因找不到别名回退默认 TS,`audio_only=False`(当前 `生成时间字幕文件=否`,不会触发字幕生成;即便开启,subs_file_path 会基于 ts_path,语义正确)
4. **平台回退(A 方案)**:
   - `only_flv_record`(shopee、花椒):新分支条件加 `not only_flv_record`,走原 FLV 直连分支
   - `only_audio_record`(猫耳FM、Look):回退到 M4A 单格式
   - H265 FLV 平台:回退到 TS 单格式
5. **`publish_completed_segments` / `publish_output_files` 已支持 `%03d` 模式**(见 [output_pipeline.py:11-26](file:///c:/Users/Admin1/Documents/0DouyinLiveRecorder-fix-mp3-recording-bug/src/output_pipeline.py#L11-L26)),双输出场景直接复用,无需改动

## 五、风险点

1. **ffmpeg 多输出 + segment muxer 兼容性**:理论上 ffmpeg 支持同一进程内多个 segment muxer 输出,但实际行为需实测。若两个 segment 输出在某些 ffmpeg 版本上互相干扰(如时间戳重置冲突),回退方案为**方案 C(多进程并行)**:启动两个 ffmpeg,各拉一次源,独立录制 ts 和 mp3
2. **路径字符串在 ffmpeg_command 中可能不唯一**:如果 `full_path` 路径里恰好有和 mp3_path 完全相同的字符串(几乎不可能,因为路径是 `xxx.mp3`),替换会出错。**缓解**:替换时记录已替换索引,只替换第一个匹配
3. **录制中段发布顺序**:ts 段比 mp3 段大,完成时间略晚。`publish_completed_segments` 会跳过每个 muxer 的最后一段(还在写),所以两边独立 publish 不会冲突,但**用户在录制中看到的"已发布段"ts 可能比 mp3 少 1 个**,属于正常现象
4. **失败回滚**:录制失败时([main.py:643](file:///c:/Users/Admin1/Documents/0DouyinLiveRecorder-fix-mp3-recording-bug/main.py#L643) `else` 分支),不会调用 `publish_output_files`,两个 `.part` 文件会残留在磁盘。**原单格式也有此问题**,非本次引入,但双输出残留空间翻倍。**缓解**:可在失败分支加清理逻辑(本次改动不做,保持与原行为一致)
5. **`recording_time_list` 第三字段类型变化**:原来是单格式字符串如 `"TS"`,双格式时变成 `"TS|MP3"`。需检查是否有其他地方读这个字段并按单格式解析(已知 [main.py:1908](file:///c:/Users/Admin1/Documents/0DouyinLiveRecorder-fix-mp3-recording-bug/main.py#L1908) 用作 fingerprint,无解析问题,但需全局 grep 确认)
6. **URL 级 `ts|MP3` 与全局默认的优先级**:`parse_multiple(requested_save_type, video_save_type)` 中,`requested_save_type` 是 URL 级,若为空则用 `video_save_type`(全局)。**注意**:全局若也写成 `ts|MP3`,所有未指定的 URL 都会双输出,可能不是用户意图。**建议**:全局保持单格式,仅 URL 级按需指定
7. **`-map 0` vs `-map 0:a` 顺序**:mp3 输出在前,ts 输出在后。若 ffmpeg 在解析时遇到 `-map 0:a` 后再 `-map 0`,行为是各自独立选流,无冲突。但需实测确认

## 六、不改动项

- `录制完成后自动转为mp4格式` 逻辑(用户明确不需要)
- `生成时间字幕文件` 逻辑(当前关闭,且双输出场景下语义复杂,暂不支持)
- 自定义脚本回调([main.py:621-640](file:///c:/Users/Admin1/Documents/0DouyinLiveRecorder-fix-mp3-recording-bug/main.py#L621-L640)):`script_output_path` 会取 `published_files[0]`(即 ts 文件),`script_save_type="TS|MP3"`,脚本需自行处理这种 save_type

## 七、使用示例

`URL_config.ini`:
```
https://live.douyin.com/696032413311,主播: WS_T3,ts|MP3
```

录制后在 `视频保存路径/抖音/WS_T3/<batch_time>/` 目录下生成:
```
WS_T3_2026-08-02_15-30-00.ts
WS_T3_2026-08-02_15-30-00.mp3
```

分段时:
```
WS_T3_2026-08-02_15-30-00_000.ts
WS_T3_2026-08-02_15-30-00_000.mp3
WS_T3_2026-08-02_16-00-00_001.ts
WS_T3_2026-08-02_16-00-00_001.mp3
...
```

---

风险点 1(ffmpeg 多 segment 输出兼容性)是最大不确定性,**建议改完后先用一个低码率测试直播间验证分段录制场景**。其余风险均有缓解措施。

确认开始动手即回复"开始"。