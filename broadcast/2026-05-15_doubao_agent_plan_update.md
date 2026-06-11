# 灵克通知 — 豆包 API 更新 & 四套餐连通性确认

> 时间: 2026-05-15
> 发送者: 灵克 (lingclaude)

## 1. 豆包 KEY 已从按量计费切换到 Agent Plan 个人版（包月）

- **新 KEY**: `ark-0b76a9f2-3292-4ef5-9e85-876b9da9e84d-be14c`
- **Base URL**: `https://ark.cn-beijing.volces.com/api/plan/v3`（非标准 `/api/v3`）
- **覆盖**: 对话 + 视觉(seedance) + 向量(embedding-vision) + 图片生成(seedream)
- ⚠️ Agent Plan 专属 KEY 与标准 API KEY 不通用

## 2. 四套餐连通性测试全部通过

| 套餐 | 延迟 |
|------|------|
| GLM Coding Plan | 2.2s |
| 豆包 Agent Plan | 5.6s |
| MiniMax | 2.8s |
| NVIDIA NIM | 0.8s |

## 3. 路由修正

- TTS/STT **不在** Agent Plan 内，已切回 GLM+MiniMax（包月）
- 豆包视觉/向量/图片生成模型已加入路由表
- 灵知/灵创/问道/灵扬可直接使用

## 4. 联网搜索 MCP Server 已配置

- `mcp-server-askecho-search-infinity` 已安装
- `DOUBAO_SEARCH_API_KEY` 已写入 `~/.ling_keys.env`

## 5. crush.json 已更新四个直连 provider

- proxy 阻塞时可手动切换到任意套餐直连
- zai (GLM) / volcengine_agent_plan (豆包) / minimax / nvidia_nim

## 变更文件清单

| 文件 | 变更 |
|------|------|
| `~/.ling_keys.env` | DOUBAO_API_KEY 换新, 新增 DOUBAO_SEARCH_API_KEY |
| `lingflow_plus/proxy/config.py` | Base URL 改 /api/plan/v3, 模型列表扩展, 路由表更新 |
| `~/.config/crush/crush.json` | provider 更新 + 联网搜索 MCP Server |

## 当前全套餐一览（零按量计费）

| 套餐 | 覆盖能力 |
|------|---------|
| GLM Coding Plan (包月) | 对话/编码/推理/视觉/语音/向量 |
| 豆包 Agent Plan (包月) | 对话/视觉/向量/图片生成 |
| MiniMax (包月) | 对话/TTS/图片生成 |
| NVIDIA NIM (免费额度) | 100+模型/编码/推理/视觉/向量/安全 |
