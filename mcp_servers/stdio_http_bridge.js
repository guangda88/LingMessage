#!/usr/bin/env node
/**
 * stdio-http-bridge.js — 通用 stdio→HTTP 无状态代理
 * 每次 HTTP 请求启动独立子进程，完成 initialize 握手后转发请求。
 * 避免长连接状态问题，天然多连接安全。
 *
 * 用法: node stdio-http-bridge.js [--command "npx -y @z_ai/mcp-server"] [--port 9530]
 */

import { spawn } from "child_process";
import { createServer } from "http";

const PORT = parseInt(process.env.BRIDGE_PORT || "9530", 10);
const HOST = "127.0.0.1";
const MCP_PATH = "/mcp";
const INIT_TIMEOUT = 15_000;
const CALL_TIMEOUT = 120_000;

let childCmd = "npx";
let childArgv = ["-y", "@z_ai/mcp-server"];

for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i] === "--command" && process.argv[++i]) {
    const parts = process.argv[i].split(" ");
    childCmd = parts[0];
    childArgv = parts.slice(1);
  }
  if (process.argv[i] === "--port" && process.argv[++i]) {
    process.argv[i] |> parseInt(#) |> !isNaN(#) && (PORT = #); // skip, assign below
  }
}

function waitForId(child, id, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timeout id=" + id)), ms);
    let buf = "";
    const onLine = (raw) => {
      buf += raw;
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const ln of lines) {
        const s = ln.trim();
        if (!s) continue;
        try {
          const m = JSON.parse(s);
          if (m.id === id) {
            clearTimeout(timer);
            child.stdout.off("data", onChunk);
            resolve(m);
          }
        } catch {}
      }
    };
    const onChunk = (d) => onLine(d.toString());
    child.stdout.on("data", onChunk);
  });
}

async function proxy(body) {
  const child = spawn(childCmd, childArgv, {
    env: { ...process.env },
    stdio: ["pipe", "pipe", "pipe"],
    shell: true,
  });
  child.stderr.on("data", () => {});
  try {
    child.stdin.write(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 0,
        method: "initialize",
        params: {
          protocolVersion: "2025-03-26",
          capabilities: {},
          clientInfo: { name: "bridge", version: "1.0" },
        },
      }) + "\n"
    );
    await waitForId(child, 0, INIT_TIMEOUT);
    child.stdin.write(
      JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) +
        "\n"
    );
    const rid = body.id ?? 1;
    body.id = rid;
    child.stdin.write(JSON.stringify(body) + "\n");
    return await waitForId(child, rid, CALL_TIMEOUT);
  } finally {
    child.kill("SIGTERM");
    setTimeout(() => tryKill(child), 2000);
  }
}

function tryKill(c) {
  try {
    c.kill("SIGKILL");
  } catch {}
}

const srv = createServer((req, res) => {
  if (req.method === "POST" && req.url?.startsWith(MCP_PATH)) {
    let b = "";
    req.on("data", (c) => (b += c));
    req.on("end", async () => {
      try {
        const j = JSON.parse(b);
        const out = await proxy(j);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(out));
      } catch (e) {
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32603, message: e.message } }));
      }
    });
    return;
  }
  if (req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok", port: PORT }));
    return;
  }
  res.writeHead(404);
  res.end();
});

srv.listen(PORT, HOST, () => {
  console.log(`[bridge] http://${HOST}:${PORT}${MCP_PATH} → ${childCmd} ${childArgv.join(" ")}`);
});
