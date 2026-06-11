#!/usr/bin/env node
/**
 * zai-mcp-server HTTP Wrapper — 端口 9530
 * 将 stdio 的 zai-mcp-server 包装为 Streamable HTTP，供所有 Crush 共享。
 * 消除每个 Crush 独立启动 zai-mcp 的冗余（10实例→1实例，省~630MB）。
 *
 * 启动: node /home/ai/lingmessage/mcp_servers/zai_mcp_http.js
 * 端点: http://127.0.0.1:9530/mcp
 */

import {
  createServer as createHttpServer,
  IncomingMessage,
  ServerResponse,
} from "http";

import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

const HOST = process.env.ZAI_MCP_HOST || "127.0.0.1";
const PORT = parseInt(process.env.ZAI_MCP_PORT || "9530", 10);
const MCP_PATH = "/mcp";

function createMcpServer() {
  const server = new McpServer(
    { name: "zai-mcp-server-proxy", version: "1.0.0" },
    { capabilities: { tools: {} } }
  );

  server.tool(
    "analyze_image",
    "Analyze an image for general-purpose understanding",
    {
      image_source: { type: "string", description: "Local path or URL" },
      prompt: { type: "string", description: "What to analyze" },
    },
    async () => ({ content: [{ type: "text", text: "stub" }] })
  );

  return server;
}

async function main() {
  const httpServer = createHttpServer(async (req, res) => {
    if (req.url?.startsWith(MCP_PATH)) {
      const mcpServer = createMcpServer();
      try {
        const transport = new StreamableHTTPServerTransport({
          sessionId: undefined,
        });
        await mcpServer.connect(transport);
        await transport.handleRequest(req, res);
      } catch (err) {
        console.error("Request error:", err);
        if (!res.headersSent) {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Internal error" }));
        }
      }
      return;
    }

    if (req.url === "/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok", port: PORT }));
      return;
    }

    res.writeHead(404);
    res.end("Not found");
  });

  httpServer.listen(PORT, HOST, () => {
    console.log(`[zai-mcp-proxy] Listening on http://${HOST}:${PORT}${MCP_PATH}`);
  });
}

main().catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});
